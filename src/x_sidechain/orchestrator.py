from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from x_sidechain.audit import AuditLog
from x_sidechain.models import AgentSpec, DiscussionEvent, DiscussionResult, ModelReply
from x_sidechain.prompts import (
    chair_draft_prompt,
    chair_questions_prompt,
    clarification_prompt,
    final_prompt,
    private_analysis_prompt,
    public_summary_prompt,
    review_prompt,
)
from x_sidechain.providers.base import Provider
from x_sidechain.workspace import SessionWorkspace, validate_agent_id

ProgressCallback = Callable[[str], None]
EventCallback = Callable[[DiscussionEvent], None]


@dataclass(frozen=True)
class AgentRuntime:
    spec: AgentSpec
    provider: Provider


@dataclass(frozen=True)
class AgentBrief:
    analysis: ModelReply
    summary: ModelReply


@dataclass(frozen=True)
class ChairQuestion:
    agent_id: str
    question: str


class RevisionChanged(RuntimeError):
    pass


class BudgetExhausted(RuntimeError):
    """The model-call budget is spent. Never degraded to an agent abstention."""


class DeadlineReached(RuntimeError):
    """The session wall-clock deadline passed. No further cycle may start."""


def truncate_brief(text: str, limit: int) -> str:
    """Cut a public brief to the configured limit, preferring a line boundary."""
    marker = "\n[brief truncated by x-sidechain]"
    budget = max(1, limit - len(marker))
    head = text[:budget]
    boundary = head.rfind("\n")
    if boundary > budget // 2:
        head = head[:boundary]
    return head.rstrip() + marker


class SidechainOrchestrator:
    """Moderated team protocol with private workspaces and bounded public events."""

    def __init__(
        self,
        agents: tuple[AgentRuntime, ...],
        chair_id: str,
        max_clarification_questions: int = 4,
        max_model_calls: int | None = None,
        min_agent_quorum: int = 2,
        max_revisions: int = 8,
        session_deadline_seconds: int | None = None,
        max_public_brief_chars: int = 1200,
        audit_directory: Path | None = None,
        progress: ProgressCallback | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        if len(agents) < 2:
            raise ValueError("at least two agents are required")
        ids = [runtime.spec.id for runtime in agents]
        for agent_id in ids:
            validate_agent_id(agent_id)
        if len(ids) != len(set(ids)):
            raise ValueError("agent ids must be unique")
        if chair_id not in ids:
            raise ValueError("chair_id must reference an agent")
        if max_clarification_questions < 0 or max_clarification_questions > 20:
            raise ValueError("max_clarification_questions must be between 0 and 20")
        maximum_questions = min(max_clarification_questions, len(agents))
        minimum_calls = 3 * len(agents) + maximum_questions + 2
        call_budget = max_model_calls if max_model_calls is not None else minimum_calls * 3
        if call_budget < minimum_calls:
            raise ValueError(
                f"max_model_calls must be at least {minimum_calls} for this chaired workflow"
            )
        if min_agent_quorum < 2 or min_agent_quorum > len(agents):
            raise ValueError("min_agent_quorum must be between 2 and the agent count")
        if max_revisions < 0 or max_revisions > 100:
            raise ValueError("max_revisions must be between 0 and 100")
        if max_public_brief_chars < 200 or max_public_brief_chars > 20_000:
            raise ValueError("max_public_brief_chars must be between 200 and 20000")
        if session_deadline_seconds is not None and session_deadline_seconds < 1:
            raise ValueError("session_deadline_seconds must be positive")

        self.agents = agents
        self.chair_id = chair_id
        self.max_clarification_questions = max_clarification_questions
        self.max_model_calls = call_budget
        self.cycle_cost = minimum_calls
        self.min_agent_quorum = min_agent_quorum
        self.max_revisions = max_revisions
        self.session_deadline_seconds = session_deadline_seconds
        self.max_public_brief_chars = max_public_brief_chars
        self.audit_directory = audit_directory
        self.progress = progress or (lambda _message: None)
        self.on_event = on_event or (lambda _event: None)

        self._condition = threading.Condition()
        self._active = False
        self._active_ready = threading.Event()
        self._accepting_input = False
        self._revision = 0
        self._events: list[DiscussionEvent] = []
        self._steering: list[str] = []
        self._audit: AuditLog | None = None
        self._model_calls = 0
        self._deadline: float | None = None
        self._abstentions: dict[str, str] = {}

    def wait_until_active(self, timeout: float | None = None) -> bool:
        return self._active_ready.wait(timeout)

    def _append_locked(
        self,
        revision: int,
        kind: str,
        author: str,
        content: str,
        based_on_sequence: int | None,
        reply: ModelReply | None = None,
    ) -> DiscussionEvent:
        event = DiscussionEvent(
            sequence=len(self._events),
            revision=revision,
            kind=kind,
            author=author,
            content=content,
            based_on_sequence=based_on_sequence,
            reply=reply,
        )
        self._events.append(event)
        return event

    def inject_user_message(self, message: str) -> DiscussionEvent:
        message = message.strip()
        if not message:
            raise ValueError("steering message cannot be empty")
        with self._condition:
            if not self._active or self._audit is None:
                raise RuntimeError("no chaired session is active")
            if not self._accepting_input:
                raise RuntimeError("interactive input has been closed for this session")
            if self._revision >= self.max_revisions:
                raise RuntimeError(
                    f"revision limit reached ({self.max_revisions}); each correction "
                    "restarts the chaired cycle, so further ones are refused"
                )
            if self._deadline is not None and time.monotonic() >= self._deadline:
                raise RuntimeError(
                    "session deadline passed; the running cycle will finish with the "
                    "updates it already has"
                )
            remaining = self.max_model_calls - self._model_calls
            if remaining < self.cycle_cost:
                raise RuntimeError(
                    f"only {remaining} model calls remain and a restarted cycle needs "
                    f"{self.cycle_cost}; this correction is refused so the running cycle "
                    "can still produce a result"
                )
            previous = self._events[-1].sequence
            self._revision += 1
            self._steering.append(message)
            event = self._append_locked(
                self._revision,
                "user.steering",
                "user",
                message,
                previous,
            )
            self._audit.append("session.user_steering", event.public_dict())
            self._condition.notify_all()
        self.on_event(event)
        return event

    def request_finish(self) -> None:
        """Close interactive input while allowing the chaired workflow to finish."""
        with self._condition:
            if not self._active:
                raise RuntimeError("no chaired session is active")
            self._accepting_input = False
            self._condition.notify_all()

    def _snapshot(self) -> tuple[int, tuple[str, ...]]:
        with self._condition:
            return self._revision, tuple(self._steering)

    def _ensure_revision(self, revision: int, phase: str) -> None:
        with self._condition:
            if revision != self._revision:
                assert self._audit is not None
                self._audit.append(
                    "cycle.superseded",
                    {
                        "phase": phase,
                        "old_revision": revision,
                        "new_revision": self._revision,
                    },
                )
                raise RevisionChanged(phase)

    def _reserve_model_call(self) -> None:
        with self._condition:
            if self._model_calls >= self.max_model_calls:
                raise BudgetExhausted(
                    f"model-call budget exhausted ({self.max_model_calls}); "
                    "increase max_model_calls deliberately"
                )
            self._model_calls += 1

    def _generate(self, runtime: AgentRuntime, prompt: str) -> ModelReply:
        self._reserve_model_call()
        return runtime.provider.generate(runtime.spec.role, prompt)

    def _settle(
        self,
        work: Callable[[AgentRuntime], object],
        agents=None,
    ) -> tuple[dict[str, object], dict[str, str]]:
        """Run work per agent, returning what completed and why the rest did not.

        One provider failing is that agent abstaining, not the end of a session the
        others already paid for. Budget, deadline and revision errors are control
        flow rather than agent behaviour, so they propagate instead.
        """
        selected = tuple(self.agents if agents is None else agents)
        if not selected:
            return {}, {}
        results: dict[str, object] = {}
        failures: dict[str, str] = {}
        control: BaseException | None = None
        with ThreadPoolExecutor(max_workers=len(selected), thread_name_prefix="xsidechain-team") as pool:
            futures = {runtime.spec.id: pool.submit(work, runtime) for runtime in selected}
            for runtime in selected:
                agent_id = runtime.spec.id
                try:
                    results[agent_id] = futures[agent_id].result()
                except (BudgetExhausted, DeadlineReached, RevisionChanged) as exc:
                    control = control or exc
                except Exception as exc:  # noqa: BLE001 - any provider failure is an abstention
                    failures[agent_id] = f"{type(exc).__name__}: {exc}"
        if control is not None:
            raise control
        return results, failures

    def _publish_abstentions(
        self,
        revision: int,
        phase: str,
        failures: dict[str, str],
        audit: AuditLog,
    ) -> None:
        if not failures:
            return
        self.progress(
            f"Revision {revision}: {len(failures)} agent(s) abstained during {phase}"
        )
        audit.append(
            "agent.abstained",
            {"revision": revision, "phase": phase, "failures": dict(failures)},
        )
        self._publish_batch(
            revision,
            "agent.abstention",
            [
                (agent_id, f"ABSTAINED during {phase}: {reason}", None)
                for agent_id, reason in failures.items()
            ],
        )

    def _publish_batch(
        self,
        revision: int,
        kind: str,
        entries: list[tuple[str, str, ModelReply | None]],
    ) -> list[DiscussionEvent]:
        with self._condition:
            if revision != self._revision:
                assert self._audit is not None
                self._audit.append(
                    "cycle.superseded",
                    {
                        "phase": kind,
                        "old_revision": revision,
                        "new_revision": self._revision,
                    },
                )
                raise RevisionChanged(kind)
            based_on = self._events[-1].sequence
            published = [
                self._append_locked(revision, kind, author, content, based_on, reply)
                for author, content, reply in entries
            ]
            assert self._audit is not None
            for event in published:
                self._audit.append(f"room.{kind}", event.public_dict())
        for event in published:
            self.on_event(event)
        return published

    @staticmethod
    def _parse_questions(text: str, valid_ids: set[str], limit: int) -> tuple[ChairQuestion, ...]:
        candidate = text.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            candidate = "\n".join(lines[1:-1]).strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].lstrip()
        try:
            raw = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise RuntimeError("chair returned invalid clarification JSON") from exc
        questions = raw.get("questions") if isinstance(raw, dict) else None
        if not isinstance(questions, list):
            raise RuntimeError("chair clarification output must contain a questions array")
        parsed: list[ChairQuestion] = []
        seen: set[str] = set()
        for item in questions:
            if not isinstance(item, dict):
                raise RuntimeError("every chair question must be an object")
            agent_id = item.get("agent_id")
            question = item.get("question")
            if agent_id not in valid_ids or not isinstance(question, str) or not question.strip():
                raise RuntimeError("chair question has an invalid agent id or empty question")
            if agent_id in seen:
                raise RuntimeError("chair may ask at most one question per agent")
            seen.add(agent_id)
            parsed.append(ChairQuestion(agent_id, question.strip()))
        if len(parsed) > limit:
            raise RuntimeError(f"chair exceeded the clarification limit of {limit}")
        return tuple(parsed)

    def _run_cycle(
        self,
        task: str,
        revision: int,
        steering: tuple[str, ...],
        workspace: SessionWorkspace,
        audit: AuditLog,
    ) -> ModelReply:
        self.progress(f"Revision {revision}: agents working privately")

        def build_brief(runtime: AgentRuntime) -> AgentBrief:
            analysis = self._generate(
                runtime,
                private_analysis_prompt(task, steering, runtime.spec.id),
            )
            workspace.write(revision, runtime.spec.id, "analysis.md", analysis.text)
            summary = self._generate(
                runtime,
                public_summary_prompt(
                    task, steering, analysis.text, self.max_public_brief_chars
                ),
            )
            if len(summary.text) > self.max_public_brief_chars:
                # The prompt asks for a bounded brief; the limit is enforced here so a
                # long reply cannot quietly become the room's whole context. The full
                # text stays in the agent's own workspace.
                workspace.write(revision, runtime.spec.id, "summary.full.md", summary.text)
                audit.append(
                    "workspace.brief_truncated",
                    {
                        "revision": revision,
                        "agent_id": runtime.spec.id,
                        "original_chars": len(summary.text),
                        "limit": self.max_public_brief_chars,
                    },
                )
                summary = replace(
                    summary,
                    text=truncate_brief(summary.text, self.max_public_brief_chars),
                )
            workspace.write(revision, runtime.spec.id, "summary.md", summary.text)
            audit.append(
                "workspace.agent_brief",
                {
                    "revision": revision,
                    "agent_id": runtime.spec.id,
                    "analysis": analysis.public_dict(),
                    "summary": summary.public_dict(),
                },
            )
            return AgentBrief(analysis, summary)

        briefs, brief_failures = self._settle(build_brief)
        self._ensure_revision(revision, "private_analysis")
        typed_briefs = {agent_id: value for agent_id, value in briefs.items() if isinstance(value, AgentBrief)}
        abstentions: dict[str, str] = dict(brief_failures)
        self._publish_abstentions(revision, "private analysis", brief_failures, audit)
        if self.chair_id not in typed_briefs:
            raise RuntimeError(
                f"chair {self.chair_id} produced no brief "
                f"({brief_failures.get(self.chair_id, 'unknown error')}); a chaired "
                "workflow cannot continue without its chair"
            )
        if len(typed_briefs) < self.min_agent_quorum:
            reasons = "; ".join(f"{name}: {why}" for name, why in brief_failures.items())
            raise RuntimeError(
                f"only {len(typed_briefs)} of {len(self.agents)} agents produced a brief, "
                f"below min_agent_quorum={self.min_agent_quorum} ({reasons})"
            )
        self._publish_batch(
            revision,
            "agent.summary",
            [
                (agent_id, brief.summary.text, brief.summary)
                for agent_id, brief in typed_briefs.items()
            ],
        )
        summaries = {agent_id: brief.summary.text for agent_id, brief in typed_briefs.items()}

        chair = next(runtime for runtime in self.agents if runtime.spec.id == self.chair_id)
        self.progress(f"Revision {revision}: chair reading summaries")
        question_reply = self._generate(
            chair,
            chair_questions_prompt(
                task,
                steering,
                summaries,
                self.max_clarification_questions,
            ),
        )
        workspace.write(revision, self.chair_id, "chair/questions.json", question_reply.text)
        self._ensure_revision(revision, "chair_questions")
        questions = self._parse_questions(
            question_reply.text,
            set(summaries),
            self.max_clarification_questions,
        )
        self._publish_batch(
            revision,
            "chair.question",
            [(self.chair_id, f"TO {question.agent_id}: {question.question}", None) for question in questions],
        )

        clarifications: dict[str, str] = {}
        if questions:
            self.progress(f"Revision {revision}: {len(questions)} targeted clarifications")
            question_by_agent = {question.agent_id: question.question for question in questions}
            selected = [runtime for runtime in self.agents if runtime.spec.id in question_by_agent]

            def answer_question(runtime: AgentRuntime) -> ModelReply:
                agent_id = runtime.spec.id
                brief = typed_briefs[agent_id]
                reply = self._generate(
                    runtime,
                    clarification_prompt(
                        task,
                        steering,
                        brief.analysis.text,
                        brief.summary.text,
                        question_by_agent[agent_id],
                    ),
                )
                workspace.write(revision, agent_id, "clarification.md", reply.text)
                return reply

            answers, answer_failures = self._settle(answer_question, selected)
            self._ensure_revision(revision, "clarifications")
            abstentions.update(answer_failures)
            self._publish_abstentions(revision, "clarification", answer_failures, audit)
            clarification_replies = {
                agent_id: reply for agent_id, reply in answers.items() if isinstance(reply, ModelReply)
            }
            self._publish_batch(
                revision,
                "agent.clarification",
                [
                    (agent_id, reply.text, reply)
                    for agent_id, reply in clarification_replies.items()
                ],
            )
            clarifications = {
                agent_id: reply.text for agent_id, reply in clarification_replies.items()
            }

        self.progress(f"Revision {revision}: chair drafting")
        draft = self._generate(
            chair,
            chair_draft_prompt(task, steering, summaries, clarifications, abstentions),
        )
        workspace.write(revision, self.chair_id, "chair/draft.md", draft.text)
        self._ensure_revision(revision, "chair_draft")
        self._publish_batch(revision, "chair.draft", [(self.chair_id, draft.text, draft)])

        reviewers = [
            runtime
            for runtime in self.agents
            if runtime.spec.id != self.chair_id and runtime.spec.id in typed_briefs
        ]
        self.progress(f"Revision {revision}: peer review of chair draft")

        def review(runtime: AgentRuntime) -> ModelReply:
            brief = typed_briefs[runtime.spec.id]
            reply = self._generate(
                runtime,
                review_prompt(
                    task,
                    steering,
                    brief.analysis.text,
                    brief.summary.text,
                    draft.text,
                ),
            )
            workspace.write(revision, runtime.spec.id, "review.md", reply.text)
            return reply

        review_results, review_failures = self._settle(review, reviewers)
        self._ensure_revision(revision, "peer_review")
        abstentions.update(review_failures)
        self._publish_abstentions(revision, "peer review", review_failures, audit)
        review_replies = {
            agent_id: reply for agent_id, reply in review_results.items() if isinstance(reply, ModelReply)
        }
        self._publish_batch(
            revision,
            "agent.review",
            [(agent_id, reply.text, reply) for agent_id, reply in review_replies.items()],
        )

        self.progress(f"Revision {revision}: chair finalizing")
        final = self._generate(
            chair,
            final_prompt(
                task,
                steering,
                draft.text,
                {agent_id: reply.text for agent_id, reply in review_replies.items()},
                abstentions,
            ),
        )
        workspace.write(revision, self.chair_id, "chair/final.md", final.text)
        self._ensure_revision(revision, "final")
        self._publish_batch(revision, "chair.final", [(self.chair_id, final.text, final)])
        self._abstentions = dict(abstentions)
        return final

    def run(self, task: str) -> DiscussionResult:
        task = task.strip()
        if not task:
            raise ValueError("task cannot be empty")
        if len(task) > 200_000:
            raise ValueError("task exceeds the 200,000 character safety limit")

        session_id = str(uuid.uuid4())
        audit = AuditLog(session_id, self.audit_directory)
        workspace = SessionWorkspace(audit.path.parent / session_id / "workspaces")
        with self._condition:
            if self._active:
                raise RuntimeError("this orchestrator already has an active session")
            self._active = True
            self._accepting_input = True
            self._revision = 0
            self._steering = []
            self._events = []
            self._model_calls = 0
            self._abstentions = {}
            self._deadline = (
                time.monotonic() + self.session_deadline_seconds
                if self.session_deadline_seconds is not None
                else None
            )
            self._audit = audit
            task_event = self._append_locked(0, "task", "user", task, None)
            audit.append(
                "session.started",
                {
                    "session_id": session_id,
                    "task": task,
                    "agents": [runtime.spec.__dict__ for runtime in self.agents],
                    "chair": self.chair_id,
                    "protocol": "chaired_workspace_v1",
                    "max_clarification_questions": self.max_clarification_questions,
                    "max_model_calls": self.max_model_calls,
                    "min_agent_quorum": self.min_agent_quorum,
                    "max_revisions": self.max_revisions,
                    "session_deadline_seconds": self.session_deadline_seconds,
                    "max_public_brief_chars": self.max_public_brief_chars,
                },
            )
            audit.append("room.task", task_event.public_dict())
            self._active_ready.set()

        try:
            while True:
                revision, steering = self._snapshot()
                try:
                    synthesis = self._run_cycle(
                        task,
                        revision,
                        steering,
                        workspace,
                        audit,
                    )
                except RevisionChanged:
                    with self._condition:
                        remaining = self.max_model_calls - self._model_calls
                    if remaining < self.cycle_cost:
                        audit.append(
                            "cycle.restart_refused",
                            {"remaining_calls": remaining, "cycle_cost": self.cycle_cost},
                        )
                        raise BudgetExhausted(
                            f"a restarted cycle needs {self.cycle_cost} model calls but "
                            f"only {remaining} remain; raise max_model_calls to keep "
                            "correcting a session this long"
                        )
                    self.progress("User update received: restarting the chaired cycle")
                    continue
                with self._condition:
                    if revision != self._revision:
                        audit.append(
                            "cycle.superseded",
                            {"phase": "commit", "old_revision": revision, "new_revision": self._revision},
                        )
                        continue
                    self._active = False
                    self._accepting_input = False
                    accepted_events = tuple(self._events)
                    break
            audit.append(
                "synthesis.final",
                {"chair_id": self.chair_id, "revision": revision, "reply": synthesis.public_dict()},
            )
            audit.append(
                "session.completed",
                {
                    "session_id": session_id,
                    "status": "completed",
                    "model_calls": self._model_calls,
                    "revisions": revision,
                    "abstentions": dict(self._abstentions),
                },
            )
        except Exception as exc:
            audit.append(
                "session.failed",
                {"session_id": session_id, "error_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        finally:
            with self._condition:
                self._active = False
                self._accepting_input = False
                self._audit = None
                self._deadline = None
                self._active_ready.clear()

        self.progress("Completed")
        return DiscussionResult(
            session_id=session_id,
            prompt=task,
            agents=tuple(runtime.spec for runtime in self.agents),
            events=accepted_events,
            chair_id=self.chair_id,
            synthesis=synthesis,
            audit_path=str(audit.path),
            workspace_root=str(workspace.root),
            abstentions=dict(self._abstentions),
        )

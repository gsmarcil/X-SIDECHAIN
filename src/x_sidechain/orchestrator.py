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
from x_sidechain.http import ProviderHTTPError
from x_sidechain.models import AgentSpec, DiscussionEvent, DiscussionResult, ModelReply
from x_sidechain.prompts import (
    chair_draft_prompt,
    chair_questions_prompt,
    clarification_prompt,
    final_prompt,
    private_analysis_prompt,
    public_summary_prompt,
    review_prompt,
    system_prompt,
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


@dataclass(frozen=True)
class ParsedQuestions:
    """Best-effort chair triage: malformed items are dropped, never fatal."""

    questions: tuple[ChairQuestion, ...]
    notes: tuple[str, ...]


def _json_objects(text: str) -> list[object]:
    """Every balanced top-level JSON object in text, ignoring prose and code fences."""
    found: list[object] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0:
                try:
                    found.append(json.loads(text[start : index + 1]))
                except json.JSONDecodeError:
                    pass
                start = -1
    return found


def _questions_object(text: str) -> dict | None:
    """Prefer the object that actually carries the chair's questions array."""
    objects = [item for item in _json_objects(text) if isinstance(item, dict)]
    for item in objects:
        if "questions" in item:
            return item
    return objects[0] if objects else None


class SessionError(RuntimeError):
    """An orchestrator-raised error. Its message is ours, so it is safe to show."""


class RevisionChanged(SessionError):
    pass


class BudgetExhausted(SessionError):
    """The model-call budget is spent. Never degraded to an agent abstention."""


class DeadlineReached(SessionError):
    """The session wall-clock deadline passed. No further cycle may start."""


def public_failure_reason(exc: BaseException) -> str:
    """The cause other agents and the audit may see.

    Provider text can echo request headers or another tenant's data, and the
    briefs of agents from different vendors share one room, so nothing but a
    message this project wrote itself is ever published. Full detail goes to the
    failing agent's own private workspace instead.
    """
    if isinstance(exc, (ProviderHTTPError, SessionError)):
        return str(exc)
    return type(exc).__name__


def failure_detail(exc: BaseException) -> str:
    """Operator-only text, written to the agent workspace and never to a prompt."""
    parts = [f"{type(exc).__name__}: {exc}"]
    detail = getattr(exc, "detail", "")
    if detail:
        parts.append(f"\nprovider response:\n{detail}")
    return "".join(parts)


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
        self._usage: dict[str, int] = {}
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
            # Every in-flight call is already counted, and bumping the revision
            # below stops the superseded cycle from reserving more, so this
            # remainder is what the restarted cycle will really have.
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
                raise RevisionChanged(phase)

    def _reserve_model_call(self, revision: int) -> None:
        with self._condition:
            if revision != self._revision:
                # A correction was accepted, so this cycle is already superseded.
                # Stopping here is what makes the budget check in
                # inject_user_message exact: no call is reserved after it ran.
                raise RevisionChanged("reserve")
            if self._model_calls >= self.max_model_calls:
                raise BudgetExhausted(
                    f"model-call budget exhausted ({self.max_model_calls}); "
                    "increase max_model_calls deliberately"
                )
            self._model_calls += 1

    def _record_usage(self, reply: ModelReply) -> None:
        with self._condition:
            for key, value in reply.usage.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    self._usage[key] = self._usage.get(key, 0) + value

    def _generate(self, runtime: AgentRuntime, prompt: str, revision: int) -> ModelReply:
        self._reserve_model_call(revision)
        reply = runtime.provider.generate(system_prompt(runtime.spec.role), prompt)
        self._record_usage(reply)
        return reply

    def _settle(
        self,
        work: Callable[[AgentRuntime], object],
        agents=None,
    ) -> tuple[dict[str, object], dict[str, BaseException]]:
        """Run work per agent, returning what completed and why the rest did not.

        One provider failing is that agent abstaining, not the end of a session the
        others already paid for. Budget, deadline and revision errors are control
        flow rather than agent behaviour, so they propagate instead.
        """
        selected = tuple(self.agents if agents is None else agents)
        if not selected:
            return {}, {}
        results: dict[str, object] = {}
        failures: dict[str, BaseException] = {}
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
                    failures[agent_id] = exc
        if control is not None:
            raise control
        return results, failures

    def _publish_abstentions(
        self,
        revision: int,
        phase: str,
        label: str,
        failures: dict[str, BaseException],
        abstentions: dict[str, str],
        workspace: SessionWorkspace,
        audit: AuditLog,
    ) -> None:
        """Record who did not contribute, without leaking what the provider said."""
        if not failures:
            return
        self.progress(
            f"Revision {revision}: {len(failures)} agent(s) abstained during {phase}"
        )
        public: dict[str, str] = {}
        for agent_id, exc in failures.items():
            reason = public_failure_reason(exc)
            public[agent_id] = reason
            note = f"{label} ({reason})"
            abstentions[agent_id] = (
                f"{abstentions[agent_id]}; {note}" if agent_id in abstentions else note
            )
            # Operator-only: the provider's own text never reaches a prompt, the
            # room, or the audit, only the failing agent's private workspace.
            workspace.write(
                revision,
                agent_id,
                "provider-errors.log",
                f"[{phase}] {failure_detail(exc)}",
            )
        audit.append(
            "agent.abstained",
            {"revision": revision, "phase": phase, "failures": public},
        )
        self._publish_batch(
            revision,
            "agent.abstention",
            [
                (agent_id, f"ABSTAINED during {phase}: {reason}", None)
                for agent_id, reason in public.items()
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
    def _parse_questions(text: str, valid_ids: set[str], limit: int) -> ParsedQuestions:
        """Read chair triage output without letting a formatting slip end the session.

        The briefs are already paid for at this point, so unusable triage degrades to
        "no clarification needed" and is recorded, instead of aborting the run.
        """
        notes: list[str] = []
        raw = _questions_object(text)
        if raw is None:
            return ParsedQuestions((), ("chair returned no parsable JSON object",))
        questions = raw.get("questions", [])
        if not isinstance(questions, list):
            return ParsedQuestions((), ("chair output has no questions array",))
        parsed: list[ChairQuestion] = []
        seen: set[str] = set()
        for item in questions:
            if not isinstance(item, dict):
                notes.append("dropped a non-object question entry")
                continue
            agent_id = item.get("agent_id")
            question = item.get("question")
            if agent_id not in valid_ids:
                notes.append(f"dropped a question for an unknown agent: {agent_id!r}")
                continue
            if not isinstance(question, str) or not question.strip():
                notes.append(f"dropped an empty question for {agent_id}")
                continue
            if agent_id in seen:
                notes.append(f"dropped a duplicate question for {agent_id}")
                continue
            seen.add(str(agent_id))
            parsed.append(ChairQuestion(str(agent_id), question.strip()))
        if len(parsed) > limit:
            notes.append(f"truncated {len(parsed)} questions to the limit of {limit}")
            parsed = parsed[:limit]
        return ParsedQuestions(tuple(parsed), tuple(notes))

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
                revision,
            )
            workspace.write(revision, runtime.spec.id, "analysis.md", analysis.text)
            summary = self._generate(
                runtime,
                public_summary_prompt(
                    task, steering, analysis.text, self.max_public_brief_chars
                ),
                revision,
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
        abstentions: dict[str, str] = {}
        self._publish_abstentions(
            revision, "private analysis", "no brief", brief_failures, abstentions, workspace, audit
        )
        if self.chair_id not in typed_briefs:
            chair_failure = brief_failures.get(self.chair_id)
            raise SessionError(
                f"chair {self.chair_id} produced no brief "
                f"({public_failure_reason(chair_failure) if chair_failure else 'unknown error'}); "
                "a chaired workflow cannot continue without its chair"
            )
        if len(typed_briefs) < self.min_agent_quorum:
            reasons = "; ".join(
                f"{name}: {public_failure_reason(why)}" for name, why in brief_failures.items()
            )
            raise SessionError(
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
            revision,
        )
        workspace.write(revision, self.chair_id, "chair/questions.json", question_reply.text)
        self._ensure_revision(revision, "chair_questions")
        triage = self._parse_questions(
            question_reply.text,
            set(summaries),
            self.max_clarification_questions,
        )
        questions = triage.questions
        if triage.notes:
            self.progress(f"Revision {revision}: chair triage repaired ({'; '.join(triage.notes)})")
            audit.append(
                "chair.triage_repaired",
                {"revision": revision, "chair_id": self.chair_id, "notes": list(triage.notes)},
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
                    revision,
                )
                workspace.write(revision, agent_id, "clarification.md", reply.text)
                return reply

            answers, answer_failures = self._settle(answer_question, selected)
            self._ensure_revision(revision, "clarifications")
            self._publish_abstentions(
                revision, "clarification", "no clarification", answer_failures,
                abstentions, workspace, audit,
            )
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
            revision,
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
                revision,
            )
            workspace.write(revision, runtime.spec.id, "review.md", reply.text)
            return reply

        review_results, review_failures = self._settle(review, reviewers)
        self._ensure_revision(revision, "peer_review")
        self._publish_abstentions(
            revision, "peer review", "no review", review_failures, abstentions, workspace, audit
        )
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
            revision,
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
            self._usage = {}
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
                except RevisionChanged as superseded:
                    with self._condition:
                        audit.append(
                            "cycle.superseded",
                            {
                                "phase": superseded.args[0] if superseded.args else "unknown",
                                "old_revision": revision,
                                "new_revision": self._revision,
                            },
                        )
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
                    "max_model_calls": self.max_model_calls,
                    "usage_totals": dict(self._usage),
                    "revisions": revision,
                    "abstentions": dict(self._abstentions),
                },
            )
        except Exception as exc:
            audit.append(
                "session.failed",
                {
                    "session_id": session_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "model_calls": self._model_calls,
                    "usage_totals": dict(self._usage),
                },
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
            model_calls=self._model_calls,
            usage_totals=dict(self._usage),
            abstentions=dict(self._abstentions),
        )

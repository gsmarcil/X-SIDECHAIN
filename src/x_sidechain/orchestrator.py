from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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


class SidechainOrchestrator:
    """Moderated team protocol with private workspaces and bounded public events."""

    def __init__(
        self,
        agents: tuple[AgentRuntime, ...],
        chair_id: str,
        max_clarification_questions: int = 4,
        max_model_calls: int | None = None,
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

        self.agents = agents
        self.chair_id = chair_id
        self.max_clarification_questions = max_clarification_questions
        self.max_model_calls = call_budget
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
                raise RuntimeError(
                    f"model-call budget exhausted ({self.max_model_calls}); "
                    "increase max_model_calls deliberately"
                )
            self._model_calls += 1

    def _generate(self, runtime: AgentRuntime, prompt: str) -> ModelReply:
        self._reserve_model_call()
        return runtime.provider.generate(runtime.spec.role, prompt)

    def _parallel(self, work: Callable[[AgentRuntime], object], agents=None) -> dict[str, object]:
        selected = tuple(self.agents if agents is None else agents)
        if not selected:
            return {}
        with ThreadPoolExecutor(max_workers=len(selected), thread_name_prefix="xsidechain-team") as pool:
            futures = {runtime.spec.id: pool.submit(work, runtime) for runtime in selected}
            return {runtime.spec.id: futures[runtime.spec.id].result() for runtime in selected}

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
                public_summary_prompt(task, steering, analysis.text),
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

        briefs = self._parallel(build_brief)
        self._ensure_revision(revision, "private_analysis")
        typed_briefs = {agent_id: value for agent_id, value in briefs.items() if isinstance(value, AgentBrief)}
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

            answers = self._parallel(answer_question, selected)
            self._ensure_revision(revision, "clarifications")
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
            chair_draft_prompt(task, steering, summaries, clarifications),
        )
        workspace.write(revision, self.chair_id, "chair/draft.md", draft.text)
        self._ensure_revision(revision, "chair_draft")
        self._publish_batch(revision, "chair.draft", [(self.chair_id, draft.text, draft)])

        reviewers = [runtime for runtime in self.agents if runtime.spec.id != self.chair_id]
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

        review_results = self._parallel(review, reviewers)
        self._ensure_revision(revision, "peer_review")
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
            ),
        )
        workspace.write(revision, self.chair_id, "chair/final.md", final.text)
        self._ensure_revision(revision, "final")
        self._publish_batch(revision, "chair.final", [(self.chair_id, final.text, final)])
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
            audit.append("session.completed", {"session_id": session_id, "status": "completed"})
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
        )

from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from x_sidechain.audit import AuditLog
from x_sidechain.models import AgentSpec, DiscussionEvent, DiscussionResult, ModelReply
from x_sidechain.prompts import contribution_prompt, synthesis_prompt
from x_sidechain.providers.base import Provider

ProgressCallback = Callable[[str], None]
EventCallback = Callable[[DiscussionEvent], None]


@dataclass(frozen=True)
class AgentRuntime:
    spec: AgentSpec
    provider: Provider


class SidechainOrchestrator:
    """Optimistic live room with stale-draft rejection.

    Providers without native mid-turn steering generate against a room sequence. If
    another agent or the user publishes before that generation returns, the stale
    draft is audited but never admitted to the room. The agent retries against the
    new shared state. This provides a provider-neutral correctness fallback while
    native steering transports can later avoid the wasted generation.
    """

    def __init__(
        self,
        agents: tuple[AgentRuntime, ...],
        synthesizer_id: str,
        contributions_per_agent: int = 2,
        max_model_calls: int | None = None,
        audit_directory: Path | None = None,
        progress: ProgressCallback | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        if len(agents) < 2:
            raise ValueError("at least two agents are required")
        ids = [runtime.spec.id for runtime in agents]
        if len(ids) != len(set(ids)):
            raise ValueError("agent ids must be unique")
        if synthesizer_id not in ids:
            raise ValueError("synthesizer_id must reference an agent")
        if contributions_per_agent < 1 or contributions_per_agent > 20:
            raise ValueError("contributions_per_agent must be between 1 and 20")
        minimum_calls = contributions_per_agent * len(agents) * (len(agents) + 1) // 2 + 1
        call_budget = max_model_calls if max_model_calls is not None else minimum_calls + 5 * len(agents)
        if call_budget < minimum_calls:
            raise ValueError(
                f"max_model_calls must be at least {minimum_calls} for this live-room configuration"
            )
        self.agents = agents
        self.synthesizer_id = synthesizer_id
        self.contributions_per_agent = contributions_per_agent
        self.max_model_calls = call_budget
        self.audit_directory = audit_directory
        self.progress = progress or (lambda _message: None)
        self.on_event = on_event or (lambda _event: None)

        self._condition = threading.Condition()
        self._active = False
        self._active_ready = threading.Event()
        self._finish_requested = False
        self._events: list[DiscussionEvent] = []
        self._counts: dict[str, int] = {}
        self._audit: AuditLog | None = None
        self._model_calls = 0

    def wait_until_active(self, timeout: float | None = None) -> bool:
        return self._active_ready.wait(timeout)

    def _append_locked(
        self,
        kind: str,
        author: str,
        content: str,
        based_on_sequence: int | None,
        reply: ModelReply | None = None,
    ) -> DiscussionEvent:
        event = DiscussionEvent(
            sequence=len(self._events),
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
                raise RuntimeError("no live discussion is active")
            previous = self._events[-1].sequence
            event = self._append_locked("user.steering", "user", message, previous)
            self._audit.append("room.user_steering", event.public_dict())
            self._condition.notify_all()
        self.on_event(event)
        return event

    def request_finish(self) -> None:
        with self._condition:
            if not self._active:
                raise RuntimeError("no live discussion is active")
            self._finish_requested = True
            self._condition.notify_all()

    def _reserve_model_call(self) -> None:
        with self._condition:
            if self._model_calls >= self.max_model_calls:
                self._finish_requested = True
                self._condition.notify_all()
                raise RuntimeError(
                    f"model-call budget exhausted ({self.max_model_calls}); "
                    "increase max_model_calls deliberately"
                )
            self._model_calls += 1

    def _agent_worker(self, runtime: AgentRuntime, task: str) -> None:
        agent_id = runtime.spec.id
        while True:
            with self._condition:
                while True:
                    if self._finish_requested or self._counts[agent_id] >= self.contributions_per_agent:
                        return
                    least_contributions = min(self._counts.values())
                    if self._counts[agent_id] == least_contributions:
                        snapshot = tuple(self._events)
                        base_sequence = snapshot[-1].sequence
                        break
                    self._condition.wait()

            try:
                self._reserve_model_call()
                reply = runtime.provider.generate(
                    runtime.spec.role,
                    contribution_prompt(task, snapshot, agent_id),
                )
            except Exception:
                with self._condition:
                    self._finish_requested = True
                    self._condition.notify_all()
                raise

            accepted: DiscussionEvent | None = None
            with self._condition:
                if self._finish_requested:
                    assert self._audit is not None
                    self._audit.append(
                        "room.draft_discarded",
                        {
                            "agent_id": agent_id,
                            "reason": "finish_requested",
                            "based_on_sequence": base_sequence,
                            "reply": reply.public_dict(),
                        },
                    )
                    return
                current_sequence = self._events[-1].sequence
                assert self._audit is not None
                if current_sequence != base_sequence:
                    self._audit.append(
                        "room.draft_superseded",
                        {
                            "agent_id": agent_id,
                            "based_on_sequence": base_sequence,
                            "current_sequence": current_sequence,
                            "reply": reply.public_dict(),
                        },
                    )
                    continue
                accepted = self._append_locked(
                    "agent.contribution",
                    agent_id,
                    reply.text,
                    base_sequence,
                    reply,
                )
                self._counts[agent_id] += 1
                self._audit.append("room.contribution_accepted", accepted.public_dict())
                self._condition.notify_all()
            self.on_event(accepted)

    def run(self, task: str) -> DiscussionResult:
        task = task.strip()
        if not task:
            raise ValueError("task cannot be empty")
        if len(task) > 200_000:
            raise ValueError("task exceeds the 200,000 character safety limit")

        session_id = str(uuid.uuid4())
        audit = AuditLog(session_id, self.audit_directory)
        with self._condition:
            if self._active:
                raise RuntimeError("this orchestrator already has an active discussion")
            self._active = True
            self._finish_requested = False
            self._audit = audit
            self._counts = {runtime.spec.id: 0 for runtime in self.agents}
            self._model_calls = 0
            self._events = []
            task_event = self._append_locked("task", "user", task, None)
            audit.append(
                "session.started",
                {
                    "session_id": session_id,
                    "task": task,
                    "agents": [runtime.spec.__dict__ for runtime in self.agents],
                    "synthesizer": self.synthesizer_id,
                    "protocol": "optimistic_live_room_v1",
                    "contributions_per_agent": self.contributions_per_agent,
                    "max_model_calls": self.max_model_calls,
                },
            )
            audit.append("room.task", task_event.public_dict())
            self._active_ready.set()
        try:
            self.progress(
                f"Live room: {len(self.agents)} agents, "
                f"{self.contributions_per_agent} accepted contributions each"
            )
            with ThreadPoolExecutor(
                max_workers=len(self.agents),
                thread_name_prefix="xsidechain-live",
            ) as pool:
                futures: dict[str, Future[None]] = {
                    runtime.spec.id: pool.submit(self._agent_worker, runtime, task)
                    for runtime in self.agents
                }
                for runtime in self.agents:
                    futures[runtime.spec.id].result()

            self.progress(f"Synthesis: {self.synthesizer_id} reading the live room")
            synthesizer = next(
                runtime for runtime in self.agents if runtime.spec.id == self.synthesizer_id
            )
            while True:
                with self._condition:
                    accepted_events = tuple(self._events)
                    synthesis_base = accepted_events[-1].sequence
                self._reserve_model_call()
                draft = synthesizer.provider.generate(
                    synthesizer.spec.role,
                    synthesis_prompt(task, accepted_events),
                )
                with self._condition:
                    current_sequence = self._events[-1].sequence
                    if current_sequence != synthesis_base:
                        audit.append(
                            "synthesis.draft_superseded",
                            {
                                "based_on_sequence": synthesis_base,
                                "current_sequence": current_sequence,
                                "reply": draft.public_dict(),
                            },
                        )
                        continue
                    synthesis = draft
                    self._active = False
                    break
            audit.append(
                "synthesis.final",
                {"agent_id": self.synthesizer_id, "reply": synthesis.public_dict()},
            )
            audit.append("session.completed", {"session_id": session_id, "status": "completed"})
        except Exception as exc:
            with self._condition:
                self._finish_requested = True
                self._condition.notify_all()
            audit.append(
                "session.failed",
                {"session_id": session_id, "error_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        finally:
            with self._condition:
                self._active = False
                self._audit = None
                self._active_ready.clear()

        self.progress("Completed")
        return DiscussionResult(
            session_id=session_id,
            prompt=task,
            agents=tuple(runtime.spec for runtime in self.agents),
            events=accepted_events,
            synthesizer_id=self.synthesizer_id,
            synthesis=synthesis,
            audit_path=str(audit.path),
        )

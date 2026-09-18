from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from x_sidechain.audit import AuditLog
from x_sidechain.models import AgentSpec, DebateResult, ModelReply
from x_sidechain.prompts import critique_prompt, initial_prompt, synthesis_prompt
from x_sidechain.providers.base import Provider

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class AgentRuntime:
    spec: AgentSpec
    provider: Provider


class SidechainOrchestrator:
    def __init__(
        self,
        agents: tuple[AgentRuntime, ...],
        synthesizer_id: str,
        cross_review: bool = True,
        audit_directory: Path | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        if len(agents) < 2:
            raise ValueError("at least two agents are required")
        ids = [runtime.spec.id for runtime in agents]
        if len(ids) != len(set(ids)):
            raise ValueError("agent ids must be unique")
        if synthesizer_id not in ids:
            raise ValueError("synthesizer_id must reference an agent")
        self.agents = agents
        self.synthesizer_id = synthesizer_id
        self.cross_review = cross_review
        self.audit_directory = audit_directory
        self.progress = progress or (lambda _message: None)

    def _parallel_generate(self, prompt_for: Callable[[AgentSpec], str]) -> dict[str, ModelReply]:
        # A barrier prevents early providers from seeing the shared prompt materially
        # before later providers. The provider calls begin only after every worker is ready.
        barrier = threading.Barrier(len(self.agents))

        def invoke(runtime: AgentRuntime) -> ModelReply:
            barrier.wait(timeout=30)
            return runtime.provider.generate(runtime.spec.role, prompt_for(runtime.spec))

        with ThreadPoolExecutor(max_workers=len(self.agents), thread_name_prefix="xsidechain") as pool:
            futures: dict[str, Future[ModelReply]] = {
                runtime.spec.id: pool.submit(invoke, runtime) for runtime in self.agents
            }
            return {runtime.spec.id: futures[runtime.spec.id].result() for runtime in self.agents}

    @staticmethod
    def _record_replies(audit: AuditLog, event: str, replies: dict[str, ModelReply]) -> None:
        for agent_id, reply in replies.items():
            audit.append(event, {"agent_id": agent_id, "reply": reply.public_dict()})

    def run(self, task: str) -> DebateResult:
        task = task.strip()
        if not task:
            raise ValueError("task cannot be empty")
        if len(task) > 200_000:
            raise ValueError("task exceeds the 200,000 character safety limit")

        session_id = str(uuid.uuid4())
        audit = AuditLog(session_id, self.audit_directory)
        audit.append(
            "session.started",
            {
                "session_id": session_id,
                "task": task,
                "agents": [runtime.spec.__dict__ for runtime in self.agents],
                "synthesizer": self.synthesizer_id,
                "cross_review": self.cross_review,
            },
        )
        try:
            self.progress(f"Independent analysis: broadcasting to {len(self.agents)} agents")
            shared_prompt = initial_prompt(task)
            initial = self._parallel_generate(lambda _spec: shared_prompt)
            self._record_replies(audit, "analysis.independent", initial)

            critiques: dict[str, ModelReply] = {}
            if self.cross_review:
                self.progress(f"Cross-examination: {len(self.agents)} agents reviewing all peers")

                def review_for(spec: AgentSpec) -> str:
                    peers = {
                        agent_id: reply.text
                        for agent_id, reply in initial.items()
                        if agent_id != spec.id
                    }
                    return critique_prompt(task, peers)

                critiques = self._parallel_generate(review_for)
                self._record_replies(audit, "analysis.cross_review", critiques)

            self.progress(f"Synthesis: {self.synthesizer_id} enforcing the evidence gate")
            synthesizer = next(
                runtime for runtime in self.agents if runtime.spec.id == self.synthesizer_id
            )
            synthesis = synthesizer.provider.generate(
                synthesizer.spec.role,
                synthesis_prompt(
                    task,
                    {agent_id: reply.text for agent_id, reply in initial.items()},
                    {agent_id: reply.text for agent_id, reply in critiques.items()},
                ),
            )
            audit.append(
                "synthesis.final",
                {"agent_id": self.synthesizer_id, "reply": synthesis.public_dict()},
            )
            audit.append("session.completed", {"session_id": session_id, "status": "completed"})
        except Exception as exc:
            audit.append(
                "session.failed",
                {"session_id": session_id, "error_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        self.progress("Completed")
        return DebateResult(
            session_id=session_id,
            prompt=task,
            agents=tuple(runtime.spec for runtime in self.agents),
            initial=initial,
            critiques=critiques,
            synthesizer_id=self.synthesizer_id,
            synthesis=synthesis,
            audit_path=str(audit.path),
        )


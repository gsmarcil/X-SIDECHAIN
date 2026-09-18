from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from x_sidechain.audit import AuditLog
from x_sidechain.models import AgentSpec, DebateResult, ModelReply
from x_sidechain.prompts import critique_prompt, initial_prompt, synthesis_prompt
from x_sidechain.providers.base import Provider

ProgressCallback = Callable[[str], None]


class SidechainOrchestrator:
    def __init__(
        self,
        provider_a: Provider,
        provider_b: Provider,
        agent_a: AgentSpec,
        agent_b: AgentSpec,
        synthesizer: str = "a",
        audit_directory: Path | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        if synthesizer not in {"a", "b"}:
            raise ValueError("synthesizer must be 'a' or 'b'")
        self.provider_a = provider_a
        self.provider_b = provider_b
        self.agent_a = agent_a
        self.agent_b = agent_b
        self.synthesizer = synthesizer
        self.audit_directory = audit_directory
        self.progress = progress or (lambda _message: None)

    def _record_reply(self, audit: AuditLog, event: str, reply: ModelReply) -> None:
        audit.append(event, reply.public_dict())

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
                "agent_a": self.agent_a.__dict__,
                "agent_b": self.agent_b.__dict__,
                "synthesizer": self.synthesizer,
            },
        )

        self.progress("Independent analysis: both agents are running")
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xsidechain") as pool:
            future_a = pool.submit(self.provider_a.generate, self.agent_a.role, initial_prompt(task))
            future_b = pool.submit(self.provider_b.generate, self.agent_b.role, initial_prompt(task))
            initial_a = future_a.result()
            initial_b = future_b.result()
        self._record_reply(audit, "analysis.agent_a", initial_a)
        self._record_reply(audit, "analysis.agent_b", initial_b)

        self.progress("Cross-examination: agents are challenging each other")
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xsidechain") as pool:
            future_a = pool.submit(
                self.provider_a.generate,
                self.agent_a.role,
                critique_prompt(task, self.agent_b.name, initial_b.text),
            )
            future_b = pool.submit(
                self.provider_b.generate,
                self.agent_b.role,
                critique_prompt(task, self.agent_a.name, initial_a.text),
            )
            critique_a = future_a.result()
            critique_b = future_b.result()
        self._record_reply(audit, "critique.agent_a", critique_a)
        self._record_reply(audit, "critique.agent_b", critique_b)

        self.progress("Synthesis: enforcing the evidence gate")
        synthesis_provider = self.provider_a if self.synthesizer == "a" else self.provider_b
        synthesis_role = self.agent_a.role if self.synthesizer == "a" else self.agent_b.role
        synthesis = synthesis_provider.generate(
            synthesis_role,
            synthesis_prompt(
                task,
                initial_a.text,
                initial_b.text,
                critique_a.text,
                critique_b.text,
            ),
        )
        self._record_reply(audit, "synthesis.final", synthesis)
        audit.append("session.completed", {"session_id": session_id, "status": "completed"})
        self.progress("Completed")

        return DebateResult(
            session_id=session_id,
            prompt=task,
            agent_a=self.agent_a,
            agent_b=self.agent_b,
            initial_a=initial_a,
            initial_b=initial_b,
            critique_a=critique_a,
            critique_b=critique_b,
            synthesis=synthesis,
            audit_path=str(audit.path),
        )


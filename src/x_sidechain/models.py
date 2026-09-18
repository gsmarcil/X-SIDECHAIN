from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentSpec:
    name: str
    provider: str
    model: str
    role: str


@dataclass(frozen=True)
class ModelReply:
    provider: str
    model: str
    text: str
    request_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DebateResult:
    session_id: str
    prompt: str
    agent_a: AgentSpec
    agent_b: AgentSpec
    initial_a: ModelReply
    initial_b: ModelReply
    critique_a: ModelReply
    critique_b: ModelReply
    synthesis: ModelReply
    audit_path: str


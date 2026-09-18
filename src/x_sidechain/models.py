from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentSpec:
    id: str
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
class DiscussionEvent:
    sequence: int
    revision: int
    kind: str
    author: str
    content: str
    based_on_sequence: int | None
    reply: ModelReply | None = None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscussionResult:
    session_id: str
    prompt: str
    agents: tuple[AgentSpec, ...]
    events: tuple[DiscussionEvent, ...]
    chair_id: str
    synthesis: ModelReply
    audit_path: str
    workspace_root: str

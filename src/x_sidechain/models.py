from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def cycle_cost(agent_count: int, max_clarification_questions: int) -> int:
    """Model calls one full cycle costs at worst: 3N + min(Q, N) + 2.

    Private analysis, public brief and peer review per agent, at most one
    clarification each, then the chair's draft and final result.
    """
    return 3 * agent_count + min(max_clarification_questions, agent_count) + 2


def default_call_budget(agent_count: int, max_clarification_questions: int) -> int:
    """The budget when none is configured: one cycle plus two restarts."""
    return cycle_cost(agent_count, max_clarification_questions) * 3


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
    model_calls: int = 0
    usage_totals: dict[str, int] = field(default_factory=dict)
    abstentions: dict[str, str] = field(default_factory=dict)

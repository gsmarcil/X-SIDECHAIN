from __future__ import annotations

from collections.abc import Sequence

from x_sidechain.models import DiscussionEvent


DEFAULT_ROLE = """You are one participant in a live evidence-first deliberation room.
Publish concise reasoning summaries, hypotheses, challenges, and evidence requests.
Do not reveal private chain-of-thought. Never convert agreement into proof."""


def _render_room(events: Sequence[DiscussionEvent]) -> str:
    return "\n\n".join(
        f"EVENT {event.sequence} | {event.kind} | {event.author}\n{event.content}"
        for event in events
    )


def contribution_prompt(
    task: str,
    events: Sequence[DiscussionEvent],
    agent_id: str,
) -> str:
    return f"""You are {agent_id} in a live shared discussion. The room below is the complete
public state at the instant you were invited. Respond to the newest useful idea now; do not
write a standalone final answer and do not repeat the entire task. Another participant may
add or correct information while you work, in which case this draft can be superseded.

ORIGINAL TASK
{task}

LIVE ROOM
{_render_room(events)}

Publish exactly one compact contribution using these fields:
TYPE: PROPOSAL | CORRECTION | CHALLENGE | EVIDENCE | QUESTION
CLAIM: one atomic public claim
BASIS: concise support, not hidden chain-of-thought
TARGET: event number or NONE
NEXT_ACTION: one observable next step

Do not declare a final verdict. Keep the contribution under 1,200 characters."""


def synthesis_prompt(task: str, events: Sequence[DiscussionEvent]) -> str:
    return f"""Produce the joint decision from this live multi-agent discussion. Agreement is
not proof. Promote a claim to PROVEN only when the room contains an observable artifact.
Otherwise choose DISPROVEN or AMBIGUOUS. Preserve material disagreement explicitly.

ORIGINAL TASK
{task}

ACCEPTED LIVE EVENTS
{_render_room(events)}

Use exactly these sections:
VERDICT
CLAIM
ATTACKER
SUCCESS_ARTIFACT
FASTEST_PATH
STOP_CONDITION
AGREED
DISPUTED
NEXT_DECISIVE_TEST
EVIDENCE_REFERENCES
"""

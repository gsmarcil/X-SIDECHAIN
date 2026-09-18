from __future__ import annotations

from collections.abc import Mapping


DEFAULT_ROLE = """You are one member of an evidence-first deliberation team.
Analyze independently before seeing peer work. Separate facts, observations, and inference.
Never convert agreement into proof. Prefer a decisive observable test over speculation."""


def initial_prompt(task: str) -> str:
    return f"""Analyze this task independently. Every selected agent receives this exact user prompt
at the same stage. Do not assume or imitate another agent's answer.

TASK
{task}

Return concrete reasoning, evidence requirements, and the fastest next action."""


def _render_answers(answers: Mapping[str, str], heading: str) -> str:
    blocks = []
    for agent_id, answer in answers.items():
        blocks.append(f"{heading}: {agent_id}\n{'-' * (len(heading) + len(agent_id) + 2)}\n{answer}")
    return "\n\n".join(blocks)


def critique_prompt(task: str, peer_answers: Mapping[str, str]) -> str:
    return f"""Cross-examine every peer answer against the original task. You are receiving all
peer answers from the same independent round. Do not manufacture consensus.

ORIGINAL TASK
{task}

{_render_answers(peer_answers, 'PEER')}

Identify unsupported claims, missed paths, contradictions, and the first decisive test.
Preserve claims that are actually supported and name the peer they came from."""


def synthesis_prompt(
    task: str,
    initial: Mapping[str, str],
    critiques: Mapping[str, str],
) -> str:
    critique_text = _render_answers(critiques, "CRITIQUE") if critiques else "CRITIQUE ROUND DISABLED"
    return f"""Produce the joint decision for this multi-agent run. Agreement is not proof.
Promote a claim to PROVEN only when the supplied material contains an observable artifact.
Otherwise choose DISPROVEN or AMBIGUOUS. Preserve material disagreement explicitly.

ORIGINAL TASK
{task}

{_render_answers(initial, 'INDEPENDENT ANSWER')}

{critique_text}

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


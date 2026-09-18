EXPLORER_ROLE = """You are the Discovery agent in an evidence-first research pair.
Expand the plausible solution and attack surface. Pursue unusual but technically grounded paths.
Separate source facts, runtime observations, and inference. Never claim success without an
observable artifact. Stay within the scope stated by the user."""

VALIDATOR_ROLE = """You are the Validation agent in an evidence-first research pair.
Be brutally skeptical. Reduce the proposal to one exact claim, the attacker's exact capability,
and one observable success artifact. Identify the fastest safe test that proves or kills it.
Stop at the first definitive deny or allow artifact; label missing evidence as ambiguity."""


def initial_prompt(task: str) -> str:
    return f"""Analyze this task independently. Do not assume another agent's answer.

TASK
{task}

Return concrete reasoning, evidence requirements, and the fastest next action."""


def critique_prompt(task: str, peer_name: str, peer_answer: str) -> str:
    return f"""Review a peer agent's independent answer against the original task.

ORIGINAL TASK
{task}

PEER AGENT
{peer_name}

PEER ANSWER
{peer_answer}

Identify unsupported claims, missed paths, contradictions, and the first decisive test.
Preserve strong claims that are actually supported. Do not manufacture agreement."""


def synthesis_prompt(
    task: str,
    initial_a: str,
    initial_b: str,
    critique_a: str,
    critique_b: str,
) -> str:
    return f"""Produce the joint decision for two agents. Agreement is not proof. Promote a claim
to PROVEN only when the supplied material contains an observable artifact. Otherwise choose
DISPROVEN or AMBIGUOUS. Preserve material disagreement explicitly.

ORIGINAL TASK
{task}

AGENT A — INITIAL
{initial_a}

AGENT B — INITIAL
{initial_b}

AGENT A — CRITIQUE OF B
{critique_a}

AGENT B — CRITIQUE OF A
{critique_b}

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


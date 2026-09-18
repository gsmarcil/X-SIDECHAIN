from __future__ import annotations

from collections.abc import Mapping, Sequence


DEFAULT_ROLE = """You are a member of an evidence-first agent team.
Produce auditable work products, not hidden chain-of-thought. Separate observations,
claims, uncertainty, and decisive tests. Agreement is never proof."""


def _task_context(task: str, steering: Sequence[str]) -> str:
    updates = "\n".join(f"- {item}" for item in steering) if steering else "- NONE"
    return f"ORIGINAL TASK\n{task}\n\nUSER UPDATES\n{updates}"


def _named(items: Mapping[str, str], heading: str) -> str:
    return "\n\n".join(f"{heading} [{name}]\n{text}" for name, text in items.items())


def private_analysis_prompt(task: str, steering: Sequence[str], agent_id: str) -> str:
    return f"""PRIVATE WORKSPACE ANALYSIS for agent {agent_id}.
{_task_context(task, steering)}

Develop your own work product before seeing other agents. Do not provide private
chain-of-thought. Record candidate claims, observable evidence, uncertainty, failure
conditions, and the fastest safe test. This note stays in your agent workspace and is
not shown to peers or the chair."""


def public_summary_prompt(task: str, steering: Sequence[str], analysis: str) -> str:
    return f"""Create the public brief that the room chair will read.
{_task_context(task, steering)}

PRIVATE ANALYSIS WORK PRODUCT
{analysis}

Do not dump the full analysis. Use exactly these fields and stay under 1,200 characters:
CLAIM:
EVIDENCE:
UNCERTAINTY:
FASTEST_TEST:
STOP_CONDITION:
QUESTION_FOR_CHAIR:"""


def chair_questions_prompt(
    task: str,
    steering: Sequence[str],
    summaries: Mapping[str, str],
    max_questions: int,
) -> str:
    return f"""You are the room chair. Read only the public briefs. Ask a targeted
question only when a specific ambiguity could change the final decision. Do not ask
every agent by default and do not draft the result yet.

{_task_context(task, steering)}

PUBLIC BRIEFS
{_named(summaries, 'AGENT')}

Return JSON only:
{{"questions":[{{"agent_id":"exact-id","question":"one decisive question"}}]}}
Use an empty array if no clarification is needed. Maximum questions: {max_questions}.
Every agent_id must exactly match one of: {', '.join(summaries)}."""


def clarification_prompt(
    task: str,
    steering: Sequence[str],
    analysis: str,
    summary: str,
    question: str,
) -> str:
    return f"""Answer one targeted chair question using your private workspace.
{_task_context(task, steering)}

YOUR PRIVATE WORK PRODUCT
{analysis}

YOUR PUBLIC BRIEF
{summary}

CHAIR QUESTION
{question}

Reply with the direct answer, supporting artifact, remaining uncertainty, and one
decisive next action. Do not broaden the discussion."""


def chair_draft_prompt(
    task: str,
    steering: Sequence[str],
    summaries: Mapping[str, str],
    clarifications: Mapping[str, str],
) -> str:
    clarification_text = _named(clarifications, "CLARIFICATION") if clarifications else "NONE"
    return f"""CHAIR DRAFT. Produce a provisional joint result from the public briefs
and targeted clarifications. Keep disagreements visible. This draft will be reviewed
by the other agents before it becomes final.

{_task_context(task, steering)}

PUBLIC BRIEFS
{_named(summaries, 'AGENT')}

TARGETED CLARIFICATIONS
{clarification_text}

Use these sections: PROVISIONAL_VERDICT, SUPPORTED_CLAIMS, UNSUPPORTED_CLAIMS,
DISAGREEMENTS, DECISIVE_EVIDENCE, NEXT_ACTION."""


def review_prompt(
    task: str,
    steering: Sequence[str],
    own_analysis: str,
    own_summary: str,
    draft: str,
) -> str:
    return f"""REVIEW THE CHAIR DRAFT against your own workspace and the task.
{_task_context(task, steering)}

YOUR PRIVATE WORK PRODUCT
{own_analysis}

YOUR PUBLIC BRIEF
{own_summary}

CHAIR DRAFT
{draft}

Return exactly:
VERDICT: APPROVE | CORRECT
ERROR: NONE or one exact unsupported/omitted claim
CORRECTION: NONE or replacement text
EVIDENCE: event, artifact, or missing test that decides it"""


def final_prompt(
    task: str,
    steering: Sequence[str],
    draft: str,
    reviews: Mapping[str, str],
) -> str:
    return f"""FINAL RESULT. Revise the chair draft using the peer reviews. Agreement
is not proof. Promote a claim to PROVEN only with an observable artifact; otherwise
use DISPROVEN or AMBIGUOUS. Preserve unresolved disagreement.

{_task_context(task, steering)}

CHAIR DRAFT
{draft}

PEER REVIEWS
{_named(reviews, 'REVIEW')}

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
EVIDENCE_REFERENCES"""

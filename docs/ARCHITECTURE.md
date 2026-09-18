# Architecture

X-SIDECHAIN is a local chaired-agent system. It sends prompts directly to configured
providers; no X-SIDECHAIN relay or hosted backend is required.

## Moderated protocol

For `N` agents and one configured chair:

1. **Private work** — every agent independently creates `analysis.md` in its own
   revision workspace. No peer or chair prompt receives this note.
2. **Public brief** — the same agent condenses its work into `summary.md` with claim,
   evidence, uncertainty, fastest test, and stop condition.
3. **Chair triage** — the chair reads all public briefs and returns zero or more
   targeted questions. Each question names exactly one agent; there is at most one
   question per agent and a configured total limit.
4. **Targeted clarification** — only named agents receive their question, their own
   private note, and their own public brief.
5. **Chair draft** — the chair creates a provisional decision from public briefs and
   clarifications.
6. **Peer review** — every non-chair agent compares the draft with its own workspace
   and returns `APPROVE` or one exact correction.
7. **Final decision** — the chair revises the draft using peer reviews and preserves
   unresolved disagreement.

A provider failure at any step is recorded as that agent abstaining, published as
`agent.abstention`, and carried into the chair's draft and final prompts, which are
told to read absence as missing evidence rather than as agreement. The session stops
only when the chair itself fails or when fewer than `min_agent_quorum` briefs exist.

This topology avoids a free-for-all room: agents cannot continuously interrupt one
another, the chair controls clarification, and every public message has a phase.

## User corrections and revisions

Interactive user input increments the task revision. Every model call in a cycle is
bound to the revision snapshot used to create it. If the revision changes before a
phase or final result commits, the cycle is marked `cycle.superseded` and restarts
from private analysis using the original task plus all user updates.

Earlier work is retained under `revision-N`; it is never silently overwritten. This
is more expensive than patching a single response but gives a clear audit boundary.

Because each correction pays for a whole new cycle, corrections are refused once any
of three bounds is reached: `max_revisions`, the session deadline, or a remaining
call budget smaller than one full cycle. A refused correction is reported to the user
and leaves the running cycle free to finish, which is what guarantees that a session
ends with a result rather than an exhausted budget.

## Workspace layout

```text
SESSION_ID/workspaces/
  revision-0/agents/AGENT_ID/
    analysis.md
    summary.md             # published brief, capped at max_public_brief_chars
    summary.full.md        # only when the brief had to be cut
    clarification.md        # only when asked
    review.md               # non-chair agents
    chair/                  # chair only
      questions.json
      draft.md
      final.md
```

Directories use mode `0700` and files `0600`. Agent IDs are restricted to safe ASCII
path components. Isolation is currently prompt-level and file-layout isolation: the
provider receives only content explicitly selected for that phase. Models have no
filesystem or shell tool access yet.

## Calls and budget

The maximum calls in one full cycle are:

`3N + min(Q, N) + 2`

where `Q` is `max_clarification_questions`. This includes two calls per agent for
private analysis and summary, chair triage and draft, up to `Q` clarifications,
`N - 1` peer reviews, and the final chair call. User revisions can restart a cycle,
so `max_model_calls` is a hard admission budget across the whole session.

## Module boundaries

- `config.py`: providers, agents, chair, limits, and authentication configuration.
- `workspace.py`: safe agent paths and local permissions.
- `auth.py`: API credentials and official OAuth Device Flow.
- `providers/`: wire-protocol adapters.
- `orchestrator.py`: phase machine, targeted routing, revisions, and call budget.
- `prompts.py`: per-phase information boundaries.
- `audit.py`: SHA-256 chained session records.
- `__main__.py`: CLI, interactive user updates, and audit verification.

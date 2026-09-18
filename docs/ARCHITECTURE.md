# Architecture

X-SIDECHAIN is a local Linux application. It sends the room state directly to the
configured model endpoints; no X-SIDECHAIN relay or hosted backend is required.

## Live-room protocol

The unit of collaboration is a public `DiscussionEvent`, not a completed private
answer. Events contain a sequence number, author, kind, content, and the room sequence
on which the contribution was based.

For `N` configured agents:

1. The user's task becomes event `0` in one shared room.
2. Eligible agents begin drafting a single compact contribution against the current
   sequence.
3. The first valid draft commits as the next event.
4. Any concurrent draft based on an older sequence becomes `draft_superseded`; it is
   audited but never shown as an accepted contribution. Its agent retries with the
   updated room, including the new correction or idea.
5. Contribution-count fairness prevents a fast provider from monopolizing the room.
6. User input is appended by the same mechanism and invalidates in-flight old drafts.
7. Final synthesis is also optimistic: user input arriving during synthesis invalidates
   that draft and forces a new synthesis from the latest event stream.

This is optimistic concurrency control for model conversation. It gives every
accepted statement a precise `based_on_sequence` and provides provider-neutral
mid-run correction without pretending that all APIs can mutate an active inference.

## Public reasoning, not hidden thought

Agents publish bounded reasoning summaries in the form `TYPE`, `CLAIM`, `BASIS`,
`TARGET`, and `NEXT_ACTION`. X-SIDECHAIN does not request, store, or expose private
chain-of-thought. “Live thinking” therefore means a fast stream of explicit,
auditable contributions that other participants can correct before the final answer.

## Native steering and fallback

Some providers/models expose a transport that can accept input during an active
response. Future adapters can use that capability to avoid discarded work. The base
contract remains stale-draft rejection so arbitrary compatible providers can still
participate correctly. This may consume more model calls than a staged debate.

With no user intervention, `C` contributions per agent require at most
`C × N × (N + 1) / 2 + 1` model calls under the current fallback, including one
synthesis. Each mid-run correction can supersede additional active drafts. A hard
`max_model_calls` budget fails closed before an unbounded retry loop.

## Extension boundaries

- `config.py`: versioned provider, authentication, agent, and run configuration.
- `auth.py`: environment credentials and official OAuth Device Flow.
- `providers/`: protocol adapters behind `Provider.generate`.
- `orchestrator.py`: shared event stream, fairness, stale-draft rejection, user
  steering, and optimistic synthesis.
- `prompts.py`: public contribution contract and evidence-gated synthesis.
- `audit.py`: append-only SHA-256 chained session records.
- `__main__.py`: configuration-first CLI, interactive steering, and verification.

## UI boundary

There is no UI in v0.3. The later Linux UI will subscribe to the same event callback
and call `inject_user_message`; it will not own provider logic or credentials.

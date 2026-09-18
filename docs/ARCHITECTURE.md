# Architecture

X-SIDECHAIN is a local Linux application. It sends the task directly to the
configured model endpoints; no X-SIDECHAIN relay or hosted backend is required.

## Deliberation protocol

For `N` configured agents:

1. One immutable first-round prompt is created.
2. `N` workers wait at a barrier, then call their providers in parallel with that
   same prompt. Provider scheduling and network latency can still differ.
3. When cross-review is enabled, every agent receives the other `N - 1` answers
   and all `N` reviews run in parallel.
4. The configured synthesis agent receives all independent answers and reviews.
5. Every completed artifact is appended to a hash-chained JSONL audit log.

The normal call count is `2N + 1`. With cross-review disabled it is `N + 1`.
This makes cost growth explicit and keeps the number of rounds bounded while the
number and identity of agents remain unrestricted.

## Extension boundaries

- `config.py`: versioned provider, authentication, agent, and run configuration.
- `auth.py`: environment credentials and official OAuth Device Flow; provider
  adapters receive only resolved header material.
- `providers/`: protocol adapters behind the small `Provider.generate` contract.
- `orchestrator.py`: provider-independent fan-out, all-peer review, and synthesis.
- `prompts.py`: shared task, review contract, and evidence gate.
- `audit.py`: append-only SHA-256 chained session records.
- `__main__.py`: configuration-first CLI and audit verification.

Adding a model that already implements one of the supported API protocols requires
only configuration. A truly different wire protocol requires one adapter plus a
factory registration; it does not require changing the orchestration algorithm.

## UI boundary

There is no UI in v0.2. This is deliberate: the provider/auth contracts and result
schema will be stabilized and tested first. The later Linux UI will call the same
orchestrator and will not own credentials, provider logic, or debate state.

## Data flow

Secrets are resolved at provider construction time and used only in request headers.
They are excluded from run configuration output and audit payloads. Provider replies,
request IDs, usage data, timing, and the final answer are retained locally for
reproducibility.

# Architecture

X-SIDECHAIN is a local Linux application. It sends the user's task directly to
the selected model providers; no X-SIDECHAIN relay or hosted backend exists.

## Protocol

1. Agent A and Agent B receive the same task independently and run in parallel.
2. Each agent receives only the other agent's initial answer and challenges it.
3. The selected synthesis agent receives all four artifacts.
4. The synthesis prompt enforces `PROVEN`, `DISPROVEN`, or `AMBIGUOUS` and keeps
   disagreements visible.
5. Every stage is appended to a hash-chained JSONL audit log.

The current release deliberately limits the exchange to one cross-examination
round. This bounds cost, latency, and context growth while still breaking the
single-answer/self-critique pattern.

## Modules

- `providers/`: provider adapters with a small common protocol.
- `orchestrator.py`: deterministic five-call workflow (2 + 2 + 1).
- `prompts.py`: role contracts and evidence gate.
- `audit.py`: append-only, SHA-256 chained session records.
- `ui.py`: native Tk Linux interface.
- `__main__.py`: GUI, headless, and audit verification entry points.

## Data flow

API keys remain in process environment variables. They are used only to create
request headers and are excluded from application configuration and audit
payloads. Provider responses, including provider request IDs and token usage,
are recorded locally for reproducibility.


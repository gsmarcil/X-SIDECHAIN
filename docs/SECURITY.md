# Security model

## Secrets

- API keys are read from `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `XAI_API_KEY`.
- Keys are never accepted as command-line options, written to configuration, or
  included in audit events.
- `.env` is ignored by Git, but exporting keys from a protected shell or a
  desktop secret manager is preferred.

## Data disclosure

The task, both agents' answers, and critiques are sent to the chosen providers.
Do not submit material that the providers are not authorized to receive.
Provider-side retention and processing are governed by each provider account.

## Audit guarantees

Audit logs make later modification detectable by chaining every record to the
SHA-256 hash of the preceding record. They do not provide authorship or an
external timestamp. A future release may sign the final chain head.

## Threat boundaries

- Model output is untrusted text. It is displayed, not executed.
- X-SIDECHAIN does not currently expose shell, browser, or filesystem tools to
  either model.
- HTTPS certificate validation uses Python's default platform trust store.
- Provider error bodies are truncated before display.


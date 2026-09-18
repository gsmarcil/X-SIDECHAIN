# Security model

## Secrets and account authentication

- API keys are read from environment-variable names declared in configuration.
- Keys and OAuth tokens are never accepted as command-line values, written to JSON
  configuration, or included in audit events.
- OAuth login is supported only through an official provider-published OAuth 2.0
  Device Authorization Grant. The provider owns email challenges, MFA, and consent.
- OAuth tokens are stored through Linux Secret Service using `secret-tool`.
- X-SIDECHAIN does not scrape login pages, automate passwords, intercept email, or
  reuse browser session cookies.

## Data disclosure

Every accepted room event, including user steering messages, is supplied to agents
when they take their next contribution. The synthesizer receives the complete
accepted room. Superseded drafts are written locally to the audit log but are not
sent to peers. Do not combine providers that are not all authorized to receive the
submitted material.

## Endpoint trust

Provider URLs and static headers are user-controlled configuration. A malicious
endpoint can receive prompts and its declared credential header. Prefer HTTPS for
remote providers. Plain HTTP is intended only for trusted local services.

## Audit guarantees

Audit logs make later modification detectable by chaining every record to the SHA-256
hash of the preceding record. They contain the task, accepted contributions, user
steering, superseded drafts, and final synthesis. They do not provide authorship or
an external timestamp.

## Threat and cost boundaries

- Model output is untrusted text and is never executed.
- Public contributions are concise reasoning summaries, not hidden chain-of-thought.
- Models receive no shell, browser, or filesystem tools from X-SIDECHAIN.
- Stale-draft rejection prevents old model output from silently overwriting a newer
  correction, but it cannot cancel a request already executing at a remote provider.
- Superseded requests may still be billed by that provider. `max_model_calls` is a
  hard local admission limit for new calls, not a provider-side spending guarantee.
- HTTPS certificate validation uses Python's default platform trust store.

# Security model

## Secrets and account authentication

- API keys are read from environment-variable names declared in configuration.
- Keys and OAuth tokens are never accepted as command-line values, written to JSON
  configuration, or included in audit events.
- OAuth account login is supported only through an official provider-published OAuth
  2.0 Device Authorization Grant. The provider owns its verification page, email
  challenge, MFA, consent, and account policy.
- OAuth tokens are stored through Linux Secret Service using `secret-tool`. Account
  login fails closed when protected storage is unavailable.
- X-SIDECHAIN does not scrape provider login pages, automate passwords, intercept
  email, or reuse browser session cookies.

## Data disclosure

The original task is sent to every selected agent. During cross-review, each agent
also receives every other agent's first answer. The selected synthesizer receives all
answers and reviews. Do not combine providers that are not all authorized to receive
the submitted material. Provider retention and processing are governed by each
provider account and policy.

## Endpoint trust

Provider base URLs and static headers are user-controlled configuration. Treat a
configuration file as security-sensitive: a malicious endpoint can receive prompts
and its declared credential header. Prefer HTTPS for remote providers. Plain HTTP is
accepted for local services such as `127.0.0.1` and should not be used across an
untrusted network.

## Audit guarantees

Audit logs make later modification detectable by chaining every record to the SHA-256
hash of the preceding record. They do not provide authorship or an external timestamp.
The logs contain prompts and model responses and must be protected accordingly.

## Threat boundaries

- Model output is untrusted text. It is printed and recorded, never executed.
- Models receive no shell, browser, or filesystem tool access from X-SIDECHAIN.
- HTTPS certificate validation uses Python's default platform trust store.
- Provider error bodies are truncated before display.
- Simultaneous release is best-effort at the application boundary; networks and
  provider queues cannot guarantee identical arrival times.

# Security model

## Information boundaries

- An agent receives the task, user updates, its role, and its own private work product.
- Peer agents receive neither another agent's `analysis.md` nor its clarification
  workspace.
- The chair receives public briefs and targeted clarification answers, not private
  analyses.
- During peer review, each reviewer receives the chair draft plus its own private
  analysis and brief.
- Private notes are still available to the local user and are included in the local
  audit record. “Private” means private from other model calls, not encrypted from
  the machine owner.

## Local workspace

Workspace directories use `0700`; files use `0600`. The session directory and the
audit directory (`~/.local/share/x-sidechain/sessions`) are also `0700`, and audit
logs are created `0600`: they contain the task text and every model reply, so they
must not inherit a world-readable umask. Agent IDs are validated before
being used as path components, and relative workspace writes reject `..` and absolute
paths. This is not yet an OS sandbox: agents currently have no tools and cannot access
the filesystem directly. Future tools must be confined to the owning agent directory.

## Secrets and account authentication

- API keys come from environment variables named in configuration.
- A provider `base_url` (or OAuth endpoint) on cleartext `http://` is rejected unless
  the host is loopback, because the API key and the task would cross the network in
  the clear. Set `"allow_insecure_http": true` on that provider to accept the risk
  deliberately, for example for a trusted host on a private link.
- Keys and OAuth tokens are excluded from configuration output, workspaces, and audit
  payloads.
- OAuth is allowed only through provider-published Device Authorization Grant values.
- OAuth tokens are stored through Linux Secret Service using `secret-tool`.
- X-SIDECHAIN never automates passwords, scrapes login pages, or reads email.

## Data disclosure

The task and user updates go to every provider selected by an agent. Public briefs,
clarifications, draft, and reviews are routed according to the chaired protocol.
Do not combine providers that are not all authorized to receive the task material.

## Audit and cost boundaries

- Audit chaining detects later modification but does not prove authorship or provide
  an external timestamp. Reopening an existing log continues its chain; a log that
  already fails verification is never extended.
- Superseded revision work remains in local workspaces and audit logs.
- An abstaining agent contributes no evidence. The chair is instructed never to read
  an absence as agreement, and every abstention is recorded with its cause.
- Model output is untrusted text and is never executed.
- `max_model_calls` prevents the application from starting calls beyond the session
  budget; calls already accepted by a provider may still be billed. Transport retries
  (429 and 5xx) do not consume budget but may be billed by the provider.
- Aggregated provider-reported token usage is written to `session.completed` and
  printed with the run summary.
- HTTPS uses Python's default certificate validation.

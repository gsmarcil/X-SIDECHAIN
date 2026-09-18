# X-SIDECHAIN

X-SIDECHAIN is a provider-agnostic, multi-agent deliberation engine for Linux.
One prompt is broadcast to every configured agent, the agents reason independently,
then optionally review every peer before one selected agent produces the joint result.

The core is alpha software. Model output remains untrusted, and agreement between
models is never treated as proof. A graphical interface is intentionally deferred
until the configuration, authentication, and deliberation contracts are stable.

## What v0.2 supports

- Any number of agents from two upward; there is no two-agent or three-agent cap.
- Arbitrary model identifiers and configurable providers.
- OpenAI Responses-compatible, Chat Completions-compatible, and Anthropic
  Messages-compatible endpoints.
- API-key authentication, no-auth local endpoints, and standards-based OAuth 2.0
  Device Authorization Grant when the provider officially exposes it.
- A synchronized first round: the exact same generated user prompt is released to
  every selected agent only after all workers are ready.
- Parallel all-peer cross-review and a configurable final synthesizer.
- Tamper-evident local JSONL audit trails and offline verification.

Provider compatibility depends on an official API matching one of the implemented
protocol adapters. A provider-specific adapter can be added without changing the
orchestrator.

## Install from source

Requirements: Linux and Python 3.11+.

```bash
git clone https://github.com/gsmarcil/X-SIDECHAIN.git
cd X-SIDECHAIN
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cp x-sidechain.example.json x-sidechain.json
```

Edit `x-sidechain.json`: add providers, then add as many agents as needed. The
`model` value is not selected from a hard-coded list.

Export the credentials referenced by your configuration. For the included example:

```bash
export OPENAI_API_KEY='...'
export ANTHROPIC_API_KEY='...'
export XAI_API_KEY='...'
export OPENROUTER_API_KEY='...'
```

Only agents listed under `agents` are called, so unused example providers do not
require credentials.

## Validate and run

```bash
x-sidechain validate-config --config x-sidechain.json
x-sidechain providers --config x-sidechain.json
x-sidechain run --config x-sidechain.json --prompt 'Analyze this claim and identify the decisive evidence.'
```

A long prompt can be read from a UTF-8 file instead:

```bash
x-sidechain run --config x-sidechain.json --prompt-file task.md
```

The final answer is printed to the terminal. Session metadata and every model
artifact are recorded in the audit path printed by the command.

## Authentication modes

`api_key` reads a secret from the configured environment variable. `none` is for
trusted local endpoints such as Ollama. `oauth_device` is available only when a
provider publishes an official OAuth Device Flow and supplies its device/token
endpoints and a client ID:

```bash
x-sidechain auth login PROVIDER_ID --config x-sidechain.json
```

The command opens the provider's official verification page. Any email, approval,
or account challenge is performed by the provider on that page; X-SIDECHAIN never
scrapes login pages or reads account email. OAuth tokens are stored in Linux Secret
Service through `secret-tool`, never in the JSON configuration.

## Verify an audit trail

```bash
x-sidechain verify ~/.local/share/x-sidechain/sessions/SESSION_ID.jsonl
```

A successful verification prints the event count and final chain hash.

## Development

```bash
make check
```

See [Architecture](docs/ARCHITECTURE.md),
[Configuration](docs/CONFIGURATION.md), and [Security model](docs/SECURITY.md).

## Roadmap

- Stabilize provider, authentication, and run-result interfaces.
- Add streaming, cancellation, provider concurrency limits, and retry policy.
- Build the Linux UI only after the core interfaces become stable.
- Add signed audit exports, evidence attachments, `.deb`, and AppImage artifacts.

# X-SIDECHAIN

X-SIDECHAIN is a provider-agnostic live discussion room for AI agents on Linux.
Agents do not disappear into separate long answers and compare them afterward.
They publish short proposals, corrections, challenges, and evidence requests into
one shared event stream while the discussion is running.

The core is alpha software. Model output remains untrusted, and agreement between
models is never treated as proof. A graphical interface is intentionally deferred
until the live-room, authentication, and provider contracts are stable.

## What v0.3 supports

- Any number of agents from two upward; there is no two-agent or three-agent cap.
- Arbitrary model identifiers and configurable providers.
- A live shared room whose accepted events are immediately visible to every agent.
- User corrections and additions while agents or the final synthesizer are working.
- Provider-neutral mid-run steering: a draft based on an old room version is audited,
  rejected, and regenerated against the new state before it can be published.
- Fair contribution quotas, a configurable model-call budget, and a selected final
  synthesizer.
- OpenAI Responses-compatible, Chat Completions-compatible, and Anthropic
  Messages-compatible endpoints.
- API-key authentication, no-auth local endpoints, and standards-based OAuth 2.0
  Device Authorization Grant when the provider officially exposes it.
- Tamper-evident local JSONL audit trails and offline verification.

The room exchanges concise public reasoning summaries. It neither requests nor
claims access to a model's hidden chain-of-thought. Native provider steering can be
added as a capability optimization; stale-draft rejection is the universal fallback.

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

Export only the credentials referenced by the selected agents. For example:

```bash
export OPENAI_API_KEY='...'
export ANTHROPIC_API_KEY='...'
export XAI_API_KEY='...'
export OPENROUTER_API_KEY='...'
```

## Validate and run

```bash
x-sidechain validate-config --config x-sidechain.json
x-sidechain providers --config x-sidechain.json
x-sidechain run --config x-sidechain.json --prompt 'Analyze this claim and identify the decisive evidence.'
```

To join the discussion while it is running:

```bash
x-sidechain run --interactive --config x-sidechain.json --prompt 'Test the original hypothesis.'
```

Type any correction or additional idea and press Enter. It becomes a `user.steering`
event immediately. In-flight drafts based on the previous room state cannot be
accepted. Type `/finish` to stop requesting more agent contributions and synthesize
from the accepted room state.

A long initial prompt can be read from a UTF-8 file using `--prompt-file task.md`.
The final answer is printed to the terminal, and every accepted or superseded
artifact is recorded in the audit path printed by the command.

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

## Development

```bash
make check
```

See [Architecture](docs/ARCHITECTURE.md),
[Configuration](docs/CONFIGURATION.md), and [Security model](docs/SECURITY.md).

## Roadmap

- Add native WebSocket mid-turn steering adapters where providers support it.
- Add streaming, request cancellation, retry policy, and context compaction.
- Build the Linux UI only after the live-room interfaces become stable.
- Add signed audit exports, evidence attachments, `.deb`, and AppImage artifacts.

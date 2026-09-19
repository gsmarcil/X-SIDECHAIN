# X-SIDECHAIN

X-SIDECHAIN is a provider-agnostic, chaired AI team for Linux. Each agent analyzes
the task in a private file-backed workspace and publishes only a compact brief. A
designated chair asks targeted clarifying questions, writes a provisional result,
collects peer reviews, and then issues the final joint decision.

The core is alpha software. Model output remains untrusted, and agreement is never
treated as proof. A graphical interface is intentionally deferred until the chaired
workflow, authentication, and provider contracts are stable.

## What v0.4 supports

- Any number of agents from two upward and arbitrary model identifiers.
- A private workspace per agent and per task revision, with local `0700` directories
  and `0600` files.
- Parallel private analysis followed by a bounded public summary from each agent.
- A configurable chair that reads summaries, not other agents' private notes.
- Optional questions directed only to the specific agent whose brief is ambiguous.
- A chair draft, parallel peer review, and evidence-gated final result.
- User corrections while the team is working. A correction increments the task
  revision and restarts the affected chaired cycle instead of mixing two states.
- A hard model-call budget, aggregated token usage, and a tamper-evident JSONL audit
  trail written with owner-only permissions.
- Transient provider failures (429, 5xx, connection resets) retried with backoff
  without consuming model-call budget.
- Quorum-based resilience: one failing provider abstains and is recorded instead of
  ending a session the other agents already paid for. The provider's own error text
  stays in that agent's private workspace and never reaches another vendor's model.
- Bounded restarts, an optional session deadline, and an enforced public-brief size,
  so a long session still ends with a result.
- Responses-compatible, Chat Completions-compatible, and Anthropic
  Messages-compatible endpoints.
- ChatGPT account access through the official Codex CLI. X-SIDECHAIN invokes
  Codex ephemerally with execution and retrieval tools disabled, and never reads
  its credential file.
- API keys, trusted local no-auth endpoints, and official OAuth Device Flow where a
  provider exposes it.

The private workspace contains an auditable work product—claims, evidence,
uncertainty, and tests—not hidden chain-of-thought. Isolation currently means that
other model prompts never receive those files. It is not yet a tool-execution sandbox.

## Install

Requirements: Linux and Python 3.11+. There are no other runtime dependencies:
the tool uses the Python standard library only.

**Debian or Ubuntu**

```bash
sudo dpkg -i x-sidechain_0.5.0_all.deb
```

This installs the `x-sidechain` command, its manual page, and a desktop entry,
so the interface can also be started from the applications menu.

**Any distribution, with pipx**

```bash
pipx install git+https://github.com/gsmarcil/X-SIDECHAIN.git
```

**From a clone, to work on the code**

```bash
git clone https://github.com/gsmarcil/X-SIDECHAIN.git
cd X-SIDECHAIN
./install.sh
```

The script checks the prerequisites, builds `.venv`, installs the package, and
verifies the result. It asks for no privileges and installs no system packages.
[INSTALL.md](INSTALL.md) covers the options, a step-by-step manual install, and
what to do when something fails.

Then create a configuration:

```bash
mkdir -p ~/.config/x-sidechain
cp x-sidechain.example.json ~/.config/x-sidechain/config.json
```

`x-sidechain ui` loads that file when it is started without `--config`, which is
what the desktop entry does. The `run` command always takes an explicit
`--config`, so no session starts by accident.

Edit that file to define providers and agents. A model name is passed to the
provider unchanged; it is not selected from a hard-coded catalog.

## Run

```bash
x-sidechain validate-config --config x-sidechain.json
x-sidechain run --config x-sidechain.json --prompt 'Test this claim and identify decisive evidence.'
```

To add a fact or correction while the team is working:

```bash
x-sidechain run --interactive --config x-sidechain.json --prompt 'Test the original hypothesis.'
```

Type the update and press Enter. `/finish` closes further keyboard input and lets
the current chaired workflow finish; the workflow is already bounded and does not
need `/finish` to complete.

The command prints the final result plus two paths:

- `model_calls` and `usage_totals`: what the session actually spent.
- `abstentions`: any agent that did not report, and why.
- `audit_path`: hash-chained session record.
- `workspace_root`: private analyses, public briefs, clarifications, draft, reviews,
  and final result, grouped by revision and agent.

## Local interface

```bash
x-sidechain ui --config x-sidechain.json
```

The command prints a loopback address carrying a one-off token and opens it. The
page starts a session, streams every public event as it happens, sends a
correction to the whole room, and closes input — the same chaired workflow the
`run` command drives, with the same spend and abstentions on screen.

Without `--config` the page still serves, but shows an explicit disconnected/empty
state that cannot reach the engine. With a config, provider status, configured agents,
the current session, and completed workspace paths come from the local API. Persistent
history, folder mounting, GitHub connection, file upload, and private agent messaging
are not available yet and are labelled as such. The server binds loopback only,
refuses a request whose `Host` or `Origin` is not this address, and never puts a
secret's value in a response:
the interface is told a variable's name and whether it is set, nothing more.

## Authentication

For OpenAI, a ChatGPT account can be the primary path instead of entering an API
key. Install the official [Codex CLI](https://learn.chatgpt.com/docs/auth), copy
the account-based example, then authenticate once:

```bash
cp x-sidechain.chatgpt.example.json x-sidechain.json
x-sidechain auth login openai-chatgpt --config x-sidechain.json
x-sidechain auth status openai-chatgpt --config x-sidechain.json
```

The normal login opens OpenAI's browser flow. On a headless device, add
`--device-auth`. Codex owns, stores, and refreshes the account credential;
X-SIDECHAIN asks `codex login status` for a yes/no state and never reads
`~/.codex/auth.json`.

This account route is deliberately separate from the OpenAI Platform API.
`api_key` still reads a secret from the configured environment variable. `none` is for
trusted local endpoints. `oauth_device` is available only when a provider publishes
an official OAuth Device Flow:

```bash
x-sidechain auth login PROVIDER_ID --config x-sidechain.json
```

Email, MFA, consent, and account challenges remain on the provider's official page.
X-SIDECHAIN never scrapes login pages or reads email. OAuth tokens for generic
providers are stored through Linux Secret Service using `secret-tool`.

## Build the Debian package

```bash
./packaging/build-deb.sh          # writes dist/x-sidechain_VERSION_all.deb
```

The package is architecture independent and depends only on `python3 (>= 3.11)`.
It carries the command, the interface assets, the manual page, the desktop entry,
and icons from 16 to 256 pixels.

## Development and audit verification

```bash
make check
x-sidechain verify ~/.local/share/x-sidechain/sessions/SESSION_ID.jsonl
```

See [Architecture](docs/ARCHITECTURE.md),
[Configuration](docs/CONFIGURATION.md), and [Security model](docs/SECURITY.md).

## Roadmap

- Add sandboxed tools scoped to each agent workspace.
- Add native streaming and mid-turn steering where providers support it.
- Add context compaction and richer artifact manifests.
- Add signed exports and an AppImage artifact.
- Publish to PyPI so `pipx install x-sidechain` works without a git URL.

## License

Apache License 2.0. See [LICENSE](LICENSE).

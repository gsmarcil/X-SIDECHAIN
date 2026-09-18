# X-SIDECHAIN

X-SIDECHAIN is a Linux desktop application that makes two models from different
providers reason independently, cross-examine one another, and produce one
evidence-gated decision. It currently supports OpenAI, Anthropic, and xAI.

The application is alpha software. Model output remains untrusted and agreement
between models is never treated as proof.

## Current vertical slice

- Native Linux GUI with no runtime Python dependencies beyond Tk.
- OpenAI Responses API, Anthropic Messages API, and xAI Responses API.
- Independent parallel analysis followed by parallel cross-examination.
- Configurable final synthesizer.
- Evidence-first security research roles.
- Tamper-evident local JSONL audit trail.
- Headless mode and offline audit verification.

## Run from source

Requirements: Linux, Python 3.11+, and Tk 8.6+.

```bash
git clone https://github.com/gsmarcil/X-SIDECHAIN.git
cd X-SIDECHAIN
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Export only the keys for the providers you select:

```bash
export OPENAI_API_KEY='...'
export ANTHROPIC_API_KEY='...'
export XAI_API_KEY='...'
x-sidechain
```

Model names are editable in the interface. Optional defaults can be set with
`OPENAI_MODEL`, `ANTHROPIC_MODEL`, and `XAI_MODEL`.

## Headless mode

```bash
x-sidechain \
  --agent-a openai:gpt-6-astra \
  --agent-b anthropic:claude-sonnet-5 \
  --synthesizer a \
  --prompt 'Test whether endpoint X accepts a foreign object Y.'
```

## Verify an audit trail

```bash
x-sidechain --verify ~/.local/share/x-sidechain/sessions/SESSION_ID.jsonl
```

A successful verification prints the event count and final chain hash.

## Development

```bash
make check
```

See [Architecture](docs/ARCHITECTURE.md) and [Security model](docs/SECURITY.md).

## Roadmap

- Secret Service/libsecret integration.
- Streaming tokens and cancellable runs.
- Signed audit exports and evidence attachments.
- `.deb` and AppImage release artifacts.
- Configurable protocols and bounded additional debate rounds.

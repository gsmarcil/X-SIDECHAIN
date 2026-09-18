# Configuration

Configuration is JSON with `version: 1`:

- `providers` define wire protocol, endpoint, and authentication.
- `agents` select arbitrary provider/model pairs and roles.
- `chair` names the agent that moderates and produces the final decision.

At least two agents are required.

## Provider protocols

| Protocol | Endpoint | Typical use |
| --- | --- | --- |
| `responses` | `base_url/responses` | Responses-compatible APIs |
| `chat_completions` | `base_url/chat/completions` | Chat-compatible and local servers |
| `anthropic_messages` | `base_url/messages` | Anthropic Messages-compatible APIs |

## Example provider

```json
{
  "protocol": "chat_completions",
  "base_url": "https://example.com/v1",
  "auth": {
    "type": "api_key",
    "env": "EXAMPLE_API_KEY",
    "header": "Authorization",
    "scheme": "Bearer"
  },
  "headers": {"X-Optional-Header": "value"},
  "max_output_tokens": 4096
}
```

Set `auth.type` to `none` for a trusted local service. For official OAuth Device
Flow, provide `device_authorization_url`, `token_url`, `client_id`, and optional
`scopes`, then run `x-sidechain auth login PROVIDER_ID --config x-sidechain.json`.

## Team and chair

```json
{
  "agents": [
    {"id": "explorer", "provider": "provider-one", "model": "any-model-id", "role": "Expand the attack surface"},
    {"id": "validator", "provider": "provider-two", "model": "another-model-id", "role": "Kill unsupported claims"},
    {"id": "evidence", "provider": "local", "model": "local-model", "role": "Check observable artifacts"}
  ],
  "chair": "validator",
  "max_clarification_questions": 3,
  "max_model_calls": 42,
  "request_timeout_seconds": 600,
  "min_agent_quorum": 2,
  "max_revisions": 8,
  "session_deadline_seconds": 3600,
  "max_public_brief_chars": 1200
}
```

Agent IDs must be 1–64 ASCII letters, digits, dots, underscores, or hyphens. The
same model may back multiple agents.

`max_clarification_questions` is between 0 and 20. The minimum call budget is
`3N + min(Q, N) + 2`. If `max_model_calls` is omitted, X-SIDECHAIN reserves three
complete worst-case cycles so user corrections can safely restart work.

## Resilience and bounds

| Key | Default | Range | Meaning |
| --- | --- | --- | --- |
| `min_agent_quorum` | `2` | 2 to N | Briefs required before the chair may proceed. Agents whose provider fails abstain; the run continues while the quorum holds. The chair itself is never optional. |
| `max_revisions` | `8` | 0 to 100 | How many user corrections a session accepts. Each one restarts the chaired cycle, so this caps the restart loop. `0` disables live corrections. |
| `session_deadline_seconds` | unset | `request_timeout_seconds` to 86400 | Wall-clock budget. Past the deadline no correction is accepted and no cycle restarts; the running cycle still finishes, so a session always yields a result. |
| `max_public_brief_chars` | `1200` | 200 to 20000 | Hard limit on a public brief. A longer brief is cut at a line boundary, marked, and the full text kept in the agent's own workspace as `summary.full.md`. |

A correction is also refused when the remaining call budget could not fund a whole
restarted cycle. Refusing it is deliberate: the alternative is spending the rest of
the budget on a cycle that cannot finish, ending the session with nothing.

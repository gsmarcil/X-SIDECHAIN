# Configuration

Configuration is JSON with `version: 1`:

- `providers` describe API protocol, endpoint, and authentication.
- `agents` select any provider/model pair and give that participant a role.

There is no model catalog or fixed agent count. At least two agents and one valid
`synthesizer` agent ID are required.

## Provider protocols

| Protocol | Endpoint appended to `base_url` | Typical use |
| --- | --- | --- |
| `responses` | `/responses` | OpenAI-compatible Responses APIs |
| `chat_completions` | `/chat/completions` | OpenAI-compatible chat APIs and local servers |
| `anthropic_messages` | `/messages` | Anthropic Messages-compatible APIs |

Model names are passed unchanged. Compatibility means wire-protocol compatibility,
not merely that a service calls itself “OpenAI compatible.”

## API key provider

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

Set `scheme` to an empty string for raw key headers such as `x-api-key`.

## Local no-auth provider

```json
{
  "protocol": "chat_completions",
  "base_url": "http://127.0.0.1:11434/v1",
  "auth": {"type": "none"}
}
```

## Official OAuth Device Flow

Use this only with values published by the provider and a client ID authorized for
your application:

```json
{
  "protocol": "chat_completions",
  "base_url": "https://api.example.com/v1",
  "auth": {
    "type": "oauth_device",
    "header": "Authorization",
    "scheme": "Bearer",
    "device_authorization_url": "https://identity.example.com/oauth/device/code",
    "token_url": "https://identity.example.com/oauth/token",
    "client_id": "OFFICIAL_CLIENT_ID",
    "scopes": ["model.invoke"]
  }
}
```

Then run `x-sidechain auth login PROVIDER_ID --config x-sidechain.json`.

## Agents and live-room limits

```json
{
  "agents": [
    {"id": "a", "provider": "provider-one", "model": "any-model-id", "role": "Attack-surface explorer"},
    {"id": "b", "provider": "provider-two", "model": "another-model-id", "role": "Claim validator"},
    {"id": "c", "provider": "local", "model": "local-model", "role": "Evidence checker"}
  ],
  "synthesizer": "b",
  "contributions_per_agent": 2,
  "max_model_calls": 28,
  "request_timeout_seconds": 600
}
```

`contributions_per_agent` is between 1 and 20. `max_model_calls` is the hard session
budget, including superseded drafts and synthesis. Its minimum is
`C × N × (N + 1) / 2 + 1`; omitting it selects that minimum plus capacity for five
full-room steering invalidations. The same provider/model may back multiple agents,
but agent IDs must be unique.

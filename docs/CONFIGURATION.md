# Configuration

Configuration is JSON with `version: 1`. It has two independent layers:

- `providers` describe API protocol, endpoint, and authentication.
- `agents` select any provider/model pair and give that agent a role.

There is no model catalog or fixed agent count. At least two agents and one valid
`synthesizer` agent ID are required.

## Provider protocols

| Protocol | Endpoint appended to `base_url` | Typical use |
| --- | --- | --- |
| `responses` | `/responses` | OpenAI-compatible Responses APIs |
| `chat_completions` | `/chat/completions` | OpenAI-compatible chat APIs and local servers |
| `anthropic_messages` | `/messages` | Anthropic Messages-compatible APIs |

Model names are passed unchanged to the endpoint. Compatibility means API protocol
compatibility, not merely that a service calls itself “OpenAI compatible.” Validate
a new endpoint with a non-sensitive prompt before using real data.

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

Then run `x-sidechain auth login PROVIDER_ID --config x-sidechain.json`. An optional
`token_env` can supply a short-lived token without writing it to configuration.

## Agents

```json
{
  "agents": [
    {"id": "a", "provider": "provider-one", "model": "any-model-id", "role": "Independent analyst"},
    {"id": "b", "provider": "provider-two", "model": "another-model-id", "role": "Adversarial reviewer"},
    {"id": "c", "provider": "local", "model": "local-model", "role": "Evidence checker"}
  ],
  "synthesizer": "b",
  "cross_review": true,
  "request_timeout_seconds": 600
}
```

The same provider and model may be used by multiple agents with different roles.
Agent IDs must be unique.

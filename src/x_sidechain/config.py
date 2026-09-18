from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from x_sidechain.models import AgentSpec
from x_sidechain.workspace import validate_agent_id


SUPPORTED_PROTOCOLS = {"responses", "chat_completions", "anthropic_messages"}
SUPPORTED_AUTH = {"none", "api_key", "oauth_device"}


@dataclass(frozen=True)
class AuthConfig:
    type: str
    env: str | None = None
    header: str = "Authorization"
    scheme: str = "Bearer"
    device_authorization_url: str | None = None
    token_url: str | None = None
    client_id: str | None = None
    scopes: tuple[str, ...] = ()
    token_env: str | None = None


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    protocol: str
    base_url: str
    auth: AuthConfig
    headers: dict[str, str] = field(default_factory=dict)
    max_output_tokens: int = 4096


@dataclass(frozen=True)
class RunConfig:
    providers: dict[str, ProviderConfig]
    agents: tuple[AgentSpec, ...]
    chair: str
    request_timeout_seconds: int = 600
    max_clarification_questions: int = 4
    max_model_calls: int | None = None
    min_agent_quorum: int = 2
    max_revisions: int = 8
    session_deadline_seconds: int | None = None
    max_public_brief_chars: int = 1200


def _required_string(raw: dict[str, Any], key: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def _auth_config(raw: Any, context: str) -> AuthConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"{context}.auth must be an object")
    auth_type = _required_string(raw, "type", f"{context}.auth")
    if auth_type not in SUPPORTED_AUTH:
        raise ValueError(f"{context}.auth.type is unsupported: {auth_type}")
    scopes = raw.get("scopes", [])
    if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
        raise ValueError(f"{context}.auth.scopes must be a string array")
    for key in (
        "env",
        "header",
        "scheme",
        "device_authorization_url",
        "token_url",
        "client_id",
        "token_env",
    ):
        if key in raw and not isinstance(raw[key], str):
            raise ValueError(f"{context}.auth.{key} must be a string")
    config = AuthConfig(
        type=auth_type,
        env=raw.get("env"),
        header=str(raw.get("header", "Authorization")),
        scheme=str(raw.get("scheme", "Bearer")),
        device_authorization_url=raw.get("device_authorization_url"),
        token_url=raw.get("token_url"),
        client_id=raw.get("client_id"),
        scopes=tuple(scopes),
        token_env=raw.get("token_env"),
    )
    if auth_type == "api_key" and not config.env:
        raise ValueError(f"{context}.auth.env is required for api_key")
    if auth_type == "oauth_device":
        missing = [
            name
            for name in ("device_authorization_url", "token_url", "client_id")
            if not getattr(config, name)
        ]
        if missing:
            raise ValueError(f"{context}.auth is missing OAuth fields: {', '.join(missing)}")
        _validate_url(str(config.device_authorization_url), f"{context}.auth.device_authorization_url")
        _validate_url(str(config.token_url), f"{context}.auth.token_url")
    return config


def _validate_url(value: str, context: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{context} must be an absolute HTTP(S) URL")
    return value.rstrip("/")


def _bounded_int(
    raw: dict[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def load_config(path: str | Path) -> RunConfig:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read config: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON config at line {exc.lineno}: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ValueError("config root must be an object")
    if raw.get("version") != 1:
        raise ValueError("config.version must be 1")

    providers_raw = raw.get("providers")
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise ValueError("config.providers must be a non-empty object")
    providers: dict[str, ProviderConfig] = {}
    for provider_id, item in providers_raw.items():
        context = f"providers.{provider_id}"
        if not isinstance(provider_id, str) or not provider_id.strip() or not isinstance(item, dict):
            raise ValueError("every provider must have a non-empty ID and object value")
        protocol = _required_string(item, "protocol", context)
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ValueError(f"{context}.protocol is unsupported: {protocol}")
        headers = item.get("headers", {})
        if not isinstance(headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
        ):
            raise ValueError(f"{context}.headers must be a string map")
        max_tokens_raw = item.get("max_output_tokens", 4096)
        if not isinstance(max_tokens_raw, int) or isinstance(max_tokens_raw, bool):
            raise ValueError(f"{context}.max_output_tokens must be an integer")
        max_tokens = max_tokens_raw
        if max_tokens < 1:
            raise ValueError(f"{context}.max_output_tokens must be positive")
        providers[provider_id] = ProviderConfig(
            id=provider_id,
            protocol=protocol,
            base_url=_validate_url(_required_string(item, "base_url", context), f"{context}.base_url"),
            auth=_auth_config(item.get("auth", {"type": "none"}), context),
            headers=dict(headers),
            max_output_tokens=max_tokens,
        )

    agents_raw = raw.get("agents")
    if not isinstance(agents_raw, list) or len(agents_raw) < 2:
        raise ValueError("config.agents must contain at least two agents")
    agents: list[AgentSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(agents_raw):
        context = f"agents[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{context} must be an object")
        agent_id = _required_string(item, "id", context)
        validate_agent_id(agent_id)
        provider_id = _required_string(item, "provider", context)
        if agent_id in seen:
            raise ValueError(f"duplicate agent id: {agent_id}")
        if provider_id not in providers:
            raise ValueError(f"{context}.provider does not exist: {provider_id}")
        seen.add(agent_id)
        agents.append(
            AgentSpec(
                id=agent_id,
                provider=provider_id,
                model=_required_string(item, "model", context),
                role=_required_string(item, "role", context),
            )
        )

    if "chair" in raw and "synthesizer" in raw:
        raise ValueError("use config.chair only; do not set both chair and legacy synthesizer")
    chair_key = "chair" if "chair" in raw else "synthesizer"
    chair = _required_string(raw, chair_key, "config")
    if chair not in seen:
        raise ValueError("config.chair must reference an agent id")
    timeout_raw = raw.get("request_timeout_seconds", 600)
    if not isinstance(timeout_raw, int) or isinstance(timeout_raw, bool):
        raise ValueError("request_timeout_seconds must be an integer")
    timeout = timeout_raw
    if timeout < 1 or timeout > 3600:
        raise ValueError("request_timeout_seconds must be between 1 and 3600")
    clarification_raw = raw.get("max_clarification_questions", 4)
    if not isinstance(clarification_raw, int) or isinstance(clarification_raw, bool):
        raise ValueError("max_clarification_questions must be an integer")
    if clarification_raw < 0 or clarification_raw > 20:
        raise ValueError("max_clarification_questions must be between 0 and 20")
    max_calls_raw = raw.get("max_model_calls")
    if max_calls_raw is not None:
        if not isinstance(max_calls_raw, int) or isinstance(max_calls_raw, bool):
            raise ValueError("max_model_calls must be an integer")
        minimum_calls = 3 * len(agents) + min(clarification_raw, len(agents)) + 2
        if max_calls_raw < minimum_calls:
            raise ValueError(
                f"max_model_calls must be at least {minimum_calls} for this configuration"
            )
    quorum = _bounded_int(raw, "min_agent_quorum", 2, 2, len(agents))
    max_revisions = _bounded_int(raw, "max_revisions", 8, 0, 100)
    brief_chars = _bounded_int(raw, "max_public_brief_chars", 1200, 200, 20000)
    deadline_raw = raw.get("session_deadline_seconds")
    if deadline_raw is not None:
        if not isinstance(deadline_raw, int) or isinstance(deadline_raw, bool):
            raise ValueError("session_deadline_seconds must be an integer")
        if deadline_raw < timeout:
            raise ValueError(
                "session_deadline_seconds must be at least request_timeout_seconds; "
                "a deadline shorter than one call can never be met"
            )
        if deadline_raw > 86_400:
            raise ValueError("session_deadline_seconds must be at most 86400")
    return RunConfig(
        providers=providers,
        agents=tuple(agents),
        chair=chair,
        request_timeout_seconds=timeout,
        max_clarification_questions=clarification_raw,
        max_model_calls=max_calls_raw,
        min_agent_quorum=quorum,
        max_revisions=max_revisions,
        session_deadline_seconds=deadline_raw,
        max_public_brief_chars=brief_chars,
    )

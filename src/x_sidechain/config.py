from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


PROVIDER_DEFAULTS = {
    "openai": os.getenv("OPENAI_MODEL", "gpt-6-astra"),
    "anthropic": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
    "xai": os.getenv("XAI_MODEL", "grok-4.6"),
}

PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
}


@dataclass
class AppConfig:
    agent_a_provider: str = "openai"
    agent_a_model: str = PROVIDER_DEFAULTS["openai"]
    agent_b_provider: str = "anthropic"
    agent_b_model: str = PROVIDER_DEFAULTS["anthropic"]
    synthesizer: str = "a"
    request_timeout_seconds: int = 600


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path.home() / ".config" / "x-sidechain" / "config.json"

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return AppConfig()
        allowed = AppConfig.__dataclass_fields__.keys()
        return AppConfig(**{key: value for key, value in raw.items() if key in allowed})

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # API keys are deliberately absent from AppConfig and can never be persisted here.
        self.path.write_text(
            json.dumps(asdict(config), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.path.chmod(0o600)


def api_key_for(provider: str) -> str:
    try:
        variable = PROVIDER_KEY_ENV[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported provider: {provider}") from exc
    value = os.getenv(variable, "").strip()
    if not value:
        raise RuntimeError(f"Missing {variable}; export it before starting X-SIDECHAIN")
    return value


def provider_status(provider: str) -> str:
    variable = PROVIDER_KEY_ENV[provider]
    return "configured" if os.getenv(variable, "").strip() else f"missing {variable}"

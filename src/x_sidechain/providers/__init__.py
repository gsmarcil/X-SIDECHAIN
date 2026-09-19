from __future__ import annotations

from x_sidechain.auth import TokenStore, resolve_auth
from x_sidechain.config import ProviderConfig
from x_sidechain.providers.anthropic import AnthropicProvider
from x_sidechain.providers.base import Provider
from x_sidechain.providers.codex_cli import CodexCLIProvider
from x_sidechain.providers.openai_compatible import OpenAICompatibleProvider


def create_provider(
    config: ProviderConfig,
    model: str,
    timeout: int = 600,
    token_store: TokenStore | None = None,
) -> Provider:
    if config.protocol == "codex_cli":
        return CodexCLIProvider(config, model, timeout)
    auth = resolve_auth(config, token_store)
    if config.protocol in {"responses", "chat_completions"}:
        return OpenAICompatibleProvider(config, model, auth, timeout)
    if config.protocol == "anthropic_messages":
        return AnthropicProvider(config, model, auth, timeout)
    raise ValueError(f"Unsupported provider protocol: {config.protocol}")


__all__ = ["Provider", "create_provider"]

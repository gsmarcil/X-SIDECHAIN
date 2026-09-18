from __future__ import annotations

from x_sidechain.config import api_key_for
from x_sidechain.providers.anthropic import AnthropicProvider
from x_sidechain.providers.base import Provider
from x_sidechain.providers.openai_compatible import ResponsesProvider


def create_provider(name: str, model: str, timeout: int = 600) -> Provider:
    key = api_key_for(name)
    if name == "openai":
        return ResponsesProvider(
            name="openai",
            model=model,
            api_key=key,
            endpoint="https://api.openai.com/v1/responses",
            timeout=timeout,
        )
    if name == "xai":
        return ResponsesProvider(
            name="xai",
            model=model,
            api_key=key,
            endpoint="https://api.x.ai/v1/responses",
            timeout=timeout,
            system_as_message=True,
        )
    if name == "anthropic":
        return AnthropicProvider(model=model, api_key=key, timeout=timeout)
    raise ValueError(f"Unsupported provider: {name}")


__all__ = ["Provider", "create_provider"]


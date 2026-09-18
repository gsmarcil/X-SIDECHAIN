from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from x_sidechain.auth import AuthMaterial
from x_sidechain.config import ProviderConfig
from x_sidechain.http import post_json
from x_sidechain.models import ModelReply

Transport = Callable[[str, dict[str, str], dict[str, Any], int], dict[str, Any]]


class AnthropicProvider:
    def __init__(
        self,
        config: ProviderConfig,
        model: str,
        auth: AuthMaterial,
        timeout: int = 600,
        transport: Transport = post_json,
    ) -> None:
        self.name = config.id
        self.model = model
        self._config = config
        self._auth = auth
        self._timeout = timeout
        self._transport = transport

    def generate(self, system: str, prompt: str) -> ModelReply:
        headers = self._auth.apply(
            {
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                **self._config.headers,
            }
        )
        started = time.monotonic()
        data = self._transport(
            f"{self._config.base_url}/messages",
            headers,
            {
                "model": self.model,
                "max_tokens": self._config.max_output_tokens,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
            self._timeout,
        )
        chunks = [
            item["text"]
            for item in data.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        ]
        if not chunks:
            raise RuntimeError("Anthropic Messages-compatible provider returned no text")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return ModelReply(
            provider=self.name,
            model=self.model,
            text="\n".join(chunks).strip(),
            request_id=str(data["id"]) if data.get("id") else None,
            usage=usage,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from x_sidechain.http import post_json
from x_sidechain.models import ModelReply

Transport = Callable[[str, dict[str, str], dict[str, Any], int], dict[str, Any]]


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout: int = 600,
        transport: Transport = post_json,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    def generate(self, system: str, prompt: str) -> ModelReply:
        started = time.monotonic()
        data = self._transport(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            {
                "model": self.model,
                "max_tokens": 4096,
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
            raise RuntimeError("Anthropic response contained no text")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return ModelReply(
            provider=self.name,
            model=self.model,
            text="\n".join(chunks).strip(),
            request_id=str(data["id"]) if data.get("id") else None,
            usage=usage,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


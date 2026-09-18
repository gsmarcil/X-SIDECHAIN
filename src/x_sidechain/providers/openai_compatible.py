from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from x_sidechain.http import post_json
from x_sidechain.models import ModelReply

Transport = Callable[[str, dict[str, str], dict[str, Any], int], dict[str, Any]]


def _response_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks: list[str] = []
    for item in data.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    if not chunks:
        raise RuntimeError("provider response contained no output text")
    return "\n".join(chunks).strip()


class ResponsesProvider:
    def __init__(
        self,
        name: str,
        model: str,
        api_key: str,
        endpoint: str,
        timeout: int = 600,
        transport: Transport = post_json,
        system_as_message: bool = False,
    ) -> None:
        self.name = name
        self.model = model
        self._api_key = api_key
        self._endpoint = endpoint
        self._timeout = timeout
        self._transport = transport
        self._system_as_message = system_as_message

    def generate(self, system: str, prompt: str) -> ModelReply:
        payload: dict[str, Any] = {
            "model": self.model,
            "store": False,
            "max_output_tokens": 4096,
        }
        if self._system_as_message:
            payload["input"] = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
        else:
            payload["instructions"] = system
            payload["input"] = prompt
        started = time.monotonic()
        data = self._transport(
            self._endpoint,
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload,
            self._timeout,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return ModelReply(
            provider=self.name,
            model=self.model,
            text=_response_text(data),
            request_id=str(data["id"]) if data.get("id") else None,
            usage=usage,
            elapsed_ms=elapsed_ms,
        )

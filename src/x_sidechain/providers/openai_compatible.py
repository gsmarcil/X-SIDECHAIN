from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from x_sidechain.auth import AuthMaterial
from x_sidechain.config import ProviderConfig
from x_sidechain.http import post_json
from x_sidechain.models import ModelReply

Transport = Callable[[str, dict[str, str], dict[str, Any], int], dict[str, Any]]


def _responses_text(data: dict[str, Any]) -> str:
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
        raise RuntimeError("Responses-compatible provider returned no output text")
    return "\n".join(chunks).strip()


def _chat_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("Chat Completions-compatible provider returned no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("Chat Completions-compatible provider returned no message")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        chunks = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if chunks:
            return "\n".join(chunks).strip()
    raise RuntimeError("Chat Completions-compatible provider returned no text")


class OpenAICompatibleProvider:
    def __init__(
        self,
        config: ProviderConfig,
        model: str,
        auth: AuthMaterial,
        timeout: int = 600,
        transport: Transport = post_json,
    ) -> None:
        if config.protocol not in {"responses", "chat_completions"}:
            raise ValueError(f"invalid OpenAI-compatible protocol: {config.protocol}")
        self.name = config.id
        self.model = model
        self._config = config
        self._auth = auth
        self._timeout = timeout
        self._transport = transport

    def generate(self, system: str, prompt: str) -> ModelReply:
        if self._config.protocol == "responses":
            endpoint = f"{self._config.base_url}/responses"
            payload: dict[str, Any] = {
                "model": self.model,
                "instructions": system,
                "input": prompt,
                "store": False,
                "max_output_tokens": self._config.max_output_tokens,
            }
            parse = _responses_text
        else:
            endpoint = f"{self._config.base_url}/chat/completions"
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": self._config.max_output_tokens,
            }
            parse = _chat_text
        headers = self._auth.apply({"Content-Type": "application/json", **self._config.headers})
        started = time.monotonic()
        data = self._transport(endpoint, headers, payload, self._timeout)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return ModelReply(
            provider=self.name,
            model=self.model,
            text=parse(data),
            request_id=str(data["id"]) if data.get("id") else None,
            usage=usage,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

from __future__ import annotations

from typing import Protocol

from x_sidechain.models import ModelReply


class Provider(Protocol):
    name: str
    model: str

    def generate(self, system: str, prompt: str) -> ModelReply:
        ...


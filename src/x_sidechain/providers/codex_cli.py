from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from x_sidechain.auth import CodexAccountStatus, codex_account_status, codex_subprocess_env
from x_sidechain.config import ProviderConfig
from x_sidechain.http import MAX_ERROR_BYTES, MAX_RESPONSE_BYTES, ProviderHTTPError, scrub
from x_sidechain.models import ModelReply


StatusChecker = Callable[[], CodexAccountStatus]


class CodexCLIProvider:
    """Use the official Codex CLI without handling ChatGPT credentials ourselves."""

    def __init__(
        self,
        config: ProviderConfig,
        model: str,
        timeout: int = 600,
        *,
        status_checker: StatusChecker = codex_account_status,
    ) -> None:
        if config.protocol != "codex_cli" or config.auth.type != "chatgpt_account":
            raise ValueError("CodexCLIProvider requires codex_cli with chatgpt_account auth")
        self.name = config.id
        self.model = model
        self._timeout = timeout
        self._command = shutil.which("codex")
        if self._command is None:
            raise RuntimeError("Codex CLI is not installed or is not on PATH")
        status = status_checker()
        if not status.authenticated:
            if status.method == "api_key":
                raise RuntimeError(
                    "Codex CLI is using an API key; sign in with ChatGPT for this provider"
                )
            raise RuntimeError(
                f"provider {config.id} is not signed in; run: "
                f"x-sidechain auth login {config.id} --config YOUR_CONFIG.json"
            )

    def generate(self, system: str, prompt: str) -> ModelReply:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="x-sidechain-codex-") as directory:
            root = Path(directory)
            answer_path = root / "answer.txt"
            error_path = root / "stderr.log"
            argv = [
                self._command,
                "-c", "features.shell_tool=false",
                "-c", "features.view_image=false",
                "-c", "features.web_search=false",
                "-c", "features.search_tool=false",
                "-c", "features.tool_search=false",
                "-c", "features.skill_search=false",
                "-c", "features.multi_agent=false",
                "-c", "features.memories=false",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox", "read-only",
                "--color", "never",
                "--model", self.model,
                "--output-last-message", str(answer_path),
                "-",
            ]
            request = (
                "SYSTEM INSTRUCTIONS (higher priority than the task):\n"
                f"{system}\n\nUSER TASK:\n{prompt}"
            )
            try:
                with error_path.open("wb") as error_file:
                    result = subprocess.run(
                        argv,
                        input=request.encode("utf-8"),
                        stdout=subprocess.DEVNULL,
                        stderr=error_file,
                        cwd=root,
                        check=False,
                        timeout=self._timeout,
                        env=codex_subprocess_env(),
                    )
            except subprocess.TimeoutExpired as exc:
                raise ProviderHTTPError(
                    f"provider command timed out after {self._timeout}s",
                    retryable=True,
                ) from exc
            except OSError as exc:
                raise ProviderHTTPError("provider command could not start", retryable=True) from exc

            with error_path.open("rb") as error_file:
                detail = scrub(
                    error_file.read(MAX_ERROR_BYTES).decode("utf-8", errors="replace")
                )
            if result.returncode != 0:
                raise ProviderHTTPError(
                    "provider command failed",
                    retryable=False,
                    detail=detail,
                )
            try:
                with answer_path.open("rb") as answer_file:
                    raw = answer_file.read(MAX_RESPONSE_BYTES + 1)
            except OSError as exc:
                raise ProviderHTTPError(
                    "provider command returned no final answer",
                    detail=detail,
                ) from exc
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ProviderHTTPError("provider response exceeded the 32 MiB safety limit")
            answer = raw.decode("utf-8", errors="replace").strip()
            if not answer:
                raise ProviderHTTPError("provider command returned no final answer", detail=detail)
            return ModelReply(
                provider=self.name,
                model=self.model,
                text=answer,
                usage={},
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )

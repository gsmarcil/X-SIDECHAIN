from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from typing import Any, Protocol

from x_sidechain.config import AuthConfig, ProviderConfig


class TokenStore(Protocol):
    def get(self, provider_id: str) -> str | None: ...
    def set(self, provider_id: str, token: str) -> None: ...


class SecretServiceTokenStore:
    """Linux Secret Service storage through the standard secret-tool utility."""

    def __init__(self) -> None:
        self.available = shutil.which("secret-tool") is not None

    def get(self, provider_id: str) -> str | None:
        if not self.available:
            return None
        result = subprocess.run(
            ["secret-tool", "lookup", "application", "x-sidechain", "provider", provider_id],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def set(self, provider_id: str, token: str) -> None:
        if not self.available:
            raise RuntimeError("OAuth token storage requires secret-tool (libsecret-tools)")
        subprocess.run(
            [
                "secret-tool",
                "store",
                "--label",
                f"X-SIDECHAIN {provider_id}",
                "application",
                "x-sidechain",
                "provider",
                provider_id,
            ],
            input=token,
            text=True,
            check=True,
        )


@dataclass(frozen=True)
class AuthMaterial:
    header: str | None
    value: str | None

    def apply(self, headers: dict[str, str]) -> dict[str, str]:
        result = dict(headers)
        if self.header and self.value:
            result[self.header] = self.value
        return result


@dataclass(frozen=True)
class CodexAccountStatus:
    available: bool
    authenticated: bool
    method: str | None = None


def codex_account_status(timeout: int = 10) -> CodexAccountStatus:
    """Ask Codex about its own credential state without reading its auth files."""
    command = shutil.which("codex")
    if command is None:
        return CodexAccountStatus(False, False)
    try:
        result = subprocess.run(
            [command, "login", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return CodexAccountStatus(True, False)
    output = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode != 0:
        return CodexAccountStatus(True, False)
    if "using chatgpt" in output:
        return CodexAccountStatus(True, True, "chatgpt")
    if "using an api key" in output:
        return CodexAccountStatus(True, False, "api_key")
    return CodexAccountStatus(True, False, "unknown")


def codex_account_login(device_auth: bool = False) -> str:
    """Delegate account authentication to the official Codex CLI."""
    command = shutil.which("codex")
    if command is None:
        raise RuntimeError("Codex CLI is not installed or is not on PATH")
    argv = [command, "login"]
    if device_auth:
        argv.append("--device-auth")
    try:
        result = subprocess.run(argv, check=False)
    except OSError as exc:
        raise RuntimeError("could not start Codex account authentication") from exc
    if result.returncode != 0:
        raise RuntimeError("Codex account authentication did not complete")
    status = codex_account_status()
    if not status.authenticated:
        if status.method == "api_key":
            raise RuntimeError("Codex is using an API key, not a ChatGPT account")
        raise RuntimeError("Codex did not report an active ChatGPT account session")
    return "ChatGPT account authentication completed through Codex CLI"


def resolve_auth(provider: ProviderConfig, token_store: TokenStore | None = None) -> AuthMaterial:
    auth = provider.auth
    if auth.type == "none":
        return AuthMaterial(None, None)
    if auth.type == "chatgpt_account":
        # Codex owns and refreshes this credential. It must never become an HTTP
        # header or pass through X-SIDECHAIN's process memory.
        return AuthMaterial(None, None)
    if auth.type == "api_key":
        value = os.getenv(str(auth.env), "").strip()
        if not value:
            raise RuntimeError(f"missing {auth.env} for provider {provider.id}")
        rendered = f"{auth.scheme} {value}".strip() if auth.scheme else value
        return AuthMaterial(auth.header, rendered)
    if auth.type == "oauth_device":
        if auth.token_env:
            value = os.getenv(auth.token_env, "").strip()
            if value:
                return AuthMaterial(auth.header, f"{auth.scheme} {value}".strip())
        store = token_store or SecretServiceTokenStore()
        value = store.get(provider.id)
        if not value:
            raise RuntimeError(
                f"provider {provider.id} is not logged in; run: "
                f"x-sidechain auth login {provider.id} --config YOUR_CONFIG.json"
            )
        return AuthMaterial(auth.header, f"{auth.scheme} {value}".strip())
    raise RuntimeError(f"unsupported auth type: {auth.type}")


def _post_form(url: str, values: dict[str, str], timeout: int = 30) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(values).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            body = {"error": f"http_{exc.code}"}
        return exc.code, body


def oauth_device_login(
    provider: ProviderConfig,
    token_store: TokenStore | None = None,
    open_browser: bool = True,
) -> str:
    auth: AuthConfig = provider.auth
    if auth.type != "oauth_device":
        raise ValueError(f"provider {provider.id} does not declare OAuth device login")
    store = token_store or SecretServiceTokenStore()
    if isinstance(store, SecretServiceTokenStore) and not store.available:
        raise RuntimeError("install libsecret-tools before account login so tokens are never stored in plaintext")
    request = {"client_id": str(auth.client_id)}
    if auth.scopes:
        request["scope"] = " ".join(auth.scopes)
    status, device = _post_form(str(auth.device_authorization_url), request)
    if status >= 400:
        raise RuntimeError(f"device authorization failed: {device.get('error', status)}")
    device_code = device.get("device_code")
    user_code = device.get("user_code")
    verification_url = device.get("verification_uri_complete") or device.get("verification_uri")
    if not all(isinstance(value, str) and value for value in (device_code, user_code, verification_url)):
        raise RuntimeError("provider returned an incomplete device authorization response")
    print(f"Open this official provider URL:\n{verification_url}\nCode: {user_code}", flush=True)
    if open_browser:
        webbrowser.open(verification_url)
    interval = max(1, int(device.get("interval", 5)))
    deadline = time.monotonic() + int(device.get("expires_in", 900))
    while time.monotonic() < deadline:
        time.sleep(interval)
        status, token = _post_form(
            str(auth.token_url),
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": str(auth.client_id),
            },
        )
        if status < 400 and isinstance(token.get("access_token"), str):
            store.set(provider.id, token["access_token"])
            return "account authentication completed; token stored in Linux Secret Service"
        error = token.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise RuntimeError(f"account authentication failed: {error or status}")
    raise RuntimeError("account authentication expired before approval")

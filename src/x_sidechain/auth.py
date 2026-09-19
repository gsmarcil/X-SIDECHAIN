from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Protocol

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
    checking: bool = False


# The Codex process must be able to find its executable, account store, locale,
# certificates, and desktop login helper. Provider credentials and arbitrary
# application variables are deliberately absent. Keep this an allowlist: a
# denylist will inevitably miss the next provider's key name.
_CODEX_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "COLORTERM",
    "LANG", "TMPDIR", "TMP", "TEMP", "CODEX_HOME",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "BROWSER",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "WSL_DISTRO_NAME", "WSL_INTEROP",
})


def codex_subprocess_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the explicit, credential-free environment used by Codex children."""
    source = os.environ if source is None else source
    return {
        name: value
        for name, value in source.items()
        if name in _CODEX_ENV_ALLOWLIST or name.startswith("LC_")
    }


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
            env=codex_subprocess_env(),
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
        result = subprocess.run(argv, check=False, env=codex_subprocess_env())
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


class CodexAccountStatusCache:
    """Small stale-while-refresh cache for the UI's Codex account indicator.

    The CLI can take up to ten seconds to answer. UI request threads therefore
    never execute it directly: the first request starts one background refresh,
    and concurrent requests receive a truthful ``checking`` state.
    """

    def __init__(
        self,
        *,
        checker: Callable[[], CodexAccountStatus] = codex_account_status,
        availability_checker: Callable[[], bool] | None = None,
        ttl_seconds: float = 30.0,
    ) -> None:
        self._checker = checker
        self._availability_checker = availability_checker or (
            lambda: shutil.which("codex") is not None
        )
        self._ttl_seconds = ttl_seconds
        self._status: CodexAccountStatus | None = None
        self._checked_at = 0.0
        self._refreshing = False
        self._lock = threading.Lock()

    def get(self) -> CodexAccountStatus:
        now = time.monotonic()
        with self._lock:
            fresh = self._status is not None and now - self._checked_at < self._ttl_seconds
            if fresh:
                return self._status

            if not self._availability_checker():
                self._status = CodexAccountStatus(False, False)
                self._checked_at = now
                return self._status

            if not self._refreshing:
                self._refreshing = True
                threading.Thread(
                    target=self._refresh,
                    name="xsidechain-codex-status",
                    daemon=True,
                ).start()

            if self._status is None:
                return CodexAccountStatus(True, False, checking=True)
            return replace(self._status, checking=True)

    def _refresh(self) -> None:
        try:
            status = self._checker()
        except Exception:  # noqa: BLE001 - status reporting must not break the UI
            status = CodexAccountStatus(True, False, "unknown")
        with self._lock:
            self._status = replace(status, checking=False)
            self._checked_at = time.monotonic()
            self._refreshing = False


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

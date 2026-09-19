import os
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from x_sidechain.auth import (
    AuthMaterial,
    CodexAccountStatus,
    CodexAccountStatusCache,
    codex_account_login,
    codex_account_status,
    oauth_device_login,
    resolve_auth,
)
from x_sidechain.config import AuthConfig, ProviderConfig


class AuthTests(unittest.TestCase):
    def test_api_key_header_is_assembled_from_environment(self) -> None:
        provider = ProviderConfig(
            id="custom",
            protocol="responses",
            base_url="https://example.invalid/v1",
            auth=AuthConfig(type="api_key", env="CUSTOM_KEY", header="X-Key", scheme="Token"),
        )
        with patch.dict(os.environ, {"CUSTOM_KEY": "value"}, clear=False):
            material = resolve_auth(provider)
        self.assertEqual(material.apply({"A": "B"})["X-Key"], "Token value")

    def test_no_auth_provider_adds_nothing(self) -> None:
        provider = ProviderConfig(
            id="local",
            protocol="chat_completions",
            base_url="http://127.0.0.1:11434/v1",
            auth=AuthConfig(type="none"),
        )
        self.assertEqual(resolve_auth(provider), AuthMaterial(None, None))

    def test_official_device_flow_stores_returned_token(self) -> None:
        class MemoryStore:
            def __init__(self) -> None:
                self.value = None

            def get(self, provider_id: str) -> str | None:
                return self.value

            def set(self, provider_id: str, token: str) -> None:
                self.value = token

        provider = ProviderConfig(
            id="official-provider",
            protocol="chat_completions",
            base_url="https://api.example.invalid/v1",
            auth=AuthConfig(
                type="oauth_device",
                device_authorization_url="https://identity.example.invalid/device",
                token_url="https://identity.example.invalid/token",
                client_id="client-id",
                scopes=("model.invoke",),
            ),
        )
        store = MemoryStore()
        replies = [
            (
                200,
                {
                    "device_code": "device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://identity.example.invalid/activate",
                    "interval": 1,
                    "expires_in": 60,
                },
            ),
            (400, {"error": "authorization_pending"}),
            (200, {"access_token": "access-token"}),
        ]
        with (
            patch("x_sidechain.auth._post_form", side_effect=replies) as post,
            patch("x_sidechain.auth.time.sleep"),
        ):
            message = oauth_device_login(provider, token_store=store, open_browser=False)
        self.assertEqual(store.value, "access-token")
        self.assertIn("completed", message)
        self.assertEqual(post.call_count, 3)

    def test_codex_status_accepts_only_chatgpt_account_login(self) -> None:
        with (
            patch("x_sidechain.auth.shutil.which", return_value="/usr/bin/codex"),
            patch(
                "x_sidechain.auth.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", "Logged in using ChatGPT"),
            ) as run,
        ):
            status = codex_account_status()
        self.assertTrue(status.available)
        self.assertTrue(status.authenticated)
        self.assertEqual(status.method, "chatgpt")
        run.assert_called_once_with(
            ["/usr/bin/codex", "login", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env=unittest.mock.ANY,
        )
        child_env = run.call_args.kwargs["env"]
        self.assertNotIn("OPENAI_API_KEY", child_env)
        self.assertNotIn("ANTHROPIC_API_KEY", child_env)

    def test_codex_api_key_login_is_not_misreported_as_account_login(self) -> None:
        with (
            patch("x_sidechain.auth.shutil.which", return_value="/usr/bin/codex"),
            patch(
                "x_sidechain.auth.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", "Logged in using an API key"),
            ),
        ):
            status = codex_account_status()
        self.assertFalse(status.authenticated)
        self.assertEqual(status.method, "api_key")

    def test_codex_login_delegates_device_auth_to_official_cli(self) -> None:
        with (
            patch("x_sidechain.auth.shutil.which", return_value="/usr/bin/codex"),
            patch(
                "x_sidechain.auth.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as run,
            patch(
                "x_sidechain.auth.codex_account_status",
                return_value=type("Status", (), {"authenticated": True})(),
            ),
        ):
            message = codex_account_login(device_auth=True)
        self.assertIn("ChatGPT", message)
        run.assert_called_once_with(
            ["/usr/bin/codex", "login", "--device-auth"],
            check=False,
            env=unittest.mock.ANY,
        )

    def test_codex_status_cache_refreshes_once_without_blocking_callers(self) -> None:
        started = threading.Event()
        release = threading.Event()
        calls = 0

        def slow_status() -> CodexAccountStatus:
            nonlocal calls
            calls += 1
            started.set()
            release.wait(timeout=2)
            return CodexAccountStatus(True, True, "chatgpt")

        cache = CodexAccountStatusCache(
            checker=slow_status,
            availability_checker=lambda: True,
            ttl_seconds=30,
        )
        before = time.monotonic()
        first = cache.get()
        second = cache.get()
        elapsed = time.monotonic() - before

        self.assertTrue(started.wait(timeout=1))
        self.assertLess(elapsed, 0.2)
        self.assertTrue(first.checking)
        self.assertTrue(second.checking)
        self.assertEqual(calls, 1)

        release.set()
        deadline = time.monotonic() + 1
        status = cache.get()
        while status.checking and time.monotonic() < deadline:
            time.sleep(0.01)
            status = cache.get()
        self.assertTrue(status.authenticated)
        self.assertFalse(status.checking)
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()

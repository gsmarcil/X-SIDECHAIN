import os
import unittest
from unittest.mock import patch

from x_sidechain.auth import AuthMaterial, oauth_device_login, resolve_auth
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


if __name__ == "__main__":
    unittest.main()

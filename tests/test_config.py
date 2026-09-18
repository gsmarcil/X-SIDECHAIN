import json
import tempfile
import unittest
from pathlib import Path

from x_sidechain.config import load_config


def valid_config() -> dict:
    return {
        "version": 1,
        "providers": {
            "custom": {
                "protocol": "chat_completions",
                "base_url": "https://models.example/v1",
                "auth": {"type": "api_key", "env": "CUSTOM_KEY"},
            }
        },
        "agents": [
            {"id": "one", "provider": "custom", "model": "model-a", "role": "role one"},
            {"id": "two", "provider": "custom", "model": "model-b", "role": "role two"},
            {"id": "three", "provider": "custom", "model": "model-c", "role": "role three"},
        ],
        "chair": "two",
    }


class ConfigTests(unittest.TestCase):
    def _load(self, raw: dict):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            return load_config(path)

    def test_custom_provider_and_arbitrary_agent_count(self) -> None:
        config = self._load(valid_config())
        self.assertEqual(len(config.agents), 3)
        self.assertEqual(config.agents[2].model, "model-c")
        self.assertEqual(config.providers["custom"].protocol, "chat_completions")

    def test_unknown_provider_is_rejected(self) -> None:
        raw = valid_config()
        raw["agents"][0]["provider"] = "missing"
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self._load(raw)

    def test_duplicate_agent_id_is_rejected(self) -> None:
        raw = valid_config()
        raw["agents"][1]["id"] = "one"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self._load(raw)

    def test_non_integer_clarification_limit_is_rejected(self) -> None:
        raw = valid_config()
        raw["max_clarification_questions"] = "two"
        with self.assertRaisesRegex(ValueError, "integer"):
            self._load(raw)

    def test_oauth_endpoints_must_be_absolute_urls(self) -> None:
        raw = valid_config()
        raw["providers"]["custom"]["auth"] = {
            "type": "oauth_device",
            "device_authorization_url": "/device",
            "token_url": "https://identity.example/token",
            "client_id": "client",
        }
        with self.assertRaisesRegex(ValueError, "absolute HTTP"):
            self._load(raw)

    def test_call_budget_cannot_be_below_live_room_minimum(self) -> None:
        raw = valid_config()
        raw["max_clarification_questions"] = 3
        raw["max_model_calls"] = 13
        with self.assertRaisesRegex(ValueError, "at least 14"):
            self._load(raw)

    def test_unsafe_agent_id_is_rejected(self) -> None:
        raw = valid_config()
        raw["agents"][0]["id"] = "../escape"
        with self.assertRaisesRegex(ValueError, "agent id"):
            self._load(raw)

    def test_cleartext_http_to_a_remote_provider_is_rejected(self) -> None:
        raw = valid_config()
        raw["providers"]["custom"]["base_url"] = "http://models.example/v1"
        with self.assertRaisesRegex(ValueError, "cleartext http"):
            self._load(raw)

    def test_cleartext_http_is_allowed_for_loopback_and_by_opt_in(self) -> None:
        raw = valid_config()
        raw["providers"]["custom"]["base_url"] = "http://127.0.0.1:11434/v1"
        self.assertEqual(self._load(raw).providers["custom"].base_url, "http://127.0.0.1:11434/v1")

        raw = valid_config()
        raw["providers"]["custom"]["base_url"] = "http://models.example/v1"
        raw["providers"]["custom"]["allow_insecure_http"] = True
        self.assertTrue(self._load(raw).providers["custom"].allow_insecure_http)

    def test_legacy_synthesizer_key_is_migrated(self) -> None:
        raw = valid_config()
        raw["synthesizer"] = raw.pop("chair")
        self.assertEqual(self._load(raw).chair, "two")


if __name__ == "__main__":
    unittest.main()

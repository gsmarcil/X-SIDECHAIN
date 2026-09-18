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
        "synthesizer": "two",
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

    def test_non_integer_contribution_count_is_rejected(self) -> None:
        raw = valid_config()
        raw["contributions_per_agent"] = "two"
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
        raw["contributions_per_agent"] = 2
        raw["max_model_calls"] = 12
        with self.assertRaisesRegex(ValueError, "at least 13"):
            self._load(raw)


if __name__ == "__main__":
    unittest.main()

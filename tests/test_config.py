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

    def test_codex_cli_uses_chatgpt_account_without_a_base_url(self) -> None:
        raw = valid_config()
        raw["providers"]["custom"] = {
            "protocol": "codex_cli",
            "auth": {"type": "chatgpt_account"},
        }
        config = self._load(raw)
        provider = config.providers["custom"]
        self.assertEqual(provider.protocol, "codex_cli")
        self.assertEqual(provider.auth.type, "chatgpt_account")
        self.assertEqual(provider.base_url, "")

    def test_codex_cli_rejects_http_and_key_configuration(self) -> None:
        raw = valid_config()
        raw["providers"]["custom"] = {
            "protocol": "codex_cli",
            "base_url": "https://api.openai.com/v1",
            "auth": {"type": "chatgpt_account"},
        }
        with self.assertRaisesRegex(ValueError, "base_url is not used"):
            self._load(raw)

        raw = valid_config()
        raw["providers"]["custom"] = {
            "protocol": "codex_cli",
            "auth": {"type": "api_key", "env": "OPENAI_API_KEY"},
        }
        with self.assertRaisesRegex(ValueError, "must be chatgpt_account"):
            self._load(raw)

    def test_chatgpt_account_cannot_be_sent_to_an_http_endpoint(self) -> None:
        raw = valid_config()
        raw["providers"]["custom"]["auth"] = {"type": "chatgpt_account"}
        with self.assertRaisesRegex(ValueError, "only valid with codex_cli"):
            self._load(raw)

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

    def test_new_bounds_have_defaults(self) -> None:
        config = self._load(valid_config())
        self.assertEqual(config.min_agent_quorum, 2)
        self.assertEqual(config.max_revisions, 8)
        self.assertEqual(config.max_public_brief_chars, 1200)
        self.assertIsNone(config.session_deadline_seconds)

    def test_quorum_cannot_exceed_the_agent_count(self) -> None:
        raw = valid_config()
        raw["min_agent_quorum"] = 4
        with self.assertRaisesRegex(ValueError, "min_agent_quorum must be between 2 and 3"):
            self._load(raw)

    def test_quorum_below_two_is_rejected(self) -> None:
        raw = valid_config()
        raw["min_agent_quorum"] = 1
        with self.assertRaisesRegex(ValueError, "min_agent_quorum"):
            self._load(raw)

    def test_revision_and_brief_bounds_are_validated(self) -> None:
        raw = valid_config()
        raw["max_revisions"] = 101
        with self.assertRaisesRegex(ValueError, "max_revisions"):
            self._load(raw)

        raw = valid_config()
        raw["max_public_brief_chars"] = 100
        with self.assertRaisesRegex(ValueError, "max_public_brief_chars"):
            self._load(raw)

    def test_deadline_shorter_than_one_call_is_rejected(self) -> None:
        raw = valid_config()
        raw["request_timeout_seconds"] = 600
        raw["session_deadline_seconds"] = 300
        with self.assertRaisesRegex(ValueError, "at least request_timeout_seconds"):
            self._load(raw)

    def test_deadline_is_accepted_when_it_fits_a_call(self) -> None:
        raw = valid_config()
        raw["request_timeout_seconds"] = 120
        raw["session_deadline_seconds"] = 3600
        self.assertEqual(self._load(raw).session_deadline_seconds, 3600)
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

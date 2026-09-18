import unittest

from x_sidechain.auth import AuthMaterial
from x_sidechain.config import AuthConfig, ProviderConfig
from x_sidechain.providers.anthropic import AnthropicProvider
from x_sidechain.providers.openai_compatible import OpenAICompatibleProvider


def provider_config(protocol: str) -> ProviderConfig:
    return ProviderConfig(
        id="test-provider",
        protocol=protocol,
        base_url="https://example.invalid/v1",
        auth=AuthConfig(type="none"),
    )


class ProviderTests(unittest.TestCase):
    def test_responses_api_shape_is_parsed(self) -> None:
        captured = {}

        def transport(url, headers, payload, timeout):
            captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
            return {
                "id": "resp_123",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "answer"}]}
                ],
                "usage": {"input_tokens": 5, "output_tokens": 1},
            }

        provider = OpenAICompatibleProvider(
            provider_config("responses"),
            "test-model",
            AuthMaterial("Authorization", "Bearer secret"),
            transport=transport,
        )
        reply = provider.generate("system", "prompt")

        self.assertEqual(reply.text, "answer")
        self.assertEqual(reply.request_id, "resp_123")
        self.assertFalse(captured["payload"]["store"])
        self.assertEqual(captured["payload"]["instructions"], "system")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret")

    def test_chat_completions_shape_is_parsed(self) -> None:
        captured = {}

        def transport(url, headers, payload, timeout):
            captured.update(url=url, payload=payload)
            return {
                "id": "chat_123",
                "choices": [{"message": {"role": "assistant", "content": "chat answer"}}],
            }

        provider = OpenAICompatibleProvider(
            provider_config("chat_completions"),
            "any-model-name",
            AuthMaterial(None, None),
            transport=transport,
        )
        reply = provider.generate("system", "prompt")

        self.assertEqual(reply.text, "chat answer")
        self.assertTrue(captured["url"].endswith("/chat/completions"))
        self.assertEqual(captured["payload"]["model"], "any-model-name")

    def test_anthropic_message_shape_is_parsed(self) -> None:
        captured = {}

        def transport(url, headers, payload, timeout):
            captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
            return {
                "id": "msg_123",
                "content": [{"type": "text", "text": "answer"}],
                "usage": {"input_tokens": 4, "output_tokens": 1},
            }

        config = provider_config("anthropic_messages")
        provider = AnthropicProvider(config, "test-model", AuthMaterial("x-api-key", "secret"), transport=transport)
        reply = provider.generate("system", "prompt")

        self.assertEqual(reply.text, "answer")
        self.assertEqual(reply.request_id, "msg_123")
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(captured["headers"]["x-api-key"], "secret")


if __name__ == "__main__":
    unittest.main()


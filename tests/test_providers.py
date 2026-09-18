import unittest

from x_sidechain.providers.anthropic import AnthropicProvider
from x_sidechain.providers.openai_compatible import ResponsesProvider


class ProviderTests(unittest.TestCase):
    def test_responses_api_shape_is_parsed(self) -> None:
        captured = {}

        def transport(url, headers, payload, timeout):
            captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
            return {
                "id": "resp_123",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "answer"}],
                    }
                ],
                "usage": {"input_tokens": 5, "output_tokens": 1},
            }

        provider = ResponsesProvider(
            name="openai",
            model="test-model",
            api_key="secret",
            endpoint="https://example.invalid/v1/responses",
            transport=transport,
        )
        reply = provider.generate("system", "prompt")

        self.assertEqual(reply.text, "answer")
        self.assertEqual(reply.request_id, "resp_123")
        self.assertFalse(captured["payload"]["store"])
        self.assertEqual(captured["payload"]["instructions"], "system")

    def test_anthropic_message_shape_is_parsed(self) -> None:
        captured = {}

        def transport(url, headers, payload, timeout):
            captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
            return {
                "id": "msg_123",
                "content": [{"type": "text", "text": "answer"}],
                "usage": {"input_tokens": 4, "output_tokens": 1},
            }

        provider = AnthropicProvider("test-model", "secret", transport=transport)
        reply = provider.generate("system", "prompt")

        self.assertEqual(reply.text, "answer")
        self.assertEqual(reply.request_id, "msg_123")
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(captured["payload"]["messages"][0]["role"], "user")


if __name__ == "__main__":
    unittest.main()


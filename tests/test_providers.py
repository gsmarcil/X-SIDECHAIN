import subprocess
import unittest
from unittest.mock import patch

from x_sidechain.auth import AuthMaterial, CodexAccountStatus
from x_sidechain.config import AuthConfig, ProviderConfig
from x_sidechain.providers.anthropic import AnthropicProvider
from x_sidechain.providers.codex_cli import CodexCLIProvider
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

    def test_codex_cli_disables_execution_tools_and_reads_only_final_answer(self) -> None:
        config = ProviderConfig(
            id="chatgpt",
            protocol="codex_cli",
            base_url="",
            auth=AuthConfig(type="chatgpt_account"),
        )
        captured = {}

        def run(argv, **kwargs):
            captured.update(argv=argv, kwargs=kwargs)
            output = argv[argv.index("--output-last-message") + 1]
            with open(output, "w", encoding="utf-8") as handle:
                handle.write("account answer\n")
            return subprocess.CompletedProcess(argv, 0)

        with (
            patch("x_sidechain.providers.codex_cli.shutil.which", return_value="/usr/bin/codex"),
            patch("x_sidechain.providers.codex_cli.subprocess.run", side_effect=run),
        ):
            provider = CodexCLIProvider(
                config,
                "gpt-test",
                status_checker=lambda: CodexAccountStatus(True, True, "chatgpt"),
            )
            reply = provider.generate("system rule", "user task")

        self.assertEqual(reply.text, "account answer")
        self.assertIn("--ephemeral", captured["argv"])
        self.assertIn("--ignore-user-config", captured["argv"])
        self.assertIn("--ignore-rules", captured["argv"])
        self.assertIn("features.shell_tool=false", captured["argv"])
        self.assertIn("features.web_search=false", captured["argv"])
        self.assertIn("features.multi_agent=false", captured["argv"])
        self.assertEqual(captured["kwargs"]["stdout"], subprocess.DEVNULL)
        sent = captured["kwargs"]["input"].decode("utf-8")
        self.assertIn("system rule", sent)
        self.assertIn("user task", sent)

    def test_codex_cli_error_detail_is_private_and_scrubbed(self) -> None:
        config = ProviderConfig(
            id="chatgpt",
            protocol="codex_cli",
            base_url="",
            auth=AuthConfig(type="chatgpt_account"),
        )

        def run(argv, **kwargs):
            kwargs["stderr"].write(b"Bearer sk-secret-secret-secret provider detail")
            return subprocess.CompletedProcess(argv, 1)

        with (
            patch("x_sidechain.providers.codex_cli.shutil.which", return_value="/usr/bin/codex"),
            patch("x_sidechain.providers.codex_cli.subprocess.run", side_effect=run),
        ):
            provider = CodexCLIProvider(
                config,
                "gpt-test",
                status_checker=lambda: CodexAccountStatus(True, True, "chatgpt"),
            )
            with self.assertRaisesRegex(RuntimeError, "provider command failed") as caught:
                provider.generate("system", "prompt")
        self.assertNotIn("sk-secret", str(caught.exception))
        self.assertNotIn("sk-secret", caught.exception.detail)
        self.assertIn("[REDACTED]", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()

import tempfile
import threading
import unittest
from pathlib import Path

from x_sidechain.audit import AuditLog
from x_sidechain.models import AgentSpec, ModelReply
from x_sidechain.orchestrator import AgentRuntime, SidechainOrchestrator


class FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.calls = []
        self._lock = threading.Lock()

    def generate(self, system: str, prompt: str) -> ModelReply:
        with self._lock:
            call_number = len(self.calls) + 1
            self.calls.append((system, prompt))
        return ModelReply(self.name, self.model, f"{self.name}-reply-{call_number}")


class OrchestratorTests(unittest.TestCase):
    def test_four_agent_broadcast_review_and_audit(self) -> None:
        providers = [FakeProvider(f"provider-{index}") for index in range(4)]
        agents = tuple(
            AgentRuntime(
                AgentSpec(f"agent-{index}", provider.name, provider.model, f"role-{index}"),
                provider,
            )
            for index, provider in enumerate(providers)
        )
        with tempfile.TemporaryDirectory() as directory:
            result = SidechainOrchestrator(
                agents=agents,
                synthesizer_id="agent-1",
                audit_directory=Path(directory),
            ).run("test exact claim")

            self.assertEqual(len(result.initial), 4)
            self.assertEqual(len(result.critiques), 4)
            first_prompts = {provider.calls[0][1] for provider in providers}
            self.assertEqual(len(first_prompts), 1, "all agents must receive the exact same first prompt")
            for index, provider in enumerate(providers):
                self.assertEqual(len(provider.calls), 3 if index == 1 else 2)
                review_prompt = provider.calls[1][1]
                for peer_index in range(4):
                    expected = f"provider-{peer_index}-reply-1"
                    if peer_index == index:
                        self.assertNotIn(expected, review_prompt)
                    else:
                        self.assertIn(expected, review_prompt)
            self.assertIn("Use exactly these sections", providers[1].calls[2][1])
            valid, message = AuditLog.verify(result.audit_path)
            self.assertTrue(valid, message)

    def test_failed_provider_closes_session_with_valid_audit(self) -> None:
        class FailingProvider(FakeProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                raise RuntimeError("definitive failure")

        agents = (
            AgentRuntime(AgentSpec("ok", "ok", "model", "role"), FakeProvider("ok")),
            AgentRuntime(AgentSpec("fail", "fail", "model", "role"), FailingProvider("fail")),
        )
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = SidechainOrchestrator(
                agents,
                "ok",
                audit_directory=Path(directory),
            )
            with self.assertRaisesRegex(RuntimeError, "definitive failure"):
                orchestrator.run("claim")
            logs = list(Path(directory).glob("*.jsonl"))
            self.assertEqual(len(logs), 1)
            valid, message = AuditLog.verify(logs[0])
            self.assertTrue(valid, message)
            self.assertIn("session.failed", logs[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


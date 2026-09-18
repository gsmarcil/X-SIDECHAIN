import tempfile
import threading
import unittest
from pathlib import Path

from x_sidechain.audit import AuditLog
from x_sidechain.models import AgentSpec, ModelReply
from x_sidechain.orchestrator import SidechainOrchestrator


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
    def test_full_protocol_and_audit(self) -> None:
        provider_a = FakeProvider("alpha")
        provider_b = FakeProvider("beta")
        with tempfile.TemporaryDirectory() as directory:
            result = SidechainOrchestrator(
                provider_a=provider_a,
                provider_b=provider_b,
                agent_a=AgentSpec("A", "alpha", "alpha-model", "explore"),
                agent_b=AgentSpec("B", "beta", "beta-model", "validate"),
                synthesizer="a",
                audit_directory=Path(directory),
            ).run("test exact claim")

            self.assertEqual(len(provider_a.calls), 3)
            self.assertEqual(len(provider_b.calls), 2)
            self.assertIn("beta-reply-1", provider_a.calls[1][1])
            self.assertIn("alpha-reply-1", provider_b.calls[1][1])
            self.assertIn("Use exactly these sections", provider_a.calls[2][1])
            valid, message = AuditLog.verify(result.audit_path)
            self.assertTrue(valid, message)


if __name__ == "__main__":
    unittest.main()


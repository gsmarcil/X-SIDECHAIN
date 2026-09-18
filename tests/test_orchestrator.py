import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from x_sidechain.audit import AuditLog
from x_sidechain.models import AgentSpec, ModelReply
from x_sidechain.orchestrator import AgentRuntime, SidechainOrchestrator


class FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def generate(self, system: str, prompt: str) -> ModelReply:
        with self._lock:
            call_number = len(self.calls) + 1
            self.calls.append((system, prompt))
        return ModelReply(self.name, self.model, f"{self.name}-reply-{call_number}")


class BlockingFirstProvider(FakeProvider):
    def __init__(
        self,
        name: str,
        started_count: list[int],
        started_lock: threading.Lock,
        all_started: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(name)
        self.started_count = started_count
        self.started_lock = started_lock
        self.all_started = all_started
        self.release = release

    def generate(self, system: str, prompt: str) -> ModelReply:
        with self._lock:
            call_number = len(self.calls) + 1
            self.calls.append((system, prompt))
        if call_number == 1:
            with self.started_lock:
                self.started_count[0] += 1
                if self.started_count[0] == 2:
                    self.all_started.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError("test release timeout")
        return ModelReply(self.name, self.model, f"{self.name}-reply-{call_number}")


def runtime(agent_id: str, provider: FakeProvider) -> AgentRuntime:
    return AgentRuntime(
        AgentSpec(agent_id, provider.name, provider.model, f"role-{agent_id}"),
        provider,
    )


class OrchestratorTests(unittest.TestCase):
    def test_user_steering_supersedes_inflight_drafts(self) -> None:
        started_count = [0]
        started_lock = threading.Lock()
        all_started = threading.Event()
        release = threading.Event()
        providers = [
            BlockingFirstProvider(
                f"provider-{index}",
                started_count,
                started_lock,
                all_started,
                release,
            )
            for index in range(2)
        ]
        observed = []
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = SidechainOrchestrator(
                agents=tuple(runtime(f"agent-{index}", provider) for index, provider in enumerate(providers)),
                synthesizer_id="agent-0",
                contributions_per_agent=1,
                audit_directory=Path(directory),
                on_event=observed.append,
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(orchestrator.run, "test exact claim")
                self.assertTrue(orchestrator.wait_until_active(timeout=2))
                self.assertTrue(all_started.wait(timeout=2))
                steering = orchestrator.inject_user_message("also test the corrected boundary")
                release.set()
                result = future.result(timeout=10)

            contributions = [event for event in result.events if event.kind == "agent.contribution"]
            self.assertEqual(len(contributions), 2)
            self.assertEqual({event.author for event in contributions}, {"agent-0", "agent-1"})
            self.assertTrue(all(event.based_on_sequence >= steering.sequence for event in contributions))
            self.assertTrue(
                all(
                    any("also test the corrected boundary" in prompt for _, prompt in provider.calls[1:])
                    for provider in providers
                )
            )
            self.assertEqual(observed[0], steering)
            valid, message = AuditLog.verify(result.audit_path)
            self.assertTrue(valid, message)
            audit_text = Path(result.audit_path).read_text(encoding="utf-8")
            self.assertGreaterEqual(audit_text.count("room.draft_superseded"), 2)
            self.assertIn("also test the corrected boundary", providers[0].calls[-1][1])

    def test_three_agents_each_contribute_twice(self) -> None:
        providers = [FakeProvider(f"provider-{index}") for index in range(3)]
        with tempfile.TemporaryDirectory() as directory:
            result = SidechainOrchestrator(
                agents=tuple(runtime(f"agent-{index}", provider) for index, provider in enumerate(providers)),
                synthesizer_id="agent-1",
                contributions_per_agent=2,
                audit_directory=Path(directory),
            ).run("test exact claim")

            contributions = [event for event in result.events if event.kind == "agent.contribution"]
            self.assertEqual(len(contributions), 6)
            for index in range(3):
                self.assertEqual(
                    sum(event.author == f"agent-{index}" for event in contributions),
                    2,
                )
            sequence_numbers = [event.sequence for event in result.events]
            self.assertEqual(sequence_numbers, list(range(len(result.events))))
            self.assertIn("ACCEPTED LIVE EVENTS", providers[1].calls[-1][1])
            valid, message = AuditLog.verify(result.audit_path)
            self.assertTrue(valid, message)

    def test_user_steering_supersedes_inflight_synthesis(self) -> None:
        synthesis_started = threading.Event()
        release_synthesis = threading.Event()

        class BlockingSynthesizer(FakeProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                with self._lock:
                    call_number = len(self.calls) + 1
                    self.calls.append((system, prompt))
                if "ACCEPTED LIVE EVENTS" in prompt and not synthesis_started.is_set():
                    synthesis_started.set()
                    if not release_synthesis.wait(timeout=5):
                        raise RuntimeError("test synthesis release timeout")
                return ModelReply(self.name, self.model, f"{self.name}-reply-{call_number}")

        synthesizer = BlockingSynthesizer("synth")
        peer = FakeProvider("peer")
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = SidechainOrchestrator(
                agents=(runtime("synth", synthesizer), runtime("peer", peer)),
                synthesizer_id="synth",
                contributions_per_agent=1,
                audit_directory=Path(directory),
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(orchestrator.run, "test exact claim")
                self.assertTrue(synthesis_started.wait(timeout=5))
                steering = orchestrator.inject_user_message("correct the final constraint")
                release_synthesis.set()
                result = future.result(timeout=10)

            self.assertEqual(result.events[-1], steering)
            synthesis_prompts = [prompt for _, prompt in synthesizer.calls if "ACCEPTED LIVE EVENTS" in prompt]
            self.assertEqual(len(synthesis_prompts), 2)
            self.assertNotIn("correct the final constraint", synthesis_prompts[0])
            self.assertIn("correct the final constraint", synthesis_prompts[1])
            audit_text = Path(result.audit_path).read_text(encoding="utf-8")
            self.assertIn("synthesis.draft_superseded", audit_text)

    def test_failed_provider_closes_session_with_valid_audit(self) -> None:
        class FailingProvider(FakeProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                raise RuntimeError("definitive failure")

        agents = (
            runtime("ok", FakeProvider("ok")),
            runtime("fail", FailingProvider("fail")),
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

    def test_steering_requires_an_active_room(self) -> None:
        agents = (
            runtime("a", FakeProvider("a")),
            runtime("b", FakeProvider("b")),
        )
        orchestrator = SidechainOrchestrator(agents, "a")
        with self.assertRaisesRegex(RuntimeError, "no live discussion"):
            orchestrator.inject_user_message("too early")


if __name__ == "__main__":
    unittest.main()

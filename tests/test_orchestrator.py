import json
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from x_sidechain.audit import AuditLog
from x_sidechain.models import AgentSpec, ModelReply
from x_sidechain.orchestrator import AgentRuntime, SidechainOrchestrator


class ScriptedProvider:
    def __init__(self, name: str, question_target: str | None = None) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.question_target = question_target
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def generate(self, system: str, prompt: str) -> ModelReply:
        with self._lock:
            self.calls.append((system, prompt))
            call_number = len(self.calls)
        if prompt.startswith("PRIVATE WORKSPACE ANALYSIS"):
            text = f"private analysis from {self.name}"
        elif prompt.startswith("Create the public brief"):
            text = f"CLAIM: public summary from {self.name}"
        elif "Return JSON only:" in prompt:
            questions = []
            if self.question_target:
                questions = [{"agent_id": self.question_target, "question": "What artifact proves it?"}]
            text = json.dumps({"questions": questions})
        elif prompt.startswith("Answer one targeted chair question"):
            text = f"clarification from {self.name}"
        elif prompt.startswith("CHAIR DRAFT"):
            text = f"chair draft from {self.name}"
        elif prompt.startswith("REVIEW THE CHAIR DRAFT"):
            text = f"VERDICT: APPROVE\nERROR: NONE\nCORRECTION: NONE\nEVIDENCE: {self.name}"
        elif prompt.startswith("FINAL RESULT"):
            text = f"final result from {self.name}"
        else:
            raise AssertionError(f"unexpected prompt: {prompt[:80]}")
        return ModelReply(self.name, self.model, text, request_id=f"req-{call_number}")


def runtime(agent_id: str, provider: ScriptedProvider) -> AgentRuntime:
    return AgentRuntime(
        AgentSpec(agent_id, provider.name, provider.model, f"role-{agent_id}"),
        provider,
    )


class OrchestratorTests(unittest.TestCase):
    def test_chaired_workflow_targets_one_agent_and_creates_workspaces(self) -> None:
        chair = ScriptedProvider("chair-provider", question_target="agent-1")
        peer_one = ScriptedProvider("peer-one")
        peer_two = ScriptedProvider("peer-two")
        agents = (
            runtime("chair", chair),
            runtime("agent-1", peer_one),
            runtime("agent-2", peer_two),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = SidechainOrchestrator(
                agents,
                chair_id="chair",
                audit_directory=Path(directory),
            ).run("test exact claim")

            kinds = [event.kind for event in result.events]
            self.assertEqual(kinds.count("agent.summary"), 3)
            self.assertEqual(kinds.count("chair.question"), 1)
            self.assertEqual(kinds.count("agent.clarification"), 1)
            self.assertEqual(kinds.count("chair.draft"), 1)
            self.assertEqual(kinds.count("agent.review"), 2)
            self.assertEqual(kinds.count("chair.final"), 1)
            clarification = next(event for event in result.events if event.kind == "agent.clarification")
            self.assertEqual(clarification.author, "agent-1")
            self.assertFalse(any(event.author == "agent-2" for event in result.events if event.kind == "agent.clarification"))
            chair_phase_prompts = [
                prompt
                for _, prompt in chair.calls
                if not prompt.startswith("PRIVATE WORKSPACE ANALYSIS")
                and not prompt.startswith("Create the public brief")
            ]
            self.assertTrue(all("private analysis from peer-one" not in prompt for prompt in chair_phase_prompts))
            self.assertTrue(all("private analysis from peer-two" not in prompt for prompt in chair_phase_prompts))

            workspace = Path(result.workspace_root)
            expected = [
                workspace / "revision-0/agents/chair/analysis.md",
                workspace / "revision-0/agents/chair/summary.md",
                workspace / "revision-0/agents/chair/chair/questions.json",
                workspace / "revision-0/agents/chair/chair/draft.md",
                workspace / "revision-0/agents/chair/chair/final.md",
                workspace / "revision-0/agents/agent-1/clarification.md",
                workspace / "revision-0/agents/agent-2/review.md",
            ]
            for path in expected:
                self.assertTrue(path.is_file(), path)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(workspace.stat().st_mode), 0o700)
            valid, message = AuditLog.verify(result.audit_path)
            self.assertTrue(valid, message)

    def test_user_update_restarts_private_work_cycle(self) -> None:
        started_count = [0]
        started_lock = threading.Lock()
        all_started = threading.Event()
        release = threading.Event()

        class BlockingProvider(ScriptedProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                if prompt.startswith("PRIVATE WORKSPACE ANALYSIS"):
                    with started_lock:
                        started_count[0] += 1
                        if started_count[0] == 2:
                            all_started.set()
                    if started_count[0] <= 2 and not release.wait(timeout=5):
                        raise RuntimeError("test release timeout")
                return super().generate(system, prompt)

        chair = BlockingProvider("chair")
        peer = BlockingProvider("peer")
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = SidechainOrchestrator(
                (runtime("chair", chair), runtime("peer", peer)),
                chair_id="chair",
                max_clarification_questions=0,
                audit_directory=Path(directory),
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(orchestrator.run, "original task")
                self.assertTrue(orchestrator.wait_until_active(timeout=2))
                self.assertTrue(all_started.wait(timeout=2))
                steering = orchestrator.inject_user_message("include the corrected boundary")
                release.set()
                result = future.result(timeout=10)

            current_summaries = [event for event in result.events if event.kind == "agent.summary"]
            self.assertEqual(len(current_summaries), 2)
            self.assertTrue(all(event.revision == steering.revision for event in current_summaries))
            all_prompts = [prompt for provider in (chair, peer) for _, prompt in provider.calls]
            self.assertTrue(any("include the corrected boundary" in prompt for prompt in all_prompts))
            workspace = Path(result.workspace_root)
            self.assertTrue((workspace / "revision-0/agents/chair/analysis.md").is_file())
            self.assertTrue((workspace / "revision-1/agents/chair/analysis.md").is_file())
            self.assertIn("cycle.superseded", Path(result.audit_path).read_text(encoding="utf-8"))

    def test_user_update_during_final_restarts_before_commit(self) -> None:
        final_started = threading.Event()
        release = threading.Event()

        class BlockingChair(ScriptedProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                if prompt.startswith("FINAL RESULT") and not final_started.is_set():
                    final_started.set()
                    if not release.wait(timeout=5):
                        raise RuntimeError("test final release timeout")
                return super().generate(system, prompt)

        chair = BlockingChair("chair")
        peer = ScriptedProvider("peer")
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = SidechainOrchestrator(
                (runtime("chair", chair), runtime("peer", peer)),
                chair_id="chair",
                max_clarification_questions=0,
                audit_directory=Path(directory),
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(orchestrator.run, "original task")
                self.assertTrue(final_started.wait(timeout=5))
                steering = orchestrator.inject_user_message("new fact before the verdict")
                release.set()
                result = future.result(timeout=10)

            self.assertEqual(result.events[-1].kind, "chair.final")
            self.assertEqual(result.events[-1].revision, steering.revision)
            final_prompts = [prompt for _, prompt in chair.calls if prompt.startswith("FINAL RESULT")]
            self.assertEqual(len(final_prompts), 2)
            self.assertNotIn("new fact before the verdict", final_prompts[0])
            self.assertIn("new fact before the verdict", final_prompts[1])

    def test_failed_provider_closes_session_with_valid_audit(self) -> None:
        class FailingProvider(ScriptedProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                raise RuntimeError("definitive failure")

        agents = (
            runtime("chair", ScriptedProvider("chair")),
            runtime("fail", FailingProvider("fail")),
        )
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = SidechainOrchestrator(agents, "chair", audit_directory=Path(directory))
            with self.assertRaisesRegex(RuntimeError, "definitive failure"):
                orchestrator.run("claim")
            log = next(Path(directory).glob("*.jsonl"))
            valid, message = AuditLog.verify(log)
            self.assertTrue(valid, message)
            self.assertIn("session.failed", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

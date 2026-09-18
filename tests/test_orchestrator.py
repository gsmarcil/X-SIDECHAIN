import json
import stat
import tempfile
import threading
import time
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

    def test_one_failing_agent_abstains_and_the_room_still_decides(self) -> None:
        class FailingProvider(ScriptedProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                raise RuntimeError("provider unavailable")

        agents = (
            runtime("chair", ScriptedProvider("chair")),
            runtime("peer", ScriptedProvider("peer")),
            runtime("down", FailingProvider("down")),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = SidechainOrchestrator(
                agents,
                chair_id="chair",
                max_clarification_questions=0,
                min_agent_quorum=2,
                audit_directory=Path(directory),
            ).run("claim")

            self.assertEqual(result.events[-1].kind, "chair.final")
            self.assertIn("down", result.abstentions)
            self.assertIn("provider unavailable", result.abstentions["down"])
            abstained = [event for event in result.events if event.kind == "agent.abstention"]
            self.assertEqual([event.author for event in abstained], ["down"])
            summaries = [event.author for event in result.events if event.kind == "agent.summary"]
            self.assertEqual(sorted(summaries), ["chair", "peer"])
            reviews = [event.author for event in result.events if event.kind == "agent.review"]
            self.assertEqual(reviews, ["peer"])
            log = Path(result.audit_path).read_text(encoding="utf-8")
            self.assertIn("agent.abstained", log)
            valid, message = AuditLog.verify(result.audit_path)
            self.assertTrue(valid, message)

            # The chair must be told an agent is missing, or silence reads as assent.
            chair_provider = agents[0].provider
            draft_prompt = next(
                prompt for _, prompt in chair_provider.calls if prompt.startswith("CHAIR DRAFT")
            )
            final_prompt_text = next(
                prompt for _, prompt in chair_provider.calls if prompt.startswith("FINAL RESULT")
            )
            for prompt in (draft_prompt, final_prompt_text):
                self.assertIn("AGENTS THAT DID NOT REPORT", prompt)
                self.assertIn("down: RuntimeError: provider unavailable", prompt)
            self.assertIn("never as agreement", draft_prompt)

    def test_chair_failure_is_fatal_even_when_peers_report(self) -> None:
        class FailingChair(ScriptedProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                raise RuntimeError("chair offline")

        agents = (
            runtime("chair", FailingChair("chair")),
            runtime("peer-a", ScriptedProvider("peer-a")),
            runtime("peer-b", ScriptedProvider("peer-b")),
        )
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = SidechainOrchestrator(
                agents, "chair", min_agent_quorum=2, audit_directory=Path(directory)
            )
            with self.assertRaisesRegex(RuntimeError, "cannot continue without its chair"):
                orchestrator.run("claim")

    def test_quorum_failure_names_the_underlying_errors(self) -> None:
        class FailingProvider(ScriptedProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                raise RuntimeError("definitive failure")

        agents = (
            runtime("chair", ScriptedProvider("chair")),
            runtime("down-one", FailingProvider("down-one")),
            runtime("down-two", FailingProvider("down-two")),
        )
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = SidechainOrchestrator(
                agents, "chair", min_agent_quorum=3, audit_directory=Path(directory)
            )
            with self.assertRaisesRegex(RuntimeError, "definitive failure"):
                orchestrator.run("claim")

    def test_oversized_public_brief_is_cut_and_the_full_text_kept_privately(self) -> None:
        class VerboseProvider(ScriptedProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                reply = super().generate(system, prompt)
                if prompt.startswith("Create the public brief"):
                    body = "\n".join(f"CLAIM line {index} padding" for index in range(200))
                    return ModelReply(reply.provider, reply.model, body)
                return reply

        with tempfile.TemporaryDirectory() as directory:
            result = SidechainOrchestrator(
                (runtime("chair", VerboseProvider("chair")), runtime("peer", VerboseProvider("peer"))),
                chair_id="chair",
                max_clarification_questions=0,
                max_public_brief_chars=400,
                audit_directory=Path(directory),
            ).run("claim")

            published = [event for event in result.events if event.kind == "agent.summary"]
            self.assertTrue(published)
            for event in published:
                self.assertLessEqual(len(event.content), 400)
                self.assertIn("truncated", event.content)
            workspace = Path(result.workspace_root)
            full = workspace / "revision-0/agents/peer/summary.full.md"
            self.assertTrue(full.is_file())
            self.assertGreater(len(full.read_text(encoding="utf-8")), 400)
            self.assertIn("workspace.brief_truncated", Path(result.audit_path).read_text(encoding="utf-8"))

    def test_corrections_are_refused_past_the_revision_limit(self) -> None:
        release = threading.Event()
        reached = threading.Event()

        class BlockingProvider(ScriptedProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                if prompt.startswith("CHAIR DRAFT"):
                    reached.set()
                    release.wait(timeout=5)
                return super().generate(system, prompt)

        with tempfile.TemporaryDirectory() as directory:
            orchestrator = SidechainOrchestrator(
                (runtime("chair", BlockingProvider("chair")), runtime("peer", ScriptedProvider("peer"))),
                chair_id="chair",
                max_clarification_questions=0,
                max_revisions=0,
                audit_directory=Path(directory),
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(orchestrator.run, "original task")
                self.assertTrue(reached.wait(timeout=5))
                with self.assertRaisesRegex(RuntimeError, "revision limit reached"):
                    orchestrator.inject_user_message("late correction")
                release.set()
                result = future.result(timeout=10)

            self.assertEqual(result.events[-1].kind, "chair.final")
            self.assertFalse(any(event.kind == "user.steering" for event in result.events))

    def test_corrections_are_refused_when_the_budget_cannot_fund_a_restart(self) -> None:
        release = threading.Event()
        reached = threading.Event()

        class BlockingProvider(ScriptedProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                if prompt.startswith("CHAIR DRAFT"):
                    reached.set()
                    release.wait(timeout=5)
                return super().generate(system, prompt)

        with tempfile.TemporaryDirectory() as directory:
            # Exactly one cycle is affordable, so a restart never is.
            orchestrator = SidechainOrchestrator(
                (runtime("chair", BlockingProvider("chair")), runtime("peer", ScriptedProvider("peer"))),
                chair_id="chair",
                max_clarification_questions=0,
                max_model_calls=8,
                audit_directory=Path(directory),
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(orchestrator.run, "original task")
                self.assertTrue(reached.wait(timeout=5))
                with self.assertRaisesRegex(RuntimeError, "model calls remain"):
                    orchestrator.inject_user_message("late correction")
                release.set()
                result = future.result(timeout=10)

            self.assertEqual(result.events[-1].kind, "chair.final")

    def test_corrections_are_refused_after_the_session_deadline(self) -> None:
        release = threading.Event()
        reached = threading.Event()

        class BlockingProvider(ScriptedProvider):
            def generate(self, system: str, prompt: str) -> ModelReply:
                if prompt.startswith("CHAIR DRAFT"):
                    reached.set()
                    release.wait(timeout=5)
                return super().generate(system, prompt)

        with tempfile.TemporaryDirectory() as directory:
            orchestrator = SidechainOrchestrator(
                (runtime("chair", BlockingProvider("chair")), runtime("peer", ScriptedProvider("peer"))),
                chair_id="chair",
                max_clarification_questions=0,
                session_deadline_seconds=3600,
                audit_directory=Path(directory),
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(orchestrator.run, "original task")
                self.assertTrue(reached.wait(timeout=5))
                orchestrator._deadline = time.monotonic() - 1  # the deadline has passed
                with self.assertRaisesRegex(RuntimeError, "deadline passed"):
                    orchestrator.inject_user_message("late correction")
                release.set()
                result = future.result(timeout=10)

            # The running cycle still finishes, so the session yields a result.
            self.assertEqual(result.events[-1].kind, "chair.final")

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

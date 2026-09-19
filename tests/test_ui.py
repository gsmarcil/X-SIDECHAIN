import json
import os
import re
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
from importlib.resources import files
from pathlib import Path
from urllib.request import Request, urlopen

from x_sidechain.config import load_config
from x_sidechain.models import ModelReply
from x_sidechain.ui import LiveSession, SessionManager, build_ui_server, phase_of

WEB = files("x_sidechain") / "web"


def _request(url, *, token=None, method="GET", body=None, origin=None, host=None):
    headers = {}
    if token is not None:
        headers["X-Sidechain-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    return Request(url, data=data, headers=headers, method=method)


class ServedUITestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = build_ui_server(port=0)
        self.token = self.server.ui_token
        self.host, self.port = self.server.server_address
        self.base = f"http://{self.host}:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class LocalUITests(ServedUITestCase):
    def test_page_is_loopback_only_and_sends_security_headers(self) -> None:
        self.assertEqual(self.host, "127.0.0.1")
        with urlopen(f"{self.base}/", timeout=2) as response:
            body = response.read().decode("utf-8")
        self.assertIn("X-SIDECHAIN", body)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])

    def test_invalid_port_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 0 and 65535"):
            build_ui_server(port=70000)


class APIGuardTests(ServedUITestCase):
    def test_api_without_a_token_is_refused(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urlopen(_request(f"{self.base}/api/config"), timeout=2)
        self.assertEqual(caught.exception.code, 401)

    def test_api_with_a_wrong_token_is_refused(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urlopen(_request(f"{self.base}/api/config", token="not-the-token"), timeout=2)
        self.assertEqual(caught.exception.code, 401)

    def test_api_with_the_issued_token_is_served(self) -> None:
        with urlopen(_request(f"{self.base}/api/config", token=self.token), timeout=2) as response:
            payload = json.loads(response.read())
        self.assertFalse(payload["live"])

    def test_a_rebound_host_header_is_refused(self) -> None:
        # A DNS name that resolves to loopback must not reach the API.
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urlopen(_request(f"{self.base}/api/config", token=self.token,
                             host="attacker.example.com"), timeout=2)
        self.assertEqual(caught.exception.code, 403)

    def test_a_cross_origin_write_is_refused_even_with_the_token(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urlopen(_request(f"{self.base}/api/run", token=self.token, method="POST",
                             body={"task": "x"}, origin="https://evil.example"), timeout=2)
        self.assertEqual(caught.exception.code, 403)

    def test_starting_a_session_without_a_config_is_refused(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urlopen(_request(f"{self.base}/api/run", token=self.token, method="POST",
                             body={"task": "prove it"}), timeout=2)
        self.assertEqual(caught.exception.code, 409)
        self.assertIn("--config", json.loads(caught.exception.read())["error"])

    def test_steering_with_no_session_is_refused(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urlopen(_request(f"{self.base}/api/steer", token=self.token, method="POST",
                             body={"message": "hi"}), timeout=2)
        self.assertEqual(caught.exception.code, 409)

    def test_state_is_idle_before_any_run(self) -> None:
        with urlopen(_request(f"{self.base}/api/state", token=self.token), timeout=2) as response:
            self.assertEqual(json.loads(response.read())["status"], "idle")

    def test_run_forwards_the_selected_agents_and_chair(self) -> None:
        session = unittest.mock.Mock(status="starting", task="prove it")
        with unittest.mock.patch.object(self.server.ui_manager, "start", return_value=session) as start:
            with urlopen(_request(
                f"{self.base}/api/run", token=self.token, method="POST",
                body={"task": "prove it", "agents": ["one", "three"], "chair": "three"},
            ), timeout=2) as response:
                self.assertEqual(response.status, 202)
        start.assert_called_once_with("prove it", ["one", "three"], "three")

    def test_run_rejects_a_non_array_agent_selection(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urlopen(_request(
                f"{self.base}/api/run", token=self.token, method="POST",
                body={"task": "prove it", "agents": "one", "chair": "one"},
            ), timeout=2)
        self.assertEqual(caught.exception.code, 409)
        self.assertIn("array", json.loads(caught.exception.read())["error"])


class ConfigReportTests(unittest.TestCase):
    RAW = {
        "version": 1,
        "providers": {"p": {"protocol": "chat_completions",
                            "base_url": "https://models.example/v1",
                            "auth": {"type": "api_key", "env": "P_KEY"}}},
        "agents": [{"id": "one", "provider": "p", "model": "a", "role": "r1"},
                   {"id": "two", "provider": "p", "model": "b", "role": "r2"},
                   {"id": "three", "provider": "p", "model": "c", "role": "r3"}],
        "chair": "two",
        "max_clarification_questions": 4,
    }

    def _describe(self, raw: dict) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            return SessionManager(load_config(path)).describe_config()

    def test_an_unset_budget_is_reported_as_the_one_a_run_will_use(self) -> None:
        limits = self._describe(self.RAW)["limits"]
        # 3 agents, 4 questions capped at 3: 3*3 + 3 + 2 = 14, three cycles.
        self.assertEqual(limits["cycle_cost"], 14)
        self.assertEqual(limits["max_model_calls"], 42)

    def test_a_configured_budget_is_reported_unchanged(self) -> None:
        limits = self._describe({**self.RAW, "max_model_calls": 20})["limits"]
        self.assertEqual(limits["max_model_calls"], 20)

    def test_only_the_name_of_a_secret_reaches_the_interface(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"P_KEY": "sk-not-for-the-page"}):
            described = self._describe(self.RAW)
        provider = described["providers"][0]
        self.assertEqual(provider["env"], "P_KEY")
        self.assertTrue(provider["env_present"])
        self.assertNotIn("sk-not-for-the-page", json.dumps(described))

    def test_chatgpt_account_status_reaches_the_interface_without_credentials(self) -> None:
        raw = {**self.RAW}
        raw["providers"] = {
            "p": {"protocol": "codex_cli", "auth": {"type": "chatgpt_account"}}
        }
        status = type(
            "Status",
            (),
            {"available": True, "authenticated": True, "method": "chatgpt"},
        )()
        with unittest.mock.patch("x_sidechain.ui.codex_account_status", return_value=status):
            provider = self._describe(raw)["providers"][0]
        self.assertTrue(provider["account_available"])
        self.assertTrue(provider["account_authenticated"])
        self.assertEqual(provider["account_method"], "chatgpt")
        self.assertNotIn("access_token", provider)
        self.assertNotIn("refresh_token", provider)
        self.assertNotIn("credential", provider)


class SessionSelectionTests(unittest.TestCase):
    class _Provider:
        def __init__(self, name: str) -> None:
            self.name = name
            self.calls = []

        def generate(self, system: str, prompt: str) -> ModelReply:
            self.calls.append(prompt)
            if prompt.startswith("PRIVATE WORKSPACE ANALYSIS"):
                text = f"private analysis from {self.name}"
            elif prompt.startswith("Create the public brief"):
                text = f"CLAIM: public summary from {self.name}"
            elif "Return JSON only:" in prompt:
                text = '{"questions": []}'
            elif prompt.startswith("CHAIR DRAFT"):
                text = f"chair draft from {self.name}"
            elif prompt.startswith("REVIEW THE CHAIR DRAFT"):
                text = "VERDICT: APPROVE\nERROR: NONE\nCORRECTION: NONE\nEVIDENCE: test"
            elif prompt.startswith("FINAL RESULT"):
                text = f"final result from {self.name}"
            else:
                raise AssertionError(f"unexpected prompt: {prompt[:80]}")
            return ModelReply(self.name, self.name, text)

    def test_only_the_selected_agents_run_and_the_selected_chair_decides(self) -> None:
        raw = {
            "version": 1,
            "providers": {
                name: {"protocol": "chat_completions", "base_url": "https://models.example/v1"}
                for name in ("p1", "p2", "p3")
            },
            "agents": [
                {"id": "one", "provider": "p1", "model": "m1", "role": "one"},
                {"id": "two", "provider": "p2", "model": "m2", "role": "two"},
                {"id": "three", "provider": "p3", "model": "m3", "role": "three"},
            ],
            "chair": "two",
        }
        providers = {name: self._Provider(name) for name in raw["providers"]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            manager = SessionManager(load_config(path))
            with unittest.mock.patch(
                "x_sidechain.ui.create_provider",
                side_effect=lambda config, model, timeout: providers[config.id],
            ):
                session = manager.start("compare", ["one", "three"], "three")
                deadline = time.monotonic() + 3
                while session.status in {"starting", "running"} and time.monotonic() < deadline:
                    time.sleep(0.01)

        self.assertEqual(session.status, "completed")
        self.assertEqual(session.state()["agents"], ["one", "three"])
        self.assertEqual(session.state()["chair"], "three")
        self.assertEqual(session.result, "final result from p3")
        self.assertEqual(providers["p2"].calls, [])


class FinishedSessionTests(ServedUITestCase):
    """A run that ended before the viewer arrived must still end their stream."""

    class _Snapshot:
        def snapshot(self):
            return {"active": False, "model_calls": 2, "max_model_calls": 9,
                    "revision": 0, "max_revisions": 2, "cycle_cost": 9,
                    "usage": {}, "abstentions": {}, "accepting_input": False}

    def test_a_late_viewer_is_told_the_run_is_over_and_the_stream_closes(self) -> None:
        session = LiveSession(session_id="s1", task="t", orchestrator=self._Snapshot())
        session.status = "completed"
        session.finish("done", {"session_id": "s1", "result": "r", "model_calls": 2})
        self.server.ui_manager._current = session

        request = _request(f"{self.base}/api/events", token=self.token)
        with urlopen(request, timeout=3) as response:
            # Without the replay this read blocks on heartbeats until the timeout.
            body = response.read().decode("utf-8")
        self.assertIn("event: state", body)
        self.assertIn("event: done", body)


class WebAssetTests(unittest.TestCase):
    def test_the_live_layer_is_a_module(self) -> None:
        # app.js is a classic script: sharing one global scope with it made the
        # live layer die on a duplicate declaration before its first statement.
        page = (WEB / "index.html").read_text(encoding="utf-8")
        tags = re.findall(r'<script src="live\.js"[^>]*>', page)
        self.assertEqual(tags, ['<script src="live.js" type="module">'])

    def test_every_element_the_live_layer_addresses_exists_in_the_page(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        script = (WEB / "live.js").read_text(encoding="utf-8")
        wanted = set(re.findall(r'"#([A-Za-z][\w-]*)', script))
        self.assertTrue(wanted, "expected the live layer to address the page")
        missing = sorted(name for name in wanted if f'id="{name}"' not in page)
        self.assertEqual(missing, [], f"live.js addresses ids the page lacks: {missing}")


class PhaseMappingTests(unittest.TestCase):
    def test_progress_text_maps_to_the_phase_being_watched(self) -> None:
        self.assertEqual(phase_of("Revision 0: agents working privately"), 1)
        self.assertEqual(phase_of("Revision 0: chair reading summaries"), 3)
        self.assertEqual(phase_of("Revision 1: 2 targeted clarifications"), 4)
        self.assertEqual(phase_of("Revision 0: chair drafting"), 5)
        self.assertEqual(phase_of("Revision 0: peer review of chair draft"), 6)
        self.assertEqual(phase_of("Revision 0: chair finalizing"), 7)
        self.assertEqual(phase_of("something unmapped"), 1)


if __name__ == "__main__":
    unittest.main()

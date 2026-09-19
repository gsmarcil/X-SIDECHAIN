from __future__ import annotations

import contextlib
import json
import os
import queue
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from x_sidechain.auth import CodexAccountStatusCache
from x_sidechain.config import RunConfig
from x_sidechain.models import DiscussionEvent, cycle_cost, default_call_budget
from x_sidechain.orchestrator import AgentRuntime, SidechainOrchestrator
from x_sidechain.providers import create_provider

HEARTBEAT_SECONDS = 15.0
MAX_BODY_BYTES = 1 * 1024 * 1024

# progress text -> the phase a viewer is watching, so the interface can show a
# step without the orchestrator having to publish one.
PHASES = (
    ("agents working privately", 1),
    ("chair reading summaries", 3),
    ("targeted clarifications", 4),
    ("chair drafting", 5),
    ("peer review", 6),
    ("chair finalizing", 7),
)


def phase_of(progress: str) -> int:
    for needle, phase in PHASES:
        if needle in progress:
            return phase
    return 1


@dataclass
class LiveSession:
    """One run, its published events, and everyone watching it."""

    session_id: str
    task: str
    orchestrator: SidechainOrchestrator
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "starting"
    phase: int = 1
    progress: str = ""
    error: str = ""
    result: str = ""
    audit_path: str = ""
    workspace_root: str = ""
    # The last event of a finished run, kept so a viewer who arrives afterwards
    # is told the run is over instead of waiting on a stream that never speaks.
    terminal: tuple[str, dict[str, Any]] | None = None
    _subs: list[queue.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=512)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, kind: str, payload: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            # A viewer that cannot keep up is dropped rather than stalling the run.
            with contextlib.suppress(queue.Full):
                q.put_nowait((kind, payload))

    def finish(self, kind: str, payload: dict[str, Any]) -> None:
        """Publish the last event of the run and remember it for late viewers."""
        self.terminal = (kind, payload)
        self.publish(kind, payload)

    def state(self) -> dict[str, Any]:
        runtimes = getattr(self.orchestrator, "agents", ())
        return {
            "session_id": self.session_id,
            "task": self.task,
            "status": self.status,
            "phase": self.phase,
            "progress": self.progress,
            "error": self.error,
            "result": self.result,
            "audit_path": self.audit_path,
            "workspace_root": self.workspace_root,
            "agents": [runtime.spec.id for runtime in runtimes],
            "chair": getattr(self.orchestrator, "chair_id", ""),
            "events": list(self.events),
            "spend": self.orchestrator.snapshot(),
        }


class SessionManager:
    """Owns at most one run at a time, as the orchestrator itself does."""

    def __init__(
        self,
        config: RunConfig | None,
        *,
        codex_status_cache: CodexAccountStatusCache | None = None,
    ) -> None:
        self.config = config
        self._codex_status_cache = codex_status_cache or CodexAccountStatusCache()
        self._current: LiveSession | None = None
        self._lock = threading.Lock()

    @property
    def current(self) -> LiveSession | None:
        return self._current

    def describe_config(self) -> dict[str, Any]:
        """Config for the interface. Never a secret: env names and presence only."""
        if self.config is None:
            return {"live": False, "providers": [], "agents": [], "limits": {}}
        providers = []
        for provider in self.config.providers.values():
            env = provider.auth.env or provider.auth.token_env
            described = {
                "id": provider.id,
                "protocol": provider.protocol,
                "base_url": provider.base_url,
                "auth": provider.auth.type,
                "env": env,
                "env_present": bool(env and os.environ.get(env, "").strip()),
                "allow_insecure_http": provider.allow_insecure_http,
                "max_output_tokens": provider.max_output_tokens,
            }
            if provider.auth.type == "chatgpt_account":
                status = self._codex_status_cache.get()
                described["account_available"] = status.available
                described["account_authenticated"] = status.authenticated
                described["account_method"] = status.method
                described["account_checking"] = status.checking
            providers.append(described)
        agents = [
            {"id": a.id, "provider": a.provider, "model": a.model, "role": a.role,
             "chair": a.id == self.config.chair}
            for a in self.config.agents
        ]
        count = len(self.config.agents)
        questions = self.config.max_clarification_questions
        # What a run will actually be bounded by, not the raw setting: an
        # unset budget is derived, and an interface that showed "null" would
        # be hiding the only number that decides when a session stops.
        budget = self.config.max_model_calls
        if budget is None:
            budget = default_call_budget(count, questions)
        return {
            "live": True,
            "providers": providers,
            "agents": agents,
            "chair": self.config.chair,
            "limits": {
                "max_model_calls": budget,
                "cycle_cost": cycle_cost(count, questions),
                "max_clarification_questions": self.config.max_clarification_questions,
                "min_agent_quorum": self.config.min_agent_quorum,
                "max_revisions": self.config.max_revisions,
                "session_deadline_seconds": self.config.session_deadline_seconds,
                "max_public_brief_chars": self.config.max_public_brief_chars,
                "request_timeout_seconds": self.config.request_timeout_seconds,
            },
        }

    def start(self, task: str, agent_ids: list[str], chair: str) -> LiveSession:
        if self.config is None:
            raise ValueError("no configuration loaded; start the UI with --config to run sessions")
        with self._lock:
            if self._current is not None and self._current.status in {"starting", "running"}:
                raise ValueError("a session is already running")

            configured_ids = {a.id for a in self.config.agents}
            requested_ids = set(agent_ids)
            unknown_ids = requested_ids - configured_ids
            if unknown_ids:
                raise ValueError(f"unknown agent id: {sorted(unknown_ids)[0]}")
            chosen = [a for a in self.config.agents if a.id in requested_ids] if agent_ids else list(self.config.agents)
            if len(chosen) < 2:
                raise ValueError("select at least two agents")
            chair_id = chair or self.config.chair
            if chair_id not in {a.id for a in chosen}:
                raise ValueError("the chair must be one of the selected agents")

            runtimes = tuple(
                AgentRuntime(
                    spec=agent,
                    provider=create_provider(
                        self.config.providers[agent.provider],
                        agent.model,
                        self.config.request_timeout_seconds,
                    ),
                )
                for agent in chosen
            )
            quorum = min(self.config.min_agent_quorum, len(chosen))
            session = LiveSession(session_id="", task=task, orchestrator=None)  # type: ignore[arg-type]

            def on_event(event: DiscussionEvent) -> None:
                payload = event.public_dict()
                session.events.append(payload)
                session.publish("room", payload)

            def on_progress(message: str) -> None:
                session.progress = message
                session.phase = phase_of(message)
                session.publish("progress", {"message": message, "phase": session.phase})

            orchestrator = SidechainOrchestrator(
                agents=runtimes,
                chair_id=chair_id,
                max_clarification_questions=self.config.max_clarification_questions,
                max_model_calls=self.config.max_model_calls,
                min_agent_quorum=quorum,
                max_revisions=self.config.max_revisions,
                session_deadline_seconds=self.config.session_deadline_seconds,
                max_public_brief_chars=self.config.max_public_brief_chars,
                progress=on_progress,
                on_event=on_event,
            )
            session.orchestrator = orchestrator
            self._current = session

        def run() -> None:
            session.status = "running"
            try:
                result = session.orchestrator.run(task)
                session.session_id = result.session_id
                session.result = result.synthesis.text
                session.audit_path = result.audit_path
                session.workspace_root = result.workspace_root
                session.status = "completed"
                session.phase = 7
                session.finish("done", {
                    "session_id": result.session_id,
                    "result": result.synthesis.text,
                    "audit_path": result.audit_path,
                    "workspace_root": result.workspace_root,
                    "model_calls": result.model_calls,
                    "usage_totals": result.usage_totals,
                    "abstentions": result.abstentions,
                })
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                session.status = "failed"
                session.error = f"{type(exc).__name__}: {exc}"
                session.finish("failed", {"error": session.error})

        threading.Thread(target=run, name="xsidechain-ui-session", daemon=True).start()
        return session


class LocalUIRequestHandler(SimpleHTTPRequestHandler):
    """Serve the packaged UI and its local API, on loopback, behind a token."""

    manager: SessionManager
    token: str
    bound_port: int

    def __init__(self, *args, directory: str | None = None, **kwargs) -> None:
        web_root = Path(str(files("x_sidechain").joinpath("web")))
        super().__init__(*args, directory=directory or str(web_root), **kwargs)

    # ---------- security ----------

    def _host_ok(self) -> bool:
        """Reject a rebound DNS name that resolves to loopback."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in {"127.0.0.1", "::1", "localhost"}

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.hostname in {"127.0.0.1", "::1", "localhost"} and parsed.port == self.bound_port

    def _token_ok(self, query_token: str | None) -> bool:
        sent = self.headers.get("X-Sidechain-Token") or query_token or ""
        return secrets.compare_digest(sent, self.token)

    def _guard(self, query_token: str | None, *, write: bool) -> bool:
        if not self._host_ok():
            self._json(403, {"error": "this interface answers on loopback only"})
            return False
        if write and not self._origin_ok():
            self._json(403, {"error": "cross-origin request refused"})
            return False
        if not self._token_ok(query_token):
            self._json(401, {"error": "missing or wrong session token"})
            return False
        return True

    # ---------- plumbing ----------

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        super().end_headers()

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body must be between 1 byte and 1 MiB")
        raw = self.rfile.read(length)
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("request body must be a JSON object")
        return parsed

    def log_message(self, format: str, *args: object) -> None:
        # The token rides in the query string of the event stream; keep it out of logs.
        scrubbed = tuple(
            (a.split("?", 1)[0] + "?…") if isinstance(a, str) and "token=" in a else a
            for a in args
        )
        super().log_message("UI " + format, *scrubbed)

    # ---------- routes ----------

    def do_GET(self) -> None:  # noqa: N802
        path, _, query = self.path.partition("?")
        params = dict(
            part.split("=", 1) if "=" in part else (part, "")
            for part in query.split("&") if part
        )
        if not path.startswith("/api/"):
            super().do_GET()
            return
        if not self._guard(params.get("token"), write=False):
            return
        if path == "/api/config":
            self._json(200, self.manager.describe_config())
        elif path == "/api/state":
            session = self.manager.current
            self._json(200, session.state() if session else {"status": "idle"})
        elif path == "/api/events":
            self._stream()
        else:
            self._json(404, {"error": "no such endpoint"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.partition("?")[0]
        if not self._guard(None, write=True):
            return
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        try:
            if path == "/api/run":
                task = str(body.get("task", "")).strip()
                if not task:
                    raise ValueError("a task is required")
                raw_agents = body.get("agents", [])
                if not isinstance(raw_agents, list) or not all(
                    isinstance(agent_id, str) for agent_id in raw_agents
                ):
                    raise ValueError("agents must be an array of agent ids")
                raw_chair = body.get("chair", "")
                if not isinstance(raw_chair, str):
                    raise ValueError("chair must be an agent id")
                session = self.manager.start(
                    task,
                    [agent_id.strip() for agent_id in raw_agents],
                    raw_chair.strip(),
                )
                self._json(202, {"status": session.status, "task": session.task})
            elif path == "/api/steer":
                session = self._require_session()
                event = session.orchestrator.inject_user_message(str(body.get("message", "")))
                self._json(200, {"revision": event.revision, "sequence": event.sequence})
            elif path == "/api/finish":
                session = self._require_session()
                session.orchestrator.request_finish()
                self._json(200, {"accepting_input": False})
            else:
                self._json(404, {"error": "no such endpoint"})
        except (ValueError, RuntimeError) as exc:
            self._json(409, {"error": str(exc)})

    def _require_session(self) -> LiveSession:
        session = self.manager.current
        if session is None or session.status not in {"starting", "running"}:
            raise RuntimeError("no session is running")
        return session

    def _stream(self) -> None:
        session = self.manager.current
        if session is None:
            self._json(404, {"error": "no session is running"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        q = session.subscribe()
        try:
            self._send_event("state", session.state())
            ended = session.terminal
            if ended is not None:
                # The run finished before this viewer connected: say so and hang up.
                self._send_event(*ended)
                return
            while True:
                try:
                    kind, payload = q.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self._send_event(kind, payload)
                if kind in {"done", "failed"}:
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            session.unsubscribe(q)

    def _send_event(self, kind: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"event: {kind}\ndata: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


def build_ui_server(
    port: int = 8765,
    config: RunConfig | None = None,
    token: str | None = None,
) -> ThreadingHTTPServer:
    if port < 0 or port > 65535:
        raise ValueError("UI port must be between 0 and 65535")
    manager = SessionManager(config)
    issued = token or secrets.token_urlsafe(32)

    class Handler(LocalUIRequestHandler):
        pass

    Handler.manager = manager
    Handler.token = issued
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    Handler.bound_port = server.server_address[1]
    server.ui_token = issued  # type: ignore[attr-defined]
    server.ui_manager = manager  # type: ignore[attr-defined]
    return server


def serve_ui(
    port: int = 8765,
    open_browser: bool = True,
    config: RunConfig | None = None,
) -> None:
    """Run the interface on loopback. No provider secret ever reaches the browser."""
    server = build_ui_server(port, config)
    host, assigned_port = server.server_address
    url = f"http://{host}:{assigned_port}/?token={server.ui_token}"  # type: ignore[attr-defined]
    print(f"X-SIDECHAIN UI: {url}")
    if config is None:
        print("Prototype mode: no --config, so sessions cannot be started from here.")
    else:
        print(f"Live mode: {len(config.agents)} agents, chair {config.chair}.")
    print("The link carries a one-off token; anything without it is refused.")
    if open_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with contextlib.suppress(Exception):
            server.server_close()

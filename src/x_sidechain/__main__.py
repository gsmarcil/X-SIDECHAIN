from __future__ import annotations

import argparse
import json
import select
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from x_sidechain.audit import AuditLog
from x_sidechain.auth import oauth_device_login
from x_sidechain.config import RunConfig, load_config
from x_sidechain.models import DiscussionEvent, DiscussionResult
from x_sidechain.orchestrator import AgentRuntime, SidechainOrchestrator
from x_sidechain.providers import create_provider
from x_sidechain.ui import serve_ui


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="x-sidechain",
        description="Provider-agnostic, evidence-first multi-agent deliberation engine",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="run every configured agent")
    run.add_argument("--config", required=True, metavar="FILE.json")
    prompt = run.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file", metavar="FILE")
    run.add_argument(
        "--interactive",
        action="store_true",
        help="accept steering lines from stdin while agents are discussing",
    )

    validate = subcommands.add_parser("validate-config", help="validate configuration without API calls")
    validate.add_argument("--config", required=True, metavar="FILE.json")

    providers = subcommands.add_parser("providers", help="list providers, protocols, and auth modes")
    providers.add_argument("--config", required=True, metavar="FILE.json")

    auth = subcommands.add_parser("auth", help="provider account authentication")
    auth_subcommands = auth.add_subparsers(dest="auth_command", required=True)
    login = auth_subcommands.add_parser("login", help="run an official OAuth Device Flow")
    login.add_argument("provider")
    login.add_argument("--config", required=True, metavar="FILE.json")

    verify = subcommands.add_parser("verify", help="verify a tamper-evident audit log")
    verify.add_argument("audit_log", metavar="AUDIT.jsonl")

    ui = subcommands.add_parser("ui", help="run the local HTML interface prototype")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument(
        "--no-browser",
        action="store_true",
        help="serve the UI without opening the default browser",
    )
    return parser


def _runtimes(config: RunConfig) -> tuple[AgentRuntime, ...]:
    return tuple(
        AgentRuntime(
            spec=agent,
            provider=create_provider(
                config.providers[agent.provider],
                agent.model,
                config.request_timeout_seconds,
            ),
        )
        for agent in config.agents
    )


def _task(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        return args.prompt
    try:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read prompt file: {exc}") from exc


def _show_event(event: DiscussionEvent) -> None:
    print(
        f"\n[{event.sequence} · revision {event.revision} · {event.author} · {event.kind}]"
        f"\n{event.content}",
        flush=True,
    )


def _interactive_run(orchestrator: SidechainOrchestrator, task: str) -> DiscussionResult:
    if not sys.stdin.isatty():
        raise ValueError("--interactive requires a terminal on stdin")
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="xsidechain-session") as pool:
        future = pool.submit(orchestrator.run, task)
        if not orchestrator.wait_until_active(timeout=10):
            return future.result()
        print(
            "[x-sidechain] Live input enabled. Type a correction or addition and press Enter. "
            "Use /finish to close input and let the chaired workflow finish.",
            flush=True,
        )
        accepting = True
        while not future.done():
            if not accepting:
                # Input is closed; wait for the chaired cycle rather than reading stdin.
                time.sleep(0.2)
                continue
            readable, _, _ = select.select([sys.stdin], [], [], 0.2)
            if not readable:
                continue
            line = sys.stdin.readline()
            if not line:
                accepting = False
                continue
            message = line.strip()
            if not message:
                continue
            if message == "/finish":
                accepting = False
                try:
                    orchestrator.request_finish()
                except RuntimeError as exc:
                    print(f"[x-sidechain] {exc}", flush=True)
                else:
                    print(
                        "[x-sidechain] Input closed. Finishing the current chaired cycle.",
                        flush=True,
                    )
                continue
            try:
                orchestrator.inject_user_message(message)
            except (RuntimeError, ValueError) as exc:
                # A late keystroke or a refused correction (revision cap, deadline,
                # remaining budget) must never discard a session already paid for.
                print(f"[x-sidechain] correction refused: {exc}", flush=True)
                accepting = isinstance(exc, ValueError)
        return future.result()


def main() -> int:
    args = _parser().parse_args()
    if args.command == "ui":
        try:
            serve_ui(port=args.port, open_browser=not args.no_browser)
            return 0
        except (OSError, ValueError) as exc:
            print(f"ERROR: {exc}")
            return 2
    if args.command == "verify":
        valid, message = AuditLog.verify(args.audit_log)
        print(("PASS" if valid else "FAIL") + f": {message}")
        return 0 if valid else 1

    try:
        config = load_config(args.config)
        if args.command == "validate-config":
            print(
                f"PASS: {len(config.providers)} providers, {len(config.agents)} agents, "
                f"chair={config.chair}, quorum={config.min_agent_quorum}, "
                f"max_revisions={config.max_revisions}"
            )
            return 0
        if args.command == "providers":
            for provider in config.providers.values():
                print(f"{provider.id}\t{provider.protocol}\t{provider.auth.type}\t{provider.base_url}")
            return 0
        if args.command == "auth" and args.auth_command == "login":
            if args.provider not in config.providers:
                raise ValueError(f"unknown provider: {args.provider}")
            print(oauth_device_login(config.providers[args.provider]))
            return 0
        if args.command == "run":
            orchestrator = SidechainOrchestrator(
                agents=_runtimes(config),
                chair_id=config.chair,
                max_clarification_questions=config.max_clarification_questions,
                max_model_calls=config.max_model_calls,
                min_agent_quorum=config.min_agent_quorum,
                max_revisions=config.max_revisions,
                session_deadline_seconds=config.session_deadline_seconds,
                max_public_brief_chars=config.max_public_brief_chars,
                progress=lambda message: print(f"[x-sidechain] {message}", flush=True),
                on_event=_show_event,
            )
            task = _task(args)
            result = (
                _interactive_run(orchestrator, task)
                if args.interactive
                else orchestrator.run(task)
            )
            print(
                json.dumps(
                    {
                        "session_id": result.session_id,
                        "audit_path": result.audit_path,
                        "workspace_root": result.workspace_root,
                        "model_calls": result.model_calls,
                        "usage_totals": result.usage_totals,
                        "abstentions": result.abstentions,
                    },
                    indent=2,
                )
            )
            print("\n" + result.synthesis.text)
            return 0
    except KeyboardInterrupt:
        print("\nERROR: interrupted; in-flight provider calls may still be billed")
        return 130
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

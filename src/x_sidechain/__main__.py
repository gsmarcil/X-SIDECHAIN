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
    print(f"\n[{event.sequence} · {event.author} · {event.kind}]\n{event.content}", flush=True)


def _interactive_run(orchestrator: SidechainOrchestrator, task: str) -> DiscussionResult:
    if not sys.stdin.isatty():
        raise ValueError("--interactive requires a terminal on stdin")
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="xsidechain-session") as pool:
        future = pool.submit(orchestrator.run, task)
        if not orchestrator.wait_until_active(timeout=10):
            return future.result()
        print(
            "[x-sidechain] Live input enabled. Type a correction or addition and press Enter. "
            "Use /finish to synthesize now.",
            flush=True,
        )
        while not future.done():
            readable, _, _ = select.select([sys.stdin], [], [], 0.2)
            if not readable:
                continue
            line = sys.stdin.readline()
            if not line:
                while not future.done():
                    time.sleep(0.1)
                break
            message = line.strip()
            if not message:
                continue
            if message == "/finish":
                orchestrator.request_finish()
                continue
            orchestrator.inject_user_message(message)
        return future.result()


def main() -> int:
    args = _parser().parse_args()
    if args.command == "verify":
        valid, message = AuditLog.verify(args.audit_log)
        print(("PASS" if valid else "FAIL") + f": {message}")
        return 0 if valid else 1

    try:
        config = load_config(args.config)
        if args.command == "validate-config":
            print(
                f"PASS: {len(config.providers)} providers, {len(config.agents)} agents, "
                f"synthesizer={config.synthesizer}"
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
                synthesizer_id=config.synthesizer,
                contributions_per_agent=config.contributions_per_agent,
                max_model_calls=config.max_model_calls,
                progress=lambda message: print(f"[x-sidechain] {message}", flush=True),
                on_event=_show_event,
            )
            task = _task(args)
            result = (
                _interactive_run(orchestrator, task)
                if args.interactive
                else orchestrator.run(task)
            )
            print(json.dumps({"session_id": result.session_id, "audit_path": result.audit_path}, indent=2))
            print("\n" + result.synthesis.text)
            return 0
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

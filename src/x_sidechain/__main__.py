from __future__ import annotations

import argparse
import json

from x_sidechain.audit import AuditLog
from x_sidechain.config import AppConfig
from x_sidechain.models import AgentSpec
from x_sidechain.orchestrator import SidechainOrchestrator
from x_sidechain.prompts import EXPLORER_ROLE, VALIDATOR_ROLE
from x_sidechain.providers import create_provider


def _agent(value: str) -> tuple[str, str]:
    provider, separator, model = value.partition(":")
    if not separator or not provider or not model:
        raise argparse.ArgumentTypeError("agent must be PROVIDER:MODEL")
    return provider, model


def _parser() -> argparse.ArgumentParser:
    defaults = AppConfig()
    parser = argparse.ArgumentParser(prog="x-sidechain")
    parser.add_argument("--verify", metavar="AUDIT.jsonl", help="verify a tamper-evident audit log")
    parser.add_argument("--prompt", help="run headless with this task instead of starting the GUI")
    parser.add_argument(
        "--agent-a",
        type=_agent,
        default=(defaults.agent_a_provider, defaults.agent_a_model),
        metavar="PROVIDER:MODEL",
    )
    parser.add_argument(
        "--agent-b",
        type=_agent,
        default=(defaults.agent_b_provider, defaults.agent_b_model),
        metavar="PROVIDER:MODEL",
    )
    parser.add_argument("--synthesizer", choices=("a", "b"), default="a")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.verify:
        valid, message = AuditLog.verify(args.verify)
        print(("PASS" if valid else "FAIL") + f": {message}")
        return 0 if valid else 1
    if not args.prompt:
        from x_sidechain.ui import launch

        launch()
        return 0

    provider_a_name, model_a = args.agent_a
    provider_b_name, model_b = args.agent_b
    result = SidechainOrchestrator(
        provider_a=create_provider(provider_a_name, model_a),
        provider_b=create_provider(provider_b_name, model_b),
        agent_a=AgentSpec("Agent A", provider_a_name, model_a, EXPLORER_ROLE),
        agent_b=AgentSpec("Agent B", provider_b_name, model_b, VALIDATOR_ROLE),
        synthesizer=args.synthesizer,
        progress=lambda message: print(f"[x-sidechain] {message}", flush=True),
    ).run(args.prompt)
    print(json.dumps({"session_id": result.session_id, "audit_path": result.audit_path}, indent=2))
    print("\n" + result.synthesis.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


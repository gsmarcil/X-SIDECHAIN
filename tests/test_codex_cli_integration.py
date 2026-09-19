import json
import io
import os
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from x_sidechain.__main__ import main


class CodexCLIIntegrationTests(unittest.TestCase):
    def test_account_backed_provider_completes_a_chair_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "codex"
            fake.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import pathlib
                    import sys

                    if sys.argv[1:] == ["login", "status"]:
                        print("Logged in using ChatGPT", file=sys.stderr)
                        raise SystemExit(0)

                    prompt = sys.stdin.read()
                    output = pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1])
                    if "FINAL RESULT" in prompt:
                        answer = "Verified joint result from account-backed agents."
                    elif "REVIEW THE CHAIR DRAFT" in prompt:
                        answer = "VERDICT: APPROVE\\nERROR: NONE\\nCORRECTION: NONE\\nEVIDENCE: fixture"
                    elif "PRIVATE WORKSPACE ANALYSIS" in prompt:
                        answer = "CLAIM: private analysis\\nEVIDENCE: fixture"
                    elif "Create the public brief" in prompt:
                        answer = "CLAIM: public brief\\nEVIDENCE: fixture"
                    elif "Return JSON only:" in prompt:
                        answer = '{"questions": []}'
                    elif "CHAIR DRAFT" in prompt:
                        answer = "Joint draft based on both briefs."
                    else:
                        raise SystemExit(3)
                    output.write_text(answer, encoding="utf-8")
                    """
                ),
                encoding="utf-8",
            )
            fake.chmod(0o755)
            config = {
                "version": 1,
                "providers": {
                    "account": {
                        "protocol": "codex_cli",
                        "auth": {"type": "chatgpt_account"},
                    }
                },
                "agents": [
                    {"id": "one", "provider": "account", "model": "gpt-test", "role": "one"},
                    {"id": "two", "provider": "account", "model": "gpt-test", "role": "two"},
                ],
                "chair": "two",
                "max_clarification_questions": 0,
                "max_model_calls": 8,
                "request_timeout_seconds": 10,
                "min_agent_quorum": 2,
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            argv = [
                "x-sidechain",
                "run",
                "--config",
                str(config_path),
                "--prompt",
                "Reach a joint answer.",
            ]
            stdout = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {"PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}"},
                    clear=False,
                ),
                patch("x_sidechain.audit.Path.home", return_value=root),
                patch.object(sys, "argv", argv),
                redirect_stdout(stdout),
            ):
                returncode = main()

        output = stdout.getvalue()
        self.assertEqual(returncode, 0, output)
        self.assertIn("Verified joint result from account-backed agents.", output)
        self.assertIn('"model_calls": 8', output)


if __name__ == "__main__":
    unittest.main()

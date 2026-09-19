import os
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "install.sh"
GUIDE = REPO / "INSTALL.md"
MAKEFILE = REPO / "Makefile"


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = INSTALLER.read_text(encoding="utf-8")
        self.guide = GUIDE.read_text(encoding="utf-8")

    def test_the_installer_is_executable_and_parses(self) -> None:
        self.assertTrue(os.access(INSTALLER, os.X_OK), "install.sh must be executable")
        subprocess.run(["sh", "-n", str(INSTALLER)], check=True, capture_output=True)

    def test_it_accepts_every_flag_the_guide_documents(self) -> None:
        documented = set(re.findall(r"`(--[a-z-]+)`", self.guide))
        documented.discard("--config")      # a flag of the program, not the installer
        documented.discard("--no-browser")
        documented.discard("--port")
        usage = subprocess.run(
            ["sh", str(INSTALLER), "--help"], capture_output=True, text=True, check=True
        ).stdout
        missing = sorted(flag for flag in documented if flag not in usage)
        self.assertEqual(missing, [], f"install.sh --help omits: {missing}")

    def test_an_unknown_flag_is_refused(self) -> None:
        result = subprocess.run(
            ["sh", str(INSTALLER), "--not-a-flag"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option", result.stderr)

    def test_it_runs_nothing_as_root(self) -> None:
        # sudo may be named in advice; it must never be a command the script runs.
        executed = [
            line for line in self.script.splitlines()
            if re.match(r"\s*sudo\b", line) and '"' not in line.split("sudo")[0]
        ]
        self.assertEqual(executed, [], f"install.sh would run sudo: {executed}")

    def test_the_step_numbering_the_guide_cites_is_real(self) -> None:
        self.assertIn("STEPS=8", self.script)
        # Troubleshooting sends the reader to step [5/8] for setuptools.
        self.assertIn("[5/8]", self.guide)
        self.assertRegex(self.script, r'step 5 "Upgrading pip, setuptools and wheel"')
        numbered = re.findall(r"^step (\d) ", self.script, re.MULTILINE)
        self.assertEqual(numbered, [str(n) for n in range(1, 9)])

    def test_every_make_target_the_guide_lists_exists(self) -> None:
        makefile = MAKEFILE.read_text(encoding="utf-8")
        targets = set(re.findall(r"^([a-z]+):", makefile, re.MULTILINE))
        cited = set(re.findall(r"^make ([a-z]+)", self.guide, re.MULTILINE))
        self.assertTrue(cited, "expected the guide to cite make targets")
        self.assertEqual(sorted(cited - targets), [])

    def test_the_installed_tree_is_never_left_outside_the_repository(self) -> None:
        # Everything it writes is derived from the script's own directory.
        for variable in ("venv=", "config=", "example="):
            line = next(l for l in self.script.splitlines() if l.startswith(variable))
            self.assertIn("$repo", line, f"{variable} must stay inside the repo: {line}")


if __name__ == "__main__":
    unittest.main()

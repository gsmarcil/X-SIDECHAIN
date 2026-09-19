import configparser
import os  # noqa: F401 - patched by name in the XDG test
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from importlib.resources import files
from pathlib import Path

from x_sidechain import __version__
from x_sidechain.__main__ import ui_config_path

REPO = Path(__file__).resolve().parent.parent
PACKAGING = REPO / "packaging"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


class VersionTests(unittest.TestCase):
    def test_the_package_is_the_only_place_the_version_is_written(self) -> None:
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = { attr = "x_sidechain.__version__" }', pyproject)
        self.assertNotIn(f'version = "{__version__}"', pyproject)

    def test_the_command_reports_its_version(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "x_sidechain", "--version"],
            capture_output=True, text=True, check=True,
            cwd=REPO, env={"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(result.stdout.strip(), f"x-sidechain {__version__}")

    def test_the_changelog_documents_this_version(self) -> None:
        changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {__version__}", changelog)


class DesktopLaunchTests(unittest.TestCase):
    def test_a_bare_ui_launch_looks_under_xdg_config_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with unittest.mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": directory}):
                self.assertEqual(
                    ui_config_path(), Path(directory) / "x-sidechain" / "config.json"
                )

    def test_the_desktop_entry_starts_the_interface_with_no_arguments(self) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(PACKAGING / "x-sidechain.desktop", encoding="utf-8")
        entry = parser["Desktop Entry"]
        # A launcher passes nothing, so Exec must be enough on its own.
        self.assertEqual(entry["Exec"], "x-sidechain ui")
        self.assertEqual(entry["Icon"], "x-sidechain")
        self.assertEqual(entry["Type"], "Application")
        for key in ("Categories", "Keywords"):
            self.assertTrue(entry[key].endswith(";"), f"{key} must end with a semicolon")


class PackagedAssetTests(unittest.TestCase):
    def test_every_icon_size_the_package_installs_exists(self) -> None:
        missing = [
            size for size in ICON_SIZES
            if not (PACKAGING / "icons" / f"x-sidechain-{size}.png").is_file()
        ]
        self.assertEqual(missing, [])

    def test_the_build_script_installs_each_of_those_sizes(self) -> None:
        script = (PACKAGING / "build-deb.sh").read_text(encoding="utf-8")
        self.assertIn(" ".join(str(size) for size in ICON_SIZES), script)

    def test_the_page_icon_ships_with_the_package(self) -> None:
        # The favicon is a file now, so package-data has to carry images.
        page = (files("x_sidechain") / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="favicon.png"', page)
        self.assertTrue((files("x_sidechain") / "web" / "favicon.png").is_file())
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"web/*.png"', pyproject)

    def test_the_license_is_declared_and_present(self) -> None:
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('license = "Apache-2.0"', pyproject)
        license_text = (REPO / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0", license_text)
        self.assertNotIn("[name of copyright owner]", license_text)
        copyright_file = (PACKAGING / "copyright").read_text(encoding="utf-8")
        self.assertIn("License: Apache-2.0", copyright_file)


if __name__ == "__main__":
    unittest.main()

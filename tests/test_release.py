"""Keep the checked-in installer wheel in sync with its source and version."""
from email.parser import Parser
from pathlib import Path
import re
import unittest
from zipfile import ZipFile

from smith_agents import __version__

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless((ROOT / "release").is_dir(), "release artifacts belong to the repository")
class ReleaseTests(unittest.TestCase):
    def test_release_version_matches_package_and_project(self):
        wheels = list((ROOT / "release").glob("smith_agents-*.whl"))
        self.assertEqual([p.name for p in wheels], [f"smith_agents-{__version__}-py3-none-any.whl"])
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'^version = "([^"]+)"', project, re.M)[1], __version__)
        with ZipFile(wheels[0]) as wheel:
            metadata = Parser().parsestr(wheel.read(f"smith_agents-{__version__}.dist-info/METADATA").decode())
        self.assertEqual(metadata["Version"], __version__)

    def test_release_contains_current_code_and_resources(self):
        with ZipFile(ROOT / "release" / f"smith_agents-{__version__}-py3-none-any.whl") as wheel:
            names = set(wheel.namelist())
            for name in sorted(names):
                if name.startswith(("smith_agents/", "claude_widget/")) and not name.endswith("/"):
                    with self.subTest(file=name):
                        self.assertEqual(wheel.read(name), (ROOT / name).read_bytes(),
                                         "Rebuild the release wheel after changing package files.")
            for package in ("smith_agents", "claude_widget"):
                for path in (ROOT / package).rglob("*"):
                    relative = path.relative_to(ROOT)
                    if (not path.is_file() or "__pycache__" in relative.parts or "previews" in relative.parts
                            or path.name == "ANIMATION_CHECKPOINT.md" or path.name.startswith(".")):
                        continue
                    self.assertIn(relative.as_posix(), names, "New package resources must ship in the wheel.")


class InstallerTests(unittest.TestCase):
    def test_installers_pin_the_same_release(self):
        shell = (ROOT / "install.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "install.ps1").read_text(encoding="utf-8")
        version = re.search(r"^smith_version=(\S+)$", shell, re.M)[1]
        checksum = re.search(r"^smith_sha256=([0-9a-f]{64})$", shell, re.M)[1]
        self.assertEqual(version, __version__, "Point the installers at the new release.")
        self.assertIn(f'$smithVersion = "{version}"', powershell)
        self.assertIn(f'$smithSha256 = "{checksum}"', powershell)
        for script in (shell, powershell):
            self.assertNotIn("master.zip", script)
            self.assertIn("releases/download/v", script)


if __name__ == "__main__":
    unittest.main()

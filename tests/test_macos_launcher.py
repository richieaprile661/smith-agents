import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import tempfile
import unittest

from smith_agents.macos_launcher import create_launcher, MARKER


class MacLauncherTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX launcher")
    def test_launcher_preserves_paths_and_arguments_with_spaces_and_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = root / "python's & interpreter"
            recorded = root / "arguments.txt"
            python.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > " + shlex.quote(str(recorded)) + "\n")
            python.chmod(0o755)
            app = create_launcher(python, root / "Smith's data", root / "My Apps")
            subprocess.run([str(app / "Contents/MacOS/Smith Agents"), "--version"], check=True)
            self.assertEqual(recorded.read_text().splitlines(), ["-m", "smith_agents", "--version"])

    def test_reinstall_updates_only_the_managed_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_launcher(root / "first-python", root / "data", root / "Apps")
            replacement = create_launcher(root / "second-python", root / "data", root / "Apps")
            self.assertEqual(app, replacement)
            self.assertIn("second-python", (app / "Contents/MacOS/Smith Agents").read_text())
            self.assertTrue(plistlib.loads((app / "Contents/Info.plist").read_bytes())[MARKER])
            self.assertFalse(list((root / "Apps").glob(".smith-launcher-*")))

    def test_existing_standalone_app_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "Apps/Smith Agents.app/Contents"
            existing.mkdir(parents=True)
            original = plistlib.dumps({"CFBundleIdentifier": "standalone.app"})
            (existing / "Info.plist").write_bytes(original)
            app = create_launcher(root / "python", root / "data", root / "Apps")
            self.assertEqual(app.name, "Smith Agents Launcher.app")
            self.assertEqual((existing / "Info.plist").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

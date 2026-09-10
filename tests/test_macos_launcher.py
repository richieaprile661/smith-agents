import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import time
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

    @unittest.skipUnless(sys.platform == "darwin", "Finder launch requires macOS")
    def test_finder_preserves_installer_architecture(self):
        # A universal Python follows the launcher's architecture, but pip's
        # native extensions were installed for the installer's architecture.
        with tempfile.TemporaryDirectory(prefix="smith-architecture-") as directory:
            root = Path(directory)
            python = root / "interpreter"
            recorded = root / "architecture.txt"
            python.write_text("#!/bin/sh\n/usr/bin/arch > " + shlex.quote(str(recorded)) + "\n")
            python.chmod(0o755)
            expected = subprocess.check_output(["/usr/bin/arch"], text=True).strip()
            app = create_launcher(python, root / "data", root / "Apps")
            plist = app / "Contents/Info.plist"
            info = plistlib.loads(plist.read_bytes())
            info["CFBundleIdentifier"] = "smith.test." + root.name
            plist.write_bytes(plistlib.dumps(info))
            try:
                subprocess.run(["open", "-n", str(app)], check=True, timeout=10)
                deadline = time.monotonic() + 10
                actual = ""
                while time.monotonic() < deadline:
                    actual = recorded.read_text().strip() if recorded.exists() else ""
                    if actual:
                        break
                    time.sleep(.1)
                self.assertEqual(actual, expected)
            finally:
                registry = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
                subprocess.run([registry, "-u", str(app)], capture_output=True, timeout=10)

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

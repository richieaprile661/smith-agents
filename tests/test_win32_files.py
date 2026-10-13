"""Reading a Windows process's open files without killing threads."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from smith_agents import codex_sessions

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(sys.platform == "win32", "Windows handles")
class OpenFiles(unittest.TestCase):
    def test_a_disk_file_the_process_holds_is_listed_and_a_directory_is_not(self):
        from smith_agents.win32_files import open_files
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rollout-test.jsonl"
            with open(path, "w", encoding="utf-8"):
                found = {os.path.normcase(p) for p in open_files(os.getpid())}
            self.assertIn(os.path.normcase(os.path.realpath(path)), found)
            self.assertNotIn(os.path.normcase(os.path.realpath(os.getcwd())), found)

    def test_an_idle_pipe_with_a_blocked_reader_is_passed_over_at_once(self):
        """The handle that made psutil kill its thread, as Codex's stdin is."""
        code = ("import os, threading, time\n"
                "from smith_agents.win32_files import open_files\n"
                "r, w = os.pipe()\n"
                "threading.Thread(target=os.read, args=(r, 1), daemon=True).start()\n"
                "time.sleep(.3)\n"
                "start = time.perf_counter()\n"
                "open_files(os.getpid())\n"
                "print(time.perf_counter() - start)\n"
                "os.write(w, b'x')\n")
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(float(result.stdout), 2.0)

    def test_a_process_that_cannot_be_opened_is_an_error_not_an_empty_list(self):
        from smith_agents.win32_files import open_files
        with self.assertRaises(OSError):
            open_files(0)                       # the System Idle Process

    def test_the_codex_scan_never_uses_psutils_open_files(self):
        process = Mock(pid=os.getpid(), open_files=Mock(side_effect=AssertionError("psutil")))
        self.assertIsInstance(codex_sessions.open_files(process), list)
        process.open_files.assert_not_called()


class OtherSystems(unittest.TestCase):
    def test_elsewhere_the_codex_scan_still_asks_psutil(self):
        process = Mock(open_files=Mock(return_value=[Mock(path="/tmp/rollout-a.jsonl")]))
        with patch.object(codex_sessions.sys, "platform", "darwin"):
            self.assertEqual(codex_sessions.open_files(process), ["/tmp/rollout-a.jsonl"])


if __name__ == "__main__":
    unittest.main()

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from smith_agents.context_bridge import atomic_json, capture, read_capacity
from tools.configure_context import configure


class ContextBridgeTests(unittest.TestCase):
    def test_main_and_subagent_capacities_are_session_specific(self):
        with tempfile.TemporaryDirectory() as directory:
            capture({"session_id": "one", "model": {"id": "opus"},
                     "context_window": {"context_window_size": 1000000}}, directory, "main")
            capture({"session_id": "one", "tasks": [
                {"id": "child", "model": "haiku", "contextWindowSize": 200000}]}, directory, "subagents")
            self.assertEqual(read_capacity(directory, {"id": "one"}, "opus"), 1000000)
            self.assertEqual(read_capacity(directory, {"id": "agent-child", "parent": "one", "sub": True}, "haiku"), 200000)
            self.assertIsNone(read_capacity(directory, {"id": "two"}, "opus"))
            self.assertIsNone(read_capacity(directory, {"id": "one"}, "new-model"))
            self.assertIsNone(read_capacity(directory, {"id": "agent-child", "parent": "two", "sub": True}))

    def test_window_selector_matches_api_model_without_guessing_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            capture({"session_id": "one", "model": {"id": "claude-opus-5[1m]"},
                     "context_window": {"context_window_size": 1000000}}, directory, "main")
            capture({"session_id": "one", "tasks": [
                {"id": "child", "model": "claude-opus-5[1M]", "contextWindowSize": 200000}
            ]}, directory, "subagents")
            self.assertEqual(read_capacity(directory, {"id": "one"}, "claude-opus-5"), 1000000)
            child = {"id": "agent-child", "parent": "one", "sub": True}
            self.assertEqual(read_capacity(directory, child, "claude-opus-5"), 200000)
            self.assertIsNone(read_capacity(directory, child, "claude-sonnet-5"))
            self.assertIsNone(read_capacity(directory, {"id": "two"}, "claude-opus-5[1m]"))

    def test_installer_preserves_commands_options_and_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            original = {"model": "custom", "statusLine": {"type": "command", "command": "printf old", "padding": 2},
                        "subagentStatusLine": {"type": "command", "command": "printf sub"}}
            atomic_json(path / "settings.json", original)
            configure(path, Path("/Applications/Smith Agents.app/Contents/MacOS/Smith Agents"))
            current = json.loads((path / "settings.json").read_text())
            self.assertEqual(current["statusLine"]["padding"], 2)
            saved = json.loads((path / "widget-context/bridge.json").read_text())
            self.assertEqual(saved["commands"], {"main": "printf old", "subagents": "printf sub"})
            current["newSetting"] = True
            atomic_json(path / "settings.json", current)
            configure(path, Path("/unused"), remove=True)
            self.assertEqual(json.loads((path / "settings.json").read_text()), dict(original, newSetting=True))
            configure(path, Path("/new/app"))  # Can reinstall after removal.

    def test_original_status_line_receives_identical_input_and_stdout_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            argv = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"]
            command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
            atomic_json(Path(directory) / "bridge.json", {"commands": {"main": command}})
            raw = b'{ "session_id": "test", "model": {"id":"opus"}, "context_window": {"context_window_size":1000000} }\n'
            result = subprocess.run([sys.executable, "-m", "smith_agents", "--context-bridge", directory, "main"],
                                    input=raw, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, raw)
            self.assertEqual(read_capacity(directory, {"id": "test"}), 1000000)

    def test_subagent_default_rendering_and_invalid_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-m", "smith_agents", "--context-bridge", directory, "subagents"],
                                    input=b'{"session_id":"test","tasks":[]}', capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            for capacity in (0, -1, True, None, "1000000"):
                capture({"session_id": "test", "context_window": {"context_window_size": capacity}}, directory, "main")
                self.assertIsNone(read_capacity(directory, {"id": "test"}))

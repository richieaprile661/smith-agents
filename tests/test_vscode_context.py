import json
import os
from pathlib import Path
import select
import queue
import threading
import signal
import subprocess
import sys
import tempfile
import unittest
import zipapp
from unittest.mock import patch

from smith_agents.context_bridge import read_capacity
from smith_agents.vscode_context import CapacityStream, input_chunks, input_lines
from tools.configure_vscode_context import configure, KEY


def reply(session="one", model="claude-opus-5", capacity=1000000):
    return {"type": "result", "session_id": session, "modelUsage": {
        model: {"contextWindow": capacity}, "claude-haiku-test": {"contextWindow": 200000}}}


class VscodeContextTests(unittest.TestCase):
    def test_permission_observer_failure_does_not_break_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            def unavailable(_data):
                raise OSError("socket unavailable")
            observer = CapacityStream(directory, on_record=unavailable)
            events = [{"type": "assistant", "session_id": "one", "message": {"model": "claude-opus-5"}}, reply()]
            observer.feed(b"\n".join(json.dumps(e).encode() for e in events) + b"\n")
            self.assertEqual(read_capacity(directory, {"id": "one"}), 1000000)

    def test_exact_session_and_main_model_with_interleaved_subagent(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = CapacityStream(directory)
            events = [
                {"type": "system", "subtype": "init", "session_id": "one", "model": "claude-opus-5[1m]"},
                {"type": "assistant", "session_id": "one", "parent_tool_use_id": "tool-child",
                 "message": {"model": "claude-haiku-test"}},
                dict(reply(capacity=200000), parent_tool_use_id="tool-child"),
                reply(),
            ]
            raw = b"garbage\n" + b"\n".join(json.dumps(e).encode() for e in events) + b"\n"
            for index in range(0, len(raw), 7):
                observer.feed(raw[index:index + 7])
            self.assertEqual(read_capacity(directory, {"id": "one"}, "claude-opus-5"), 1000000)
            self.assertIsNone(read_capacity(directory, {"id": "other"}, "claude-opus-5"))
            self.assertIsNone(read_capacity(directory, {"id": "one"}, "claude-haiku-test"))
            observer.record({"type": "assistant", "session_id": "two", "message": {"model": "claude-opus-5"}})
            observer.record(reply(session="two", capacity=200000))
            self.assertEqual(read_capacity(directory, {"id": "two"}), 200000)

    def test_missing_ambiguous_and_invalid_capacity_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = CapacityStream(directory)
            observer.record(reply())  # No known main model.
            self.assertIsNone(read_capacity(directory, {"id": "one"}))
            observer.record({"type": "assistant", "session_id": "one", "message": {"model": "claude-opus-5"}})
            for capacity in (None, -1, 0, True, "1000000"):
                observer.record(reply(capacity=capacity))
                self.assertIsNone(read_capacity(directory, {"id": "one"}))
            data = reply()
            data["modelUsage"]["claude-opus-5[1m]"] = {"contextWindow": 200000}
            observer.record(data)
            self.assertIsNone(read_capacity(directory, {"id": "one"}))

    def test_oversized_line_recovers_and_only_metadata_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = CapacityStream(directory)
            observer.MAX_LINE = 512
            observer.feed(b"x" * 1000)
            self.assertLessEqual(len(observer.buffer), 512)
            observer.feed(b'\n' + json.dumps({"type": "assistant", "session_id": "one",
                          "message": {"model": "claude-opus-5", "content": "private message"}}).encode() + b'\n')
            observer.feed(json.dumps(reply()).encode() + b'\n')
            saved = json.loads(next(Path(directory).glob('*.json')).read_text())
            self.assertEqual(set(saved), {"session_id", "task_id", "model", "capacity", "updated_at"})
            self.assertEqual(saved["capacity"], 1000000)

    def test_launcher_preserves_stream_stdin_stderr_exit_code_and_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "child.py"
            script.write_text("import sys, json, os\n"
                              "print('ready', flush=True)\n"
                              "line = sys.stdin.buffer.readline()\n"
                              "sys.stdout.buffer.write(line); sys.stdout.buffer.flush()\n"
                              "print(json.dumps({'type':'assistant','session_id':'one','message':{'model':'claude-opus-5'}}))\n"
                              "print(json.dumps({'type':'result','session_id':'one','modelUsage':{'claude-opus-5':{'contextWindow':1000000}}}), flush=True)\n"
                              "print(os.getcwd(), file=sys.stderr)\n"
                              "sys.exit(7)\n")
            env = dict(os.environ, CLAUDE_CONFIG_DIR=directory)
            proc = subprocess.Popen([sys.executable, "-m", "smith_agents", "--vscode-context-bridge",
                                     sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env)
            try:
                ready = queue.Queue()
                threading.Thread(target=lambda: ready.put(proc.stdout.readline()), daemon=True).start()
                self.assertEqual(ready.get(timeout=5).rstrip(b"\r\n"), b"ready")
                out, err = proc.communicate(b'unchanged input \xe2\x9c\x93\n', timeout=10)
                self.assertTrue(out.startswith(b'unchanged input \xe2\x9c\x93\n'))
                self.assertEqual(err.decode().strip(), os.getcwd())
                self.assertEqual(proc.returncode, 7)
                self.assertEqual(read_capacity(Path(directory) / "widget-context", {"id": "one"}), 1000000)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    stream.close()

    def test_input_framing_preserves_split_utf8_oversized_lines_and_eof(self):
        import io
        raw = 'first ✓\nsecond\r\n'.encode() + b'x' * 100 + b'\npartial'
        for limit in (7, 256):
            source = io.BytesIO(raw)
            expected = list(iter(lambda: source.readline(limit), b""))
            for chunk_size in (1, 3, 65536):
                chunks = (raw[i:i + chunk_size] for i in range(0, len(raw), chunk_size))
                self.assertEqual(list(input_lines(chunks, limit)), expected)

    def test_input_reader_stops_without_client_eof(self):
        read_fd, write_fd = os.pipe()
        stopped = threading.Event()
        received, errors = [], []
        reading = threading.Event()
        def read():
            try:
                for chunk in input_chunks(read_fd, stopped):
                    received.append(chunk)
                    reading.set()
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=read, daemon=True)
        try:
            worker.start()
            os.write(write_fd, b"ready")
            self.assertTrue(reading.wait(timeout=2), "Input reader did not receive data")
            stopped.set()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), "Input reader still waits for client EOF")
            self.assertEqual(b"".join(received), b"ready")
            self.assertEqual(errors, [])
        finally:
            stopped.set()
            os.close(write_fd)
            worker.join(timeout=2)
            os.close(read_fd)

    def test_child_exit_while_client_stdin_stays_open_does_not_abort(self):
        # communicate() closes stdin and hides the shutdown race. VS Code
        # keeps its pipe open until the wrapper exits, so wait before closing.
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "child.py"
            script.write_text("import sys, time\n"
                              "assert sys.stdin.buffer.readline() == b'finish\\n'\n"
                              "print('finished', flush=True)\n"
                              "time.sleep(.1)\n"
                              "sys.exit(7)\n")
            proc = subprocess.Popen([sys.executable, "-m", "smith_agents", "--vscode-context-bridge",
                                     sys.executable, str(script)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=dict(os.environ, CLAUDE_CONFIG_DIR=directory))
            try:
                proc.stdin.write(b"finish\n")
                proc.stdin.flush()
                code = proc.wait(timeout=10)
                out, err = proc.stdout.read(), proc.stderr.read()
                self.assertEqual(code, 7, err.decode(errors="replace"))
                self.assertEqual(out.rstrip(b"\r\n"), b"finished")
                self.assertEqual(err, b"")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    stream.close()

    def test_legacy_launcher_import_and_exit_with_client_stdin_open(self):
        # Installed distlib launchers retain this import after the package rename.
        # Run outside the checkout, with only the launcher's saved source path.
        with tempfile.TemporaryDirectory(prefix="legacy bridge ") as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            script = ("#!python\nimport sys\n"
                      f"sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})\n"
                      "from claude_widget.vscode_context import main\n"
                      "sys.exit(main(sys.argv[1:]))\n")
            if sys.platform == "win32":
                from distlib.scripts import ScriptMaker
                (source / "vscode-launcher.py").write_text(script, encoding="utf-8")
                maker = ScriptMaker(str(source), str(root))
                maker.executable = sys.executable
                maker.make("vscode-launcher.py")
                command = [str(root / "vscode-launcher.exe")]
            else:
                (source / "__main__.py").write_text(script, encoding="utf-8")
                archive = root / "vscode-launcher.pyz"
                zipapp.create_archive(source, archive)
                command = [sys.executable, str(archive)]
            child = root / "child.py"
            child.write_text("import sys\n"
                             "assert sys.stdin.buffer.readline() == b'finish\\n'\n"
                             "print('finished', flush=True)\n"
                             "sys.exit(7)\n")
            env = dict(os.environ, CLAUDE_CONFIG_DIR=directory)
            env.pop("PYTHONPATH", None)
            proc = subprocess.Popen([*command, sys.executable, str(child)], cwd=root,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env)
            try:
                proc.stdin.write(b"finish\n")
                proc.stdin.flush()
                code = proc.wait(timeout=10)
                out, err = proc.stdout.read(), proc.stderr.read()
                self.assertEqual(code, 7, err.decode(errors="replace"))
                self.assertEqual(out.rstrip(b"\r\n"), b"finished")
                self.assertEqual(err, b"")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    stream.close()

    @unittest.skipUnless(os.name == "posix", "Mac launcher uses POSIX signals")
    def test_sigterm_reaches_child(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "child.py"
            script.write_text("import signal, sys, time\n"
                              "signal.signal(signal.SIGTERM, lambda *_: sys.exit(23))\n"
                              "print('ready', flush=True)\n"
                              "while True: time.sleep(1)\n")
            proc = subprocess.Popen([sys.executable, "-m", "smith_agents", "--vscode-context-bridge",
                                     sys.executable, str(script)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=dict(os.environ, CLAUDE_CONFIG_DIR=directory))
            try:
                self.assertTrue(select.select([proc.stdout], [], [], 5)[0])
                self.assertEqual(proc.stdout.readline(), b"ready\n")
                proc.send_signal(signal.SIGTERM)
                code = proc.wait(timeout=10)
                err = proc.stderr.read()
                self.assertEqual(code, 23, err.decode(errors="replace"))
                self.assertEqual(err, b"")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    stream.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows native launcher")
    def test_windows_installer_roundtrip_and_native_protocol(self):
        with tempfile.TemporaryDirectory(prefix="bridge with spaces ") as directory:
            root = Path(directory)
            settings = root / "settings.json"
            settings.write_text('{"editor.fontSize": 14}')
            bridge = root / "context"
            configure(settings, bridge, Path(sys.executable))
            launcher = json.loads(settings.read_text())[KEY]
            child = root / "child.py"
            child.write_text("import sys, json, os\n"
                             "raw=sys.stdin.buffer.read()\n"
                             "sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()\n"
                             "print(json.dumps({'type':'assistant','session_id':'one','message':{'model':'claude-opus-5'}}))\n"
                             "print(json.dumps({'type':'result','session_id':'one','modelUsage':{'claude-opus-5':{'contextWindow':1000000}}}),flush=True)\n"
                             "print(json.dumps([os.getcwd(),sys.argv[1:]]),file=sys.stderr)\n"
                             "sys.exit(7)\n")
            arguments = ['space here', 'a&b|c', '"quoted"', '%PATH%', 'trailing\\', '\u05e9\u05dc\u05d5\u05dd']
            raw = 'unchanged input \u2713\n'.encode()
            proc = subprocess.run([launcher, sys.executable, str(child), *arguments],
                                  input=raw, capture_output=True, cwd=root, timeout=15,
                                  env=dict(os.environ, CLAUDE_CONFIG_DIR=directory))
            self.assertEqual(proc.returncode, 7, proc.stderr)
            self.assertTrue(proc.stdout.startswith(raw), proc.stdout)
            self.assertEqual(json.loads(proc.stderr), [str(root), arguments])
            self.assertEqual(read_capacity(root / "widget-context", {"id": "one"}), 1000000)
            changed = json.loads(settings.read_text())
            changed['newSetting'] = True
            settings.write_text(json.dumps(changed))
            configure(settings, bridge, Path(sys.executable))
            configure(settings, bridge, Path(sys.executable), remove=True)
            self.assertEqual(json.loads(settings.read_text()), {'editor.fontSize': 14, 'newSetting': True})

    @unittest.skipUnless(sys.platform == "win32", "Windows native launcher")
    def test_windows_preserves_existing_wrapper_and_rejects_external_change(self):
        from distlib.scripts import ScriptMaker
        with tempfile.TemporaryDirectory(prefix="existing wrapper ") as directory:
            root = Path(directory)
            source = root / "original.py"
            source.write_text("#!python\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\nsys.exit(37)\n")
            maker = ScriptMaker(str(root), str(root))
            maker.executable = sys.executable
            maker.force = True
            maker.make(source.name)
            original = str(root / "original.exe")
            settings = root / "settings.json"
            settings.write_text(json.dumps({KEY: original, "editor.fontSize": 14}))
            bridge = root / "bridge"
            configure(settings, bridge, Path(sys.executable))
            installed = json.loads(settings.read_text())[KEY]
            result = subprocess.run([installed, 'claude.exe', '--argument', 'with spaces'], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 37, result.stderr)
            self.assertEqual(json.loads(result.stdout), ['claude.exe', '--argument', 'with spaces'])
            settings.write_text(json.dumps({KEY: 'user-changed.exe'}))
            with self.assertRaisesRegex(RuntimeError, 'changed since installation'):
                configure(settings, bridge, Path(sys.executable), remove=True)
            self.assertEqual(json.loads(settings.read_text())[KEY], 'user-changed.exe')
            settings.write_text(json.dumps({KEY: installed, 'newSetting': True}))
            configure(settings, bridge, Path(sys.executable), remove=True)
            self.assertEqual(json.loads(settings.read_text()), {KEY: original, 'newSetting': True})

    @patch("tools.configure_vscode_context.sys.platform", "darwin")
    def test_installer_preserves_existing_wrapper_and_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            original = {"editor.fontSize": 14, KEY: "/existing wrapper"}
            settings.write_text(json.dumps(original))
            bridge = root / "context"
            configure(settings, bridge, Path("/App With Spaces/Smith Agents"))
            self.assertIn("'/existing wrapper'", (bridge / "vscode-launcher.sh").read_text())
            changed = json.loads(settings.read_text())
            changed["newSetting"] = True
            settings.write_text(json.dumps(changed))
            configure(settings, bridge, Path("/updated app"))
            configure(settings, bridge, Path("/unused"), remove=True)
            self.assertEqual(json.loads(settings.read_text()), dict(original, newSetting=True))


if __name__ == "__main__":
    unittest.main()

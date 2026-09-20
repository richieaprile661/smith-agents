"""Launches must retain their code location and hold the lock until exit."""
import ast
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

from test_core import core
from smith_agents import app


def windows_functions(*names, **scope):
    source = Path(core.SCRIPT_DIR) / "platform_win32.py"
    nodes = [node for node in ast.parse(source.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
    return scope


class LaunchLifecycleTests(unittest.TestCase):
    def test_windows_relaunch_imports_current_checkout_despite_installed_shim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "current checkout"
            current.mkdir()
            package = current / "smith_agents"
            package.mkdir()
            (package / "__init__.py").write_text("")
            (package / "__main__.py").write_text("print('current checkout')")
            old_shim = Mock(return_value=str(root / "old" / "smith-agents.exe"))
            scope = windows_functions("_launch_argv", os=os, sys=sys,
                                      SCRIPT_DIR=str(package), _entry_exe=old_shim,
                                      _sibling_exe=lambda _: sys.executable)
            argv, cwd = scope['_launch_argv']()
            self.assertEqual(subprocess.check_output(argv, cwd=cwd, text=True).strip(), "current checkout")
            old_shim.assert_not_called()

    def test_relaunch_keeps_lock_until_the_old_window_exits(self):
        widget = types.SimpleNamespace(demo=False, quit=Mock())
        with patch.object(app.platform, "launch_argv", return_value=(["python", "-m", "smith_agents"], "/tmp")), \
                patch.object(app.platform, "relaunch") as launch, \
                patch.object(app.platform, "release_single_instance") as release:
            app.SmithAgentsWidget.relaunch(widget)
            launch.assert_called_once()
            widget.quit.assert_called_once()
            release.assert_not_called()

    def test_failed_relaunch_keeps_current_window_and_lock(self):
        widget = types.SimpleNamespace(demo=False, quit=Mock())
        with patch.object(app.platform, "launch_argv", return_value=(["python"], "/tmp")), \
                patch.object(app.platform, "relaunch", side_effect=OSError), \
                patch.object(app.platform, "release_single_instance") as release:
            app.SmithAgentsWidget.relaunch(widget)
            widget.quit.assert_not_called()
            release.assert_not_called()

    def test_a_terminated_widget_still_releases_its_lock_and_logs_its_exit(self):
        """Default SIGTERM would kill the process mid-loop, leaving the
        single-instance claim behind and no exit line to tell a killed widget
        from a crashed one."""
        import signal
        widget = Mock()

        def terminate():
            # Only deliver the signal once a handler is installed: with the
            # default disposition this call would take the test runner down
            # with it instead of reporting a failure.
            self.assertNotIn(signal.getsignal(signal.SIGTERM),
                             (signal.SIG_DFL, signal.SIG_IGN))
            os.kill(os.getpid(), signal.SIGTERM)

        widget.run.side_effect = terminate
        before = signal.getsignal(signal.SIGTERM)
        lines = []
        with patch.object(app, "SmithAgentsWidget", return_value=widget), \
             patch.object(app.platform, "claim_single_instance", return_value=True), \
             patch.object(app.platform, "release_single_instance") as release, \
             patch.object(app.core, "log_line", lines.append), \
             patch.object(app.faulthandler, "enable"), \
             patch.object(app.faulthandler, "disable"):
            self.assertEqual(app.main([]), 0)
        widget.quit.assert_called_once_with()
        release.assert_called_once_with()
        self.assertTrue(any(line.startswith("exit pid=") for line in lines), lines)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_mutex_lifecycle_and_duplicate_rejection(self):
        handle = 2 ** 40 + 123
        kernel = types.SimpleNamespace(CreateMutexW=Mock(return_value=handle), CloseHandle=Mock())
        ffi = types.SimpleNamespace(set_last_error=Mock(), get_last_error=Mock(return_value=0),
                                    WinError=lambda error: OSError(error, 'mutex failure'))
        scope = windows_functions('claim_single_instance', 'release_single_instance',
                                  _INSTANCE_LOCK=None, _kernel32=kernel, ctypes=ffi, time=time,
                                  MUTEX_NAME='test', ERROR_ALREADY_EXISTS=183)
        self.assertTrue(scope['claim_single_instance']())
        self.assertTrue(scope['claim_single_instance']())
        kernel.CreateMutexW.assert_called_once()
        scope['release_single_instance']()
        kernel.CloseHandle.assert_called_once_with(handle)
        ffi.get_last_error.return_value = 183
        self.assertFalse(scope['claim_single_instance']())
        self.assertIsNone(scope['_INSTANCE_LOCK'])
        kernel.CreateMutexW.return_value = None
        ffi.get_last_error.return_value = 5
        with self.assertRaises(OSError):
            scope['claim_single_instance']()

    @unittest.skipUnless(sys.platform == 'win32', 'native Windows mutex')
    def test_windows_second_process_exits_and_lock_recovers(self):
        from smith_agents import platform_win32 as win
        name = 'Local\\SmithAgentsTest-%d' % os.getpid()
        command = [sys.executable, '-c',
                   'from smith_agents import platform_win32 as w; '
                   'w.MUTEX_NAME = %r; print(w.claim_single_instance())' % name]
        with patch.object(win, 'MUTEX_NAME', name):
            self.assertTrue(win.claim_single_instance())
            try:
                self.assertEqual(subprocess.check_output(command, text=True).strip(), 'False')
            finally:
                win.release_single_instance()
            self.assertEqual(subprocess.check_output(command, text=True).strip(), 'True')


if __name__ == '__main__':
    unittest.main()

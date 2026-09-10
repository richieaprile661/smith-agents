"""Ending one agent must never terminate a shared host or another session."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_session_windows import win
from smith_agents import app, core


def sessions():
    return [dict(id=str(i), pid=100+i, started_at=10+i, state='working',
                 cwd='/projects/shared', entrypoint='claude-vscode') for i in range(4)]


class SessionTerminationTests(unittest.TestCase):
    def test_four_chats_in_one_project_only_stop_the_selected_process(self):
        rows = sessions()
        with patch.object(core, 'list_agents', return_value=rows), \
             patch.object(core.platform, 'terminate_agent', return_value=True) as terminate:
            self.assertTrue(core.terminate_agent(dict(rows[2])))
            terminate.assert_called_once_with(rows[2])

    def test_four_sessions_sharing_a_process_cannot_be_ended_as_one(self):
        rows = [dict(row, pid=100, started_at=10) for row in sessions()]
        with patch.object(core, 'list_agents', return_value=rows), \
             patch.object(core.platform, 'terminate_agent') as terminate:
            with self.assertRaisesRegex(PermissionError, 'shares its process'):
                core.terminate_agent(dict(rows[2]))
            terminate.assert_not_called()

    def test_parent_with_subagents_is_not_killed_to_stop_one_agent(self):
        parent = sessions()[0]
        children = [dict(id='agent-'+str(i), pid=parent['pid'], sub=True,
                         parent=parent['id'], state='working') for i in range(3)]
        with patch.object(core, 'list_agents', return_value=[parent, *children]), \
             patch.object(core.platform, 'terminate_agent') as terminate:
            with self.assertRaises(PermissionError):
                core.terminate_agent(parent)
            self.assertFalse(core.terminate_agent(children[0]))
            terminate.assert_not_called()

    def test_target_is_revalidated_after_confirmation(self):
        selected = sessions()[0]
        for changes in ({'id': 'replacement'}, {'pid': 999}, {'started_at': 99}, {'state': 'closed'}):
            with patch.object(core, 'list_agents', return_value=[dict(selected, **changes)]), \
                 patch.object(core.platform, 'terminate_agent') as terminate:
                self.assertFalse(core.terminate_agent(selected))
                terminate.assert_not_called()

    def test_confirmation_keeps_other_panels_and_reports_failures(self):
        for outcome in (True, False, PermissionError('Shared process; nothing stopped.')):
            widget = app.SmithAgentsWidget.__new__(app.SmithAgentsWidget)
            widget.agents = sessions()
            selected = widget.agents[2]
            widget.agent_rows = [('yes', 0, 0, 50, 50, selected)]
            widget._agents_scan = 123
            widget._confirm_kill = selected['id']
            with patch.object(app, 'terminate_agent', side_effect=outcome if isinstance(outcome, Exception) else None,
                              return_value=outcome) as terminate, \
                 patch.object(app.platform, 'show_error') as error, patch.object(widget, '_repaint'):
                widget._on_console_click(SimpleNamespace(x=25, y=25))
                terminate.assert_called_once_with(selected)
                self.assertEqual(len(widget.agents), 4)
                self.assertEqual(widget._agents_scan, 0)
                self.assertIsNone(widget._confirm_kill)
                self.assertEqual(error.call_count, 0 if outcome is True else 1)

    def test_windows_checks_identity_on_the_same_handle_it_terminates(self):
        for actual_start, image, readable, allowed in (
                (10, r'C:\Claude\claude.exe', True, True),
                (11, r'C:\Claude\claude.exe', True, False),
                (None, r'C:\Claude\claude.exe', True, False),
                (10, r'C:\VS Code\Code.exe', True, False),
                (10, r'C:\Windows\WindowsTerminal.exe', True, False),
                (10, r'C:\Claude\claude.exe', False, False)):
            kernel = Mock()
            kernel.OpenProcess.return_value = 0x123456789
            kernel.TerminateProcess.return_value = 1
            def read_image(handle, flags, buffer, length):
                buffer.value = image
                return readable
            kernel.QueryFullProcessImageNameW.side_effect = read_image
            with patch.object(win, '_process_api', return_value=kernel), \
                 patch.object(win, '_handle_started', return_value=actual_start) as started:
                self.assertEqual(win.terminate_agent(sessions()[0]), allowed)
                kernel.OpenProcess.assert_called_once_with(0x1001, False, 100)
                started.assert_called_once_with(kernel, 0x123456789)
                if allowed:
                    kernel.TerminateProcess.assert_called_once_with(0x123456789, 0)
                else:
                    kernel.TerminateProcess.assert_not_called()
                kernel.CloseHandle.assert_called_once_with(0x123456789)

    def test_windows_requires_identity_before_opening_termination_handle(self):
        with patch.object(win, '_process_api') as api:
            for row in ({'pid': 100}, {'pid': 100, 'started_at': 10, 'sub': True}, {'pid': 0, 'started_at': 10}):
                self.assertFalse(win.terminate_agent(row))
            api.assert_not_called()

    def test_windows_can_capture_identity_for_session_files_without_procstart(self):
        actual = 116444736000000000 + 10000000 * 1000
        with patch.object(win, '_proc_started', return_value=actual):
            self.assertEqual(win.session_process_start({'startedAt': 1000000}, 100), actual)
            self.assertEqual(win.session_process_start({'startedAt': 900000}, 100), -1)
            self.assertEqual(win.session_process_start({'procStart': 123}, 100), 123)

    @unittest.skipUnless(sys.platform == 'darwin', 'Native macOS process isolation')
    def test_four_disposable_processes_only_selected_one_exits(self):
        import psutil
        from smith_agents import platform_darwin as mac
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory)/'claude-isolation-test.py'
            script.write_text('import time; time.sleep(60)')
            children = [subprocess.Popen([sys.executable, str(script)], cwd=directory,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL) for _ in range(4)]
            try:
                rows = [dict(row, pid=child.pid, started_at=psutil.Process(child.pid).create_time())
                        for row, child in zip(sessions(), children)]
                with patch.object(core, 'list_agents', return_value=rows), \
                     patch.object(core.platform, 'terminate_agent', side_effect=mac.terminate_agent):
                    self.assertTrue(core.terminate_agent(dict(rows[2])))
                children[2].wait(timeout=5)
                self.assertTrue(all(child.poll() is None for i, child in enumerate(children) if i != 2))
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()

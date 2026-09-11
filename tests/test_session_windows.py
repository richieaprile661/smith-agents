"""Opening/hiding changes window visibility without ending a Claude session."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from smith_agents import app, core
if sys.platform == 'win32':
    from smith_agents import platform_win32 as win
else:
    import ctypes
    fake_dll = Mock()
    fake_dll.user32.GetDpiForSystem.return_value = 96
    with patch.object(ctypes, 'windll', fake_dll, create=True):
        from smith_agents import platform_win32 as win
from smith_agents.session_windows import WindowTargets, window_agent


class SessionWindowTests(unittest.TestCase):
    def test_subagents_share_verified_parent_state_and_actions(self):
        main = dict(core.demo_agents()[1], id='main', provider='claude')
        sub = dict(main, id='child', sub=True, parent='main')
        nested = dict(sub, id='nested', parent='child')
        orphan = dict(sub, id='orphan', parent='missing')
        other_provider = dict(main, provider='codex', _window_state='hidden')
        widget = app.SmithAgentsWidget.__new__(app.SmithAgentsWidget)
        widget.agents = [nested, orphan, sub, other_provider, main]
        widget.agent_open = None
        widget.root = Mock()
        with patch.object(app.platform, 'agent_window_state', side_effect=['hidden', 'front']) as state, \
             patch.object(app.platform, 'agent_window_is_visible', side_effect=[False, True]), \
             patch.object(app, 'hide_agent_window', return_value=True) as hide, \
             patch.object(widget, '_repaint'):
            widget._sync_agent_windows()
            self.assertEqual(state.call_args_list[0].args, (other_provider,))
            self.assertEqual(state.call_args_list[1].args, (main,))
            for row in (nested, sub):
                self.assertEqual(row['_window_state'], 'front')
                self.assertTrue(row['_window_open'])
                self.assertEqual(core.agent_window_action(row), ('hide', 'Hide parent window'))
            self.assertEqual(orphan['_window_state'], 'unknown')
            self.assertFalse(orphan['_window_open'])
            _, widget.agent_rows = core.render_console([], None, {}, [main, sub], 0)
            box = next(box for box in widget.agent_rows if box[0] == 'hide' and box[-1]['id'] == 'child')
            with patch.object(widget, '_sync_agent_windows'):
                widget._on_console_click(SimpleNamespace(x=(box[1]+box[3])/2, y=(box[2]+box[4])/2))
            hide.assert_called_once_with(main)
        sub['parent'] = 'nested'
        self.assertIsNone(window_agent(sub, widget.agents))

    def test_windows_focus_is_exact_and_observation_does_not_raise_or_hide(self):
        hwnd = 2**33 + 789  # HWND must not be truncated to a 32-bit integer.
        user32 = Mock()
        user32.IsWindowVisible.return_value = True
        user32.IsIconic.return_value = False
        with patch.object(win, '_find_agent_window', return_value=hwnd) as find, \
             patch.object(win.ctypes, 'windll', SimpleNamespace(user32=user32), create=True):
            for foreground, expected in ((hwnd, 'front'), (hwnd+1, 'background'), (0, 'unknown')):
                user32.GetForegroundWindow.return_value = foreground
                self.assertEqual(win.agent_window_state({}), expected)
            user32.IsIconic.return_value = True
            self.assertEqual(win.agent_window_state({}), 'hidden')
            user32.IsIconic.return_value = False
            user32.IsWindowVisible.return_value = False
            self.assertEqual(win.agent_window_state({}), 'hidden')
            find.return_value = None
            self.assertEqual(win.agent_window_state({}), 'unknown')
            user32.ShowWindow.assert_not_called()
            user32.SetForegroundWindow.assert_not_called()

    def test_windows_discovers_only_a_unique_ancestor_window_without_focusing(self):
        user32 = Mock()
        user32.IsWindowVisible.return_value = True
        user32.GetWindowTextLengthW.return_value = 20
        user32.GetWindowTextW.side_effect = lambda hwnd, buffer, length: setattr(buffer, 'value', 'widget — VS Code')
        handles = [789, 790]
        user32.EnumWindows.side_effect = lambda visit, param: [visit(hwnd, param) for hwnd in handles]
        process = Mock(pid=123)
        process.parents.return_value = [Mock(pid=456)]
        owners = {789: 456, 790: 999}
        row = {'id': 'one', 'pid': 123, 'started_at': 10, 'cwd': '/projects/widget'}
        with patch.object(win, '_AGENT_WINDOWS', WindowTargets()) as targets, \
             patch.object(win, '_pid_alive', return_value=True), \
             patch.object(win, '_window_owner', side_effect=owners.get), \
             patch.object(win, '_proc_started', return_value=20), \
             patch('psutil.Process', return_value=process), \
             patch.object(win.ctypes, 'WINFUNCTYPE', return_value=lambda callback: callback, create=True), \
             patch.object(win.ctypes, 'windll', SimpleNamespace(user32=user32), create=True):
            self.assertEqual(win._find_agent_window(row), 789)
            self.assertEqual(targets.get(row), (789, 456, 20))
            targets.discard(row)
            owners[790] = 456
            self.assertIsNone(win._find_agent_window(row))
            self.assertIsNone(targets.get(row))
            user32.ShowWindow.assert_not_called()
            user32.SetForegroundWindow.assert_not_called()

    def test_role_and_window_labels_fit_before_context_without_hiding_activity(self):
        from PIL import ImageDraw
        for scale in (1, 2):
            with patch.object(core, 'SCALE', scale):
                for sub in (False, True):
                    for state in ('front', 'background', 'hidden', 'unknown'):
                        row = dict(core.demo_agents()[1], sub=sub, _window_state=state)
                        chip = core.console_base((core.CONSOLE_W, core.agent_row_height(row)))
                        draw = ImageDraw.Draw(chip)
                        pen = Mock(wraps=draw)
                        core.render_row(pen, chip, row, 0, 0, False, False)
                        label = core.agent_window_label(row)
                        calls = [call for call in pen.text.call_args_list if call.args[1] == label]
                        self.assertEqual(len(calls), 1, label)
                        call = calls[0]
                        bounds = draw.textbbox(call.args[0], label, font=call.kwargs['font'])
                        self.assertLessEqual(bounds[2], core.CONSOLE_W - core.PAD_X)
                        self.assertLess(bounds[3], core.agent_row_layout(row)[2])
                        self.assertIn('Parent window' if sub else 'Main agent', label)
                        self.assertEqual(core.agent_status(row), 'Working')

    def test_targets_are_bounded_and_do_not_follow_reused_session_pids(self):
        targets = WindowTargets(limit=2)
        row = {'id': 'a', 'pid': 12, 'started_at': 100}
        targets.remember(row, 'original')
        self.assertEqual(targets.get(dict(row)), 'original')
        self.assertIsNone(targets.get(dict(row, started_at=101)))
        for i in range(3):
            targets.remember({'id': str(i), 'pid': i, 'started_at': 1}, i)
        self.assertEqual(len(targets.targets), 2)
        self.assertIsNone(targets.get(row))

    def test_row_and_drawer_toggle_open_hide_open_without_ending_session(self):
        for entrypoint, noun in (('claude-cli', 'terminal'), ('claude-vscode', 'chat')):
            with self.subTest(entrypoint=entrypoint):
                row = dict(core.demo_agents()[1], entrypoint=entrypoint)
                widget = app.SmithAgentsWidget.__new__(app.SmithAgentsWidget)
                widget.agents = [row]
                widget.agent_open = None
                widget.root = Mock()
                visible = False
                def opening(agent):
                    nonlocal visible
                    visible = True
                    return True
                def hiding(agent):
                    nonlocal visible
                    visible = False
                    return True
                with patch.object(app, 'raise_agent_window', side_effect=opening) as open_window, \
                     patch.object(app, 'hide_agent_window', side_effect=hiding) as hide_window, \
                     patch.object(app.platform, 'agent_window_is_visible', side_effect=lambda agent: visible), \
                     patch.object(app, 'terminate_agent') as terminate, \
                     patch.object(widget, '_repaint'):
                    for kind in ('open', 'hide', 'open'):
                        _, boxes = core.render_console([], None, {}, widget.agents, 0)
                        widget.agent_rows = boxes
                        box = next(box for box in boxes if box[0] == kind)
                        self.assertEqual(core.agent_window_action(widget.agents[0]), (kind, kind.title()+' '+noun))
                        self.assertTrue(widget._on_console_click(SimpleNamespace(
                            x=(box[1]+box[3])/2, y=(box[2]+box[4])/2)))
                        self.assertIsNone(widget.agent_open)
                        # Rescans replace row dicts; visibility must survive them.
                        widget.agents = [dict(row)]
                        widget.agents[0].pop('_window_open', None)
                        widget._sync_agent_windows()
                    self.assertEqual(open_window.call_count, 2)
                    hide_window.assert_called_once()
                    terminate.assert_not_called()
                    _, boxes = core.render_console([], None, {}, widget.agents, 0, open_id=row['id'])
                    self.assertEqual(sum(box[0]=='hide' for box in boxes), 2)
                    visible = False  # A user can minimize/hide outside the widget.
                    widget._sync_agent_windows()
                    self.assertEqual(core.agent_window_action(widget.agents[0])[0], 'open')

    def test_delayed_hide_is_checked_after_the_native_event_loop_updates(self):
        row = dict(core.demo_agents()[1], _window_open=True)
        widget = app.SmithAgentsWidget.__new__(app.SmithAgentsWidget)
        widget.agents = [row]
        widget.root = Mock()
        with patch.object(app.platform, 'agent_window_is_visible', return_value=True) as visible, \
             patch.object(app.platform, 'show_error') as error, \
             patch.object(app, 'terminate_agent') as terminate, patch.object(widget, '_repaint'):
            widget._finish_window_hide(row)
            error.assert_not_called()
            callback = widget.root.after.call_args.args[1]
            visible.return_value = False
            callback()
            self.assertFalse(row['_window_open'])
            error.assert_not_called()
            visible.return_value = True
            widget._finish_window_hide(row, remaining=0)
            error.assert_called_once()
            self.assertTrue(row['_window_open'])
            terminate.assert_not_called()

    def test_failed_hide_keeps_action_and_never_falls_back_to_termination(self):
        row = dict(core.demo_agents()[1], _window_open=True)
        widget = app.SmithAgentsWidget.__new__(app.SmithAgentsWidget)
        widget.agents = [row]
        widget.agent_open = None
        _, widget.agent_rows = core.render_console([], None, {}, [row], 0)
        box = next(box for box in widget.agent_rows if box[0]=='hide')
        with patch.object(app, 'hide_agent_window', return_value=False), \
             patch.object(app.platform, 'agent_window_is_visible', return_value=True), \
             patch.object(app.platform, 'show_error') as error, \
             patch.object(app, 'terminate_agent') as terminate, patch.object(widget, '_repaint'):
            widget._on_console_click(SimpleNamespace(x=(box[1]+box[3])/2, y=(box[2]+box[4])/2))
            error.assert_called_once()
            terminate.assert_not_called()
            self.assertEqual(core.agent_window_action(row)[0], 'hide')

    def test_windows_minimizes_only_recorded_window_and_rejects_reused_owner(self):
        row = {'id': 'one', 'pid': 123, 'started_at': 10}
        targets = WindowTargets()
        targets.remember(row, (789, 456, 20))
        user32 = Mock()
        user32.IsWindow.return_value = True
        user32.IsWindowVisible.return_value = True
        iconic = False
        user32.IsIconic.side_effect = lambda handle: iconic
        def minimize(handle, operation):
            nonlocal iconic
            self.assertEqual(handle.value, 789)
            self.assertEqual(operation, 6)
            iconic = True
        user32.ShowWindow.side_effect = minimize
        kernel32 = Mock()
        with patch.object(win, '_AGENT_WINDOWS', targets), \
             patch.object(win.ctypes, 'windll', SimpleNamespace(user32=user32, kernel32=kernel32), create=True), \
             patch.object(win, '_window_owner', return_value=456), \
             patch.object(win, '_proc_started', return_value=20) as started, \
             patch.object(win, '_pid_alive', return_value=True):
            self.assertTrue(win.agent_window_is_visible(row))
            self.assertTrue(win.hide_agent_window(row))
            self.assertFalse(win.agent_window_is_visible(row))
            self.assertTrue(win.hide_agent_window(row))
            user32.ShowWindow.assert_called_once()
            user32.PostMessageW.assert_not_called()
            user32.SendMessageW.assert_not_called()
            kernel32.TerminateProcess.assert_not_called()
            started.return_value = 21
            self.assertFalse(win.hide_agent_window(row))
            self.assertIsNone(targets.get(row))
            user32.EnumWindows.assert_not_called()

    def test_windows_reopen_uses_recorded_handle_without_searching_other_titles(self):
        row = {'id': 'one', 'pid': 123, 'started_at': 10}
        user32 = Mock()
        user32.IsIconic.return_value = True
        user32.SetForegroundWindow.return_value = 1
        with patch.object(win, '_opened_agent_window', return_value=789), \
             patch.object(win, '_pid_alive', return_value=True), \
             patch.object(win.ctypes, 'windll', SimpleNamespace(user32=user32), create=True):
            self.assertTrue(win.raise_agent_window(row))
            self.assertEqual(user32.ShowWindow.call_args.args[0].value, 789)
            self.assertEqual(user32.ShowWindow.call_args.args[1], 9)
            self.assertEqual(user32.SetForegroundWindow.call_args.args[0].value, 789)
            user32.EnumWindows.assert_not_called()

    def test_windows_ambiguous_project_never_chooses_first_window(self):
        user32 = Mock()
        user32.IsWindowVisible.return_value = True
        user32.GetWindowTextLengthW.return_value = 20
        def title(hwnd, buffer, length):
            buffer.value = 'widget — VS Code'
        user32.GetWindowTextW.side_effect = title
        user32.EnumWindows.side_effect = lambda visit, param: [visit(hwnd, param) for hwnd in (789, 790)]
        with patch.object(win, '_opened_agent_window', return_value=None), \
             patch.object(win, '_pid_alive', return_value=True), \
             patch.object(win.ctypes, 'WINFUNCTYPE', return_value=lambda callback: callback, create=True), \
             patch.object(win.ctypes, 'windll', SimpleNamespace(user32=user32), create=True):
            self.assertFalse(win.raise_agent_window({'cwd': '/projects/widget', 'pid': 123}))
            user32.ShowWindow.assert_not_called()
            user32.SetForegroundWindow.assert_not_called()

    def test_project_title_matches_whole_name(self):
        from smith_agents.session_windows import title_matches_project
        self.assertTrue(title_matches_project('file.py — Widget — VS Code', 'widget'))
        self.assertFalse(title_matches_project('mywidget — VS Code', 'widget'))
        self.assertFalse(title_matches_project('widget-tools — VS Code', 'widget'))


@unittest.skipUnless(sys.platform=='darwin', 'macOS window controls')
class MacSessionWindowTests(unittest.TestCase):
    def test_mac_front_state_requires_exact_window_in_active_application(self):
        from smith_agents import platform_darwin as mac
        host, window = Mock(), Mock()
        host.isHidden.return_value = False
        host.isActive.return_value = True
        window.get.return_value = False
        row = {'id': 'one', 'pid': 123, 'started_at': 10}
        with patch.object(mac, '_opened_agent_window', return_value=(host, window)), \
             patch.object(mac, 'pid_alive', return_value=True), \
             patch.object(mac.macos_windows, 'is_focused') as focused:
            for focus, expected in ((True, 'front'), (False, 'background'), (None, 'unknown')):
                focused.return_value = focus
                self.assertEqual(mac.agent_window_state(row), expected)
            focused.assert_called_with(host.processIdentifier(), window)
            host.isActive.return_value = False
            self.assertEqual(mac.agent_window_state(row), 'background')
            host.isHidden.return_value = True
            self.assertEqual(mac.agent_window_state(row), 'hidden')
            host.isHidden.return_value = False
            window.get.return_value = True
            self.assertEqual(mac.agent_window_state(row), 'hidden')
            window.raise_window.assert_not_called()
            window.set_bool.assert_not_called()

    def test_mac_missing_window_stays_unknown_without_permission_prompt(self):
        from smith_agents import platform_darwin as mac
        row = {'id': 'one', 'pid': 123, 'started_at': 10}
        with patch.object(mac, '_opened_agent_window', return_value=None), \
             patch.object(mac, 'pid_alive', return_value=True), \
             patch('psutil.Process'), \
             patch.object(mac, '_agent_host_application', return_value=Mock()), \
             patch.object(mac, '_match_agent_window', return_value=None), \
             patch.object(mac.macos_windows, 'request_access') as request:
            self.assertEqual(mac.agent_window_state(row), 'unknown')
            request.assert_not_called()

    def test_mac_controls_only_selected_window_and_reuses_it_after_title_changes(self):
        from smith_agents import platform_darwin as mac
        row = {'id': 'one', 'pid': 123, 'started_at': 10,
               'entrypoint': 'cli', 'cwd': '/projects/widget'}
        host = Mock()
        host.isTerminated.return_value = False
        host.isHidden.return_value = False
        first, other = Mock(), Mock()
        state = {'AXTitle': 'widget — Terminal', 'AXMinimized': False}
        first.get.side_effect = state.get
        other.get.side_effect = {'AXTitle': 'other — Terminal', 'AXMinimized': False}.get
        def change(attribute, value):
            state[attribute] = value
            return True
        first.set_bool.side_effect = change
        first.raise_window.side_effect = lambda: change('AXMinimized', False)
        with patch.object(mac, '_AGENT_WINDOWS', WindowTargets()), \
             patch.object(mac, '_agent_host_application', return_value=host), \
             patch.object(mac.macos_windows, 'windows', return_value=[first, other]) as windows, \
             patch.object(mac, 'pid_alive', return_value=True), patch('psutil.Process') as process:
            self.assertTrue(mac.raise_agent_window(row))
            self.assertTrue(mac.agent_window_is_visible(row))
            self.assertTrue(mac.hide_agent_window(row))
            self.assertFalse(mac.agent_window_is_visible(row))
            state['AXTitle'] = 'new title'
            self.assertTrue(mac.raise_agent_window(row))
            self.assertTrue(mac.agent_window_is_visible(row))
            windows.assert_called_once()
            other.raise_window.assert_not_called()
            other.set_bool.assert_not_called()
            host.hide.assert_not_called()
            host.activateWithOptions_.assert_not_called()
            host.terminate.assert_not_called()
            process.return_value.send_signal.assert_not_called()
            state.pop('AXMinimized')  # Closed windows cannot be retargeted on Hide.
            self.assertFalse(mac.hide_agent_window(row))

    def test_mac_ambiguous_project_never_activates_or_hides_host(self):
        from smith_agents import platform_darwin as mac
        host = Mock()
        host.isTerminated.return_value = False
        first, second = Mock(), Mock()
        first.get.return_value = second.get.return_value = 'widget — Terminal'
        row = {'id': 'one', 'pid': 123, 'entrypoint': 'cli', 'cwd': '/projects/widget'}
        with patch.object(mac, '_AGENT_WINDOWS', WindowTargets()), \
             patch.object(mac, '_agent_host_application', return_value=host), \
             patch.object(mac.macos_windows, 'windows', return_value=[first, second]), \
             patch.object(mac.macos_windows, 'trusted', return_value=True), \
             patch.object(mac, 'pid_alive', return_value=True), patch('psutil.Process'):
            self.assertFalse(mac.raise_agent_window(row))
            self.assertFalse(mac.hide_agent_window(row))
            first.raise_window.assert_not_called()
            second.raise_window.assert_not_called()
            host.activateWithOptions_.assert_not_called()
            host.hide.assert_not_called()

    def test_mac_missing_accessibility_reports_requirement_without_app_fallback(self):
        from smith_agents import platform_darwin as mac
        host = Mock()
        host.isTerminated.return_value = False
        row = {'id': 'one', 'pid': 123, 'entrypoint': 'cli'}
        with patch.object(mac, '_AGENT_WINDOWS', WindowTargets()), \
             patch.object(mac, '_agent_host_application', return_value=host), \
             patch.object(mac.macos_windows, 'windows', return_value=[]), \
             patch.object(mac.macos_windows, 'trusted', return_value=False), \
             patch.object(mac.macos_windows, 'request_access') as request_access, \
             patch.object(mac, 'pid_alive', return_value=True), patch('psutil.Process'):
            with self.assertRaisesRegex(PermissionError, 'Accessibility'):
                mac.raise_agent_window(row)
            request_access.assert_called_once()
            host.activateWithOptions_.assert_not_called()

    def test_mac_ignores_helper_apps_and_selects_the_regular_host(self):
        from smith_agents import platform_darwin as mac
        import AppKit
        process = Mock(pid=123)
        process.parents.return_value = [Mock(pid=456)]
        helper, host = Mock(), Mock()
        helper.activationPolicy.return_value = 2
        host.activationPolicy.return_value = 0
        with patch.object(AppKit, 'NSRunningApplication', Mock(
                runningApplicationWithProcessIdentifier_=Mock(side_effect=[helper, host]))):
            self.assertIs(mac._agent_host_application(process), host)


if __name__=='__main__':
    unittest.main()

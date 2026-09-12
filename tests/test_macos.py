import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

if sys.platform == "darwin":
    from smith_agents import platform_darwin as mac
else:
    mac = None


@unittest.skipUnless(sys.platform == "darwin", "macOS backend")
class AccessibilitySetupTests(unittest.TestCase):
    def offer(self, config, choice=1001, trusted=False, force=False):
        alert = Mock()
        alert.runModal.return_value = choice
        nsalert = Mock()
        nsalert.alloc.return_value.init.return_value = alert
        bundle = Mock()
        bundle.mainBundle.return_value.objectForInfoDictionaryKey_.return_value = "Python"
        save, open_settings = Mock(), Mock()
        modules = {"AppKit": SimpleNamespace(NSAlert=nsalert, NSAlertFirstButtonReturn=1000),
                   "Foundation": SimpleNamespace(NSBundle=bundle)}
        with patch.dict(sys.modules, modules), patch.object(mac.macos_windows, "trusted", return_value=trusted), \
             patch.object(mac, "open_accessibility_settings", open_settings):
            mac.offer_accessibility_setup(config, save, force)
        return alert, save, open_settings

    def test_skip_is_remembered_without_requesting_permission(self):
        config = {}
        alert, save, settings = self.offer(config)
        alert.runModal.assert_called_once()
        self.assertIn("Python", alert.setInformativeText_.call_args.args[0])
        settings.assert_not_called()
        save.assert_called_once()
        alert, _, settings = self.offer(config)
        alert.runModal.assert_not_called()
        settings.assert_not_called()

    def test_settings_open_only_after_explicit_choice_and_menu_can_retry(self):
        config = {"accessibility_setup_executable": str(Path(sys.executable).resolve())}
        alert, _, settings = self.offer(config, choice=1000, force=True)
        alert.runModal.assert_called_once()
        settings.assert_called_once()

    def test_already_granted_does_not_prompt(self):
        config = {}
        alert, save, settings = self.offer(config, trusted=True)
        alert.runModal.assert_not_called()
        settings.assert_not_called()
        save.assert_called_once()

    def test_new_executable_gets_setup_again(self):
        alert, _, _ = self.offer({"accessibility_setup_executable": "/old/Python"})
        alert.runModal.assert_called_once()


@unittest.skipUnless(sys.platform == "darwin", "macOS backend")
class CredentialsTests(unittest.TestCase):
    def test_default_keychain_account_is_used_without_a_file(self):
        payload = {"claudeAiOauth": {"accessToken": "unit-test-token"}}
        result = Mock(returncode=0, stdout=json.dumps(payload))
        path = str(Path.home() / ".claude/.credentials.json")
        with patch.object(mac.subprocess, "run", return_value=result) as run:
            self.assertEqual(mac.read_credentials(path), payload)
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/security")
        self.assertIn("Claude Code-credentials", run.call_args.args[0])

    def test_custom_config_does_not_borrow_default_keychain_account(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".credentials.json"
            path.write_text('{"claudeAiOauth":{"accessToken":"custom-test-token"}}')
            with patch.object(mac.subprocess, "run") as run:
                self.assertEqual(mac.read_credentials(path)["claudeAiOauth"]["accessToken"], "custom-test-token")
                run.assert_not_called()

    def test_keychain_denial_does_not_silently_use_another_credential(self):
        with patch.object(mac.subprocess, "run", return_value=Mock(returncode=36)), \
             patch("builtins.open") as open_file:
            with self.assertRaises(OSError):
                mac.read_credentials(str(Path.home() / ".claude/.credentials.json"))
            open_file.assert_not_called()


@unittest.skipUnless(sys.platform == "darwin", "macOS backend")
class ProcessTests(unittest.TestCase):
    def test_vscode_link_targets_session_and_subagent_parent(self):
        session = "6eff17ae-ef20-4d23-8cd7-e6fc909f4ccc"
        expected = "vscode://anthropic.claude-code/open?session=" + session + "&windowId=4"
        self.assertEqual(mac.vscode_session_url({"id": session}, 4), expected)
        self.assertEqual(mac.vscode_session_url({"id": "agent-child", "parent": session, "sub": True}, 4), expected)
        self.assertEqual(mac.vscode_session_url({"id": "agent-nested", "parent": "agent-child",
                                                "root_parent": session, "sub": True}, 4), expected)
        self.assertIsNone(mac.vscode_session_url({"id": "invalid&prompt=do-stuff"}, 4))
        self.assertIsNone(mac.vscode_session_url({"id": session}))

    def test_vscode_open_reveals_session_instead_of_only_activating_app(self):
        import AppKit
        workspace = Mock()
        workspace.openURL_.return_value = True
        agent = {"id": "6eff17ae-ef20-4d23-8cd7-e6fc909f4ccc", "pid": 123, "entrypoint": "claude-vscode"}
        with patch.object(mac, "pid_alive", return_value=True), \
             patch.object(mac, "_agent_host_application", return_value=None), \
             patch.object(AppKit, "NSWorkspace", Mock(sharedWorkspace=Mock(return_value=workspace))), \
             patch.object(mac, "vscode_window_id", return_value=4), \
             patch("psutil.Process") as process:
            process.return_value.parents.return_value = []
            self.assertTrue(mac.raise_agent_window(agent))
            self.assertIn(agent["id"], str(workspace.openURL_configuration_completionHandler_.call_args.args[0]))
            self.assertIn("windowId=4", str(workspace.openURL_configuration_completionHandler_.call_args.args[0]))
            configuration = workspace.openURL_configuration_completionHandler_.call_args.args[1]
            self.assertFalse(configuration.activates())
            workspace.openURL_.assert_not_called()

    def test_window_match_uses_extension_host_identity(self):
        from datetime import datetime
        started = datetime(2026, 9, 9, 10, 45, 33).timestamp()
        host = Mock(pid=36388)
        host.name.return_value = "Code Helper (Plugin)"
        host.create_time.return_value = started
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "run/window4/exthost/exthost.log"
            log.parent.mkdir(parents=True)
            log.write_text("2026-09-09 10:45:33.193 [info] Extension host with pid 36388 started\n")
            self.assertEqual(mac.vscode_window_id([host], directory), 4)
            host.create_time.return_value = started + 60
            self.assertIsNone(mac.vscode_window_id([host], directory))

    def test_unknown_window_never_opens_unscoped_session_link(self):
        import AppKit
        process = Mock()
        process.parents.return_value = []
        process.pid = 123
        with patch.object(mac, "pid_alive", return_value=True), \
             patch.object(mac, "vscode_window_id", return_value=None), \
             patch.object(mac, "_match_agent_window", return_value=None), \
             patch.object(AppKit, "NSWorkspace") as workspace, \
             patch.object(AppKit, "NSRunningApplication") as app, \
             patch("psutil.Process", return_value=process):
            host = Mock()
            host.activationPolicy.return_value = 0
            host.isTerminated.return_value = False
            app.runningApplicationWithProcessIdentifier_.return_value = host
            self.assertFalse(mac.raise_agent_window({"id": "6eff17ae-ef20-4d23-8cd7-e6fc909f4ccc", "pid": 123, "entrypoint": "claude-vscode"}))
            workspace.sharedWorkspace.assert_not_called()
            host.activateWithOptions_.assert_not_called()

    def test_live_process_and_reused_pid(self):
        started = mac.process_started(os.getpid())
        self.assertIsNotNone(started)
        self.assertTrue(mac.pid_alive(os.getpid(), started))
        self.assertFalse(mac.pid_alive(os.getpid(), started - 1))
        self.assertFalse(mac.pid_alive(-1))

    def test_new_process_cannot_impersonate_old_session(self):
        with patch.object(mac, "process_started", return_value=2000.125):
            self.assertEqual(mac.session_process_start({"startedAt": 1000000}, 123), -1)
            self.assertEqual(mac.session_process_start({"startedAt": 2000125}, 123), 2000.125)

    def test_no_termination_for_subagent_or_unverified_pid(self):
        with patch.object(mac, "pid_alive", return_value=True), patch("psutil.Process") as process:
            self.assertFalse(mac.terminate_agent({"pid": 123, "sub": True, "started_at": 100}))
            self.assertFalse(mac.terminate_agent({"pid": 123}))
            process.assert_not_called()

    def test_pid_reused_between_initial_check_and_signal_is_rejected(self):
        process = Mock()
        process.create_time.return_value = 201
        with patch.object(mac, "pid_alive", return_value=True), \
             patch("psutil.Process", return_value=process):
            self.assertFalse(mac.terminate_agent({"pid": 123, "started_at": 200}))
            process.send_signal.assert_not_called()


@unittest.skipUnless(sys.platform == "darwin", "macOS backend")
class StartupAndLockTests(unittest.TestCase):
    def test_startup_preserves_spaces_in_arguments_and_removes_only_its_plist(self):
        import plistlib
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "widget.plist"
            command = ["/Applications/Smith Agents.app/Contents/MacOS/Smith Agents"]
            with patch.object(mac, "startup_path", return_value=path), \
                 patch.object(mac, "launch_argv", return_value=(command, "/Applications")):
                mac.set_autostart(True)
                self.assertTrue(mac.autostart_enabled())
                payload = plistlib.loads(path.read_bytes())
                self.assertEqual(payload["ProgramArguments"], command)
                self.assertTrue(payload["RunAtLoad"])
                mac.set_autostart(False)
                self.assertFalse(path.exists())

    def test_second_process_cannot_take_lock_then_recovers_after_release(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SMITH_AGENTS_CONFIG_DIR": directory}):
            self.assertTrue(mac.claim_single_instance())
            command = [sys.executable, "-c", "from smith_agents.platform_darwin import claim_single_instance; print(claim_single_instance())"]
            try:
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.strip(), "False")
            finally:
                mac.release_single_instance()
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), "True")


if __name__ == "__main__":
    unittest.main()

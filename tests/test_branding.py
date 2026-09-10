import ast
import os
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch
import tempfile
import subprocess

from test_core import core
from smith_agents import branding


class BrandingTests(unittest.TestCase):
    def test_new_config_override_takes_precedence_and_legacy_still_works(self):
        with patch.dict(os.environ, {"CLAUDE_WIDGET_CONFIG_DIR": "/legacy-test"}, clear=True):
            self.assertEqual(branding.config_override(), "/legacy-test")
            os.environ["SMITH_AGENTS_CONFIG_DIR"] = "/smith-test"
            self.assertEqual(branding.config_override(), "/smith-test")
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(branding.config_override())

    def test_windows_launcher_prefers_smith_but_accepts_existing_shortcuts(self):
        # Exercise the OS-independent path lookup without loading Win32 APIs.
        source = Path(core.SCRIPT_DIR)/'platform_win32.py'
        tree = ast.parse(source.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_entry_exe')
        path = types.SimpleNamespace(join=os.path.join, exists=Mock())
        scope = {'os': types.SimpleNamespace(path=path),
                 'sysconfig': types.SimpleNamespace(get_path=lambda _: '/scripts'),
                 'COMMAND': branding.COMMAND, 'LEGACY_COMMAND': branding.LEGACY_COMMAND}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), scope)
        for available, expected in (({'smith-agents.exe','claude-widget.exe'}, 'smith-agents.exe'),
                                    ({'claude-widget.exe'}, 'claude-widget.exe'), (set(), None)):
            path.exists.side_effect = lambda value: os.path.basename(value) in available
            result = scope['_entry_exe']()
            self.assertEqual(os.path.basename(result) if result else None, expected)

    def test_windows_startup_migrates_only_after_replacement_succeeds(self):
        source = Path(core.SCRIPT_DIR)/'platform_win32.py'
        tree = ast.parse(source.read_text())
        wanted = {'_legacy_startup_path', 'autostart_enabled', 'set_autostart'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        with tempfile.TemporaryDirectory() as directory:
            new = Path(directory)/'Smith Agents.lnk'
            old = Path(directory)/'ClaudeUsageWidget.lnk'
            old.write_text('existing shortcut')
            run = Mock(side_effect=subprocess.CalledProcessError(1, 'powershell'))
            scope = {'os': os, 'SCRIPT_DIR': directory,
                     'startup_path': lambda: str(new),
                     '_launch_argv': lambda: (['smith-agents.exe'], directory),
                     'subprocess': types.SimpleNamespace(run=run, list2cmdline=subprocess.list2cmdline)}
            exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), scope)
            self.assertTrue(scope['autostart_enabled']())
            with self.assertRaises(subprocess.CalledProcessError):
                scope['set_autostart'](True)
            self.assertTrue(old.exists())
            run.side_effect = lambda *args, **kwargs: new.write_text('replacement shortcut')
            scope['set_autostart'](True)
            self.assertTrue(new.exists())
            self.assertFalse(old.exists())
            self.assertTrue(scope['autostart_enabled']())
            scope['set_autostart'](False)
            self.assertFalse(scope['autostart_enabled']())


if __name__ == '__main__':
    unittest.main()

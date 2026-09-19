"""Slow session/window inspection must not stop input or launch duplicate scans."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from smith_agents import app, core


class AgentPollingTests(unittest.TestCase):
    def widget(self):
        w = app.SmithAgentsWidget.__new__(app.SmithAgentsWidget)
        w.config = dict(core.DEFAULTS, tucked=True, usage_provider='hermes')
        w.lock = threading.Lock()
        w.stopping = threading.Event()
        w._agents_scan = 0
        w._agent_scan_running = False
        w._pending_agents = None
        w.agents = [dict(core.demo_agents()[1], id='old', _reply_expanded=True)]
        w._figure_assignments = Mock()
        w._portrait_assignments = Mock()
        w._save_config = Mock()
        w._repaint = Mock()
        w._peek_open_id = None
        w._peek_usage_open = False
        w._confirm_kill = None
        return w

    def test_slow_scan_keeps_usage_click_responsive_and_merges_after_completion(self):
        w = self.widget()
        entered, release = threading.Event(), threading.Event()
        fresh = dict(w.agents[0], _reply_expanded=False)
        def windows(rows):
            entered.set()
            self.assertTrue(release.wait(3))
            rows[0]['_window_state'] = 'front'
        with patch.object(app, 'list_agents', return_value=[fresh]) as scan, \
             patch.object(app, 'decorate_agents', side_effect=lambda rows: rows), \
             patch.object(w, '_sync_agent_windows', side_effect=windows):
            try:
                w._scan_agents(100)
                self.assertTrue(entered.wait(2))
                for now in (100.1, 102, 103):
                    w._scan_agents(now)
                scan.assert_called_once()
                self.assertNotIn('_window_state', w.agents[0])
                w.agent_rows = [('provider:hermes', 0, 0, 20, 20, None)]
                w._drag = None
                w._on_release(SimpleNamespace(x=10, y=10))
                self.assertTrue(w._peek_usage_open)
                w._repaint.assert_called_once()
            finally:
                release.set()
                w._agent_scan_thread.join(3)
            self.assertFalse(w._agent_scan_running)
            # The UI merges the completed result, preserving expanded replies.
            w._scan_agents(100.2)
            self.assertEqual(w.agents[0]['_window_state'], 'front')
            self.assertTrue(w.agents[0]['_reply_expanded'])

    def test_failure_retains_rows_and_allows_retry(self):
        w = self.widget()
        original = w.agents
        with patch.object(app, 'list_agents', side_effect=RuntimeError('busy')), patch.object(app, 'log_line'):
            w._scan_agents(100)
            w._agent_scan_thread.join(3)
        self.assertIs(w.agents, original)
        self.assertFalse(w._agent_scan_running)
        self.assertIsNone(w._pending_agents)
        with patch.object(app, 'list_agents', return_value=[]), patch.object(app, 'decorate_agents', return_value=[]), \
             patch.object(w, '_sync_agent_windows'):
            w._scan_agents(102)
            w._agent_scan_thread.join(3)
            w._scan_agents(102.1)
        self.assertEqual(w.agents, [])
        with patch.object(app.threading, 'Thread') as thread:
            w._scan_agents(102.2)
            thread.assert_not_called()  # Empty results do not rescan every frame.

    def test_dismissal_during_scan_is_applied_when_result_arrives(self):
        w = self.widget()
        w.config['dismissed'] = ['old']
        w._pending_agents = w.agents
        w._agents_scan = 100
        w._scan_agents(100.1)
        self.assertEqual(w.agents, [])
        self.assertEqual(w.config['dismissed'], ['old'])

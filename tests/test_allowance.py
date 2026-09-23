"""Saved allowance readings, and how a rise is split between sessions.
Every reading and receipt here is synthetic."""
from datetime import datetime
import json
import os
import tempfile
import unittest

_config = tempfile.TemporaryDirectory(prefix="widget-allowance-tests-")
os.environ.setdefault("SMITH_AGENTS_CONFIG_DIR", _config.name)

from smith_agents import allowance_log
from smith_agents.dashboard import allowance

WEEK_RESET = '2026-09-25T16:00:00+00:00'
T0 = datetime(2026, 9, 23, 10, 0).timestamp()


def claude(week, five=None, reset=WEEK_RESET):
    metrics = [{'key': 'weekly', 'pct': week, 'resets_at': reset}]
    if five is not None:
        metrics.append({'key': 'session', 'pct': five, 'resets_at': '2026-09-23T15:00:00+00:00'})
    return metrics


def reading(minutes, pct, provider='Claude', reset=WEEK_RESET):
    return {'at': T0 + minutes * 60, 'provider': provider, 'window': 'week', 'pct': float(pct),
            'reset': reset}


def event(identity, session, minutes, fresh=1000, cached=0, output=0, provider='Claude'):
    at = datetime.fromtimestamp(T0 + minutes * 60).astimezone().isoformat(timespec='seconds')
    return {'id': identity, 'session': session, 'at': at, 'tokens': [fresh, cached, output],
            'provider': provider}


class Log(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.folder.name, 'allowance-history.jsonl')
        allowance_log._last.clear()
        allowance_log._pruned.clear()

    def tearDown(self):
        self.folder.cleanup()

    def test_only_the_account_wide_windows_are_saved(self):
        metrics = claude(40, five=12) + [{'key': 'scoped', 'pct': 90, 'resets_at': WEEK_RESET}]
        allowance_log.record('Claude', metrics, at=T0, path=self.path)
        allowance_log.record('Codex', [
            {'key': 'codex:codex:primary', 'label': '5h', 'pct': 3, 'resets_at': None},
            {'key': 'codex:codex:secondary', 'label': '1w', 'pct': 20, 'resets_at': None},
            {'key': 'codex:gpt-5-codex:secondary', 'label': '1w', 'pct': 70, 'resets_at': None},
        ], at=T0, path=self.path)
        rows = allowance_log.read(self.path)
        self.assertEqual(sorted((r['provider'], r['window'], r['pct']) for r in rows),
                         [('Claude', '5h', 12.0), ('Claude', 'week', 40.0),
                          ('Codex', '5h', 3.0), ('Codex', 'week', 20.0)])

    def test_an_unchanged_reading_is_saved_again_only_as_a_heartbeat(self):
        for minutes in (0, 5, 10):
            allowance_log.record('Claude', claude(40), at=T0 + minutes * 60, path=self.path)
        allowance_log.record('Claude', claude(41), at=T0 + 15 * 60, path=self.path)
        allowance_log.record('Claude', claude(41), at=T0 + 50 * 60, path=self.path)
        self.assertEqual([r['pct'] for r in allowance_log.read(self.path)], [40.0, 41.0, 41.0])

    def test_a_restart_remembers_the_last_saved_reading(self):
        allowance_log.record('Claude', claude(40), at=T0, path=self.path)
        allowance_log._last.clear()
        allowance_log._pruned.clear()
        allowance_log.record('Claude', claude(40), at=T0 + 60, path=self.path)
        self.assertEqual(len(allowance_log.read(self.path)), 1)

    def test_old_and_damaged_lines_are_dropped(self):
        old = {'at': T0 - 100 * 86400, 'provider': 'Claude', 'window': 'week', 'pct': 5, 'reset': None}
        with open(self.path, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(old) + '\nnot json\n{"at": "x"}\n')
        allowance_log.record('Claude', claude(40), at=T0, path=self.path)
        self.assertEqual([r['pct'] for r in allowance_log.read(self.path)], [40.0])

    def test_a_folder_it_cannot_write_is_not_an_error(self):
        allowance_log.record('Claude', claude(40), at=T0, path=os.path.join(self.path, 'no', 'x'))


class Split(unittest.TestCase):
    def test_one_session_alone_gets_the_whole_rise_exactly(self):
        result = allowance.attribute([reading(0, 40), reading(10, 43)],
                                     [event('r1', 'a', 3), event('r2', 'a', 8)])
        shares = result['shares']
        self.assertAlmostEqual(shares['r1']['week']['pct'] + shares['r2']['week']['pct'], 3)
        self.assertFalse(shares['r1']['week']['est'])

    def test_overlapping_sessions_split_by_weight_and_are_marked_estimated(self):
        result = allowance.attribute([reading(0, 40), reading(10, 44)],
                                     [event('r1', 'a', 3, fresh=3000), event('r2', 'b', 5, fresh=1000)])
        self.assertAlmostEqual(result['shares']['r1']['week']['pct'], 3)
        self.assertAlmostEqual(result['shares']['r2']['week']['pct'], 1)
        self.assertTrue(result['shares']['r2']['week']['est'])

    def test_output_weighs_more_than_cached_context(self):
        result = allowance.attribute([reading(0, 0), reading(10, 6)],
                                     [event('out', 'a', 2, fresh=0, output=1000),
                                      event('cache', 'b', 3, fresh=0, cached=10000)])
        self.assertAlmostEqual(result['shares']['out']['week']['pct'], 5)
        self.assertAlmostEqual(result['shares']['cache']['week']['pct'], 1)

    def test_a_rise_with_no_local_receipts_stays_unattributed(self):
        result = allowance.attribute([reading(0, 40), reading(10, 42)], [event('r', 'a', 20)])
        self.assertEqual(result['shares'], {})
        figures = result['days'][allowance.local_day(T0)]['Claude']['week']
        self.assertEqual((figures['unattributed'], figures['used']), (2, 2))

    def test_the_other_account_does_not_take_a_share(self):
        result = allowance.attribute([reading(0, 40), reading(10, 42)],
                                     [event('r', 'a', 5, provider='Codex')])
        self.assertEqual(result['shares'], {})

    def test_a_new_window_counts_from_zero_at_the_old_reset(self):
        reset = datetime.fromtimestamp(T0 + 5 * 60).astimezone().isoformat()
        later = datetime.fromtimestamp(T0 + 7 * 86400).astimezone().isoformat()
        result = allowance.attribute([reading(0, 90, reset=reset), reading(10, 2, reset=later)],
                                     [event('before', 'a', 3), event('after', 'b', 8)])
        self.assertNotIn('before', result['shares'])
        self.assertAlmostEqual(result['shares']['after']['week']['pct'], 2)
        self.assertTrue(result['days'][allowance.local_day(T0)]['Claude']['week']['reset'])

    def test_a_fall_inside_a_window_is_never_use(self):
        result = allowance.attribute([reading(0, 40), reading(10, 30), reading(20, 31)],
                                     [event('r1', 'a', 5), event('r2', 'a', 15)])
        self.assertNotIn('r1', result['shares'])
        self.assertAlmostEqual(result['shares']['r2']['week']['pct'], 1)

    def test_reset_jitter_is_the_same_window(self):
        result = allowance.attribute([reading(0, 40, reset='2026-09-25T16:00:00.928178+00:00'),
                                      reading(10, 41, reset='2026-09-25T16:00:01.100000+00:00')],
                                     [event('r', 'a', 5)])
        self.assertAlmostEqual(result['shares']['r']['week']['pct'], 1)

    def test_days_report_start_end_and_where_the_record_begins(self):
        result = allowance.attribute([reading(0, 40), reading(10, 43), reading(20, 45)], [])
        today = allowance.local_day(T0)
        figures = result['days'][today]['Claude']['week']
        self.assertEqual((figures['start'], figures['end'], figures['used']), (40, 45, 5))
        self.assertEqual(result['since'], {'Claude': today})

    def test_a_long_pause_is_marked_as_a_gap(self):
        result = allowance.attribute([reading(0, 40), reading(120, 41)], [event('r', 'a', 60)])
        self.assertTrue(result['days'][allowance.local_day(T0 + 7200)]['Claude']['week']['gap'])
        self.assertAlmostEqual(result['shares']['r']['week']['pct'], 1)



class Service(unittest.TestCase):
    def test_the_split_spans_every_project_and_rides_on_the_receipts(self):
        from unittest.mock import patch
        from smith_agents.dashboard import history
        from smith_agents.dashboard.service import DashboardService
        service = DashboardService()
        rows = [{'id': 'one', 'name': 'one', 'path': '/one'}, {'id': 'two', 'name': 'two', 'path': '/two'}]
        # History receipts name their session, not their account.
        bare = lambda *args: {k: v for k, v in event(*args).items() if k != 'provider'}
        data = {
            '/one': {'events': [bare('r1', 'a', 3)],
                     'sessions': [{'id': 'a', 'provider': 'Claude Code'}], 'actions': [], 'version': 'v1'},
            '/two': {'events': [bare('r2', 'b', 5)],
                     'sessions': [{'id': 'b', 'provider': 'Claude Code'}], 'actions': [], 'version': 'v2'},
        }
        with patch.object(service, '_projects', return_value=rows), \
             patch.object(service, '_overview', return_value=[]), \
             patch.object(history, 'snapshot', side_effect=lambda path: dict(data[path])), \
             patch.object(allowance_log, 'read', return_value=[reading(0, 40), reading(10, 42)]):
            result = service.snapshot('one')
        # Two projects' sessions overlapped, so each took half, as an estimate.
        self.assertEqual(result['events'][0]['allow']['week'], {'pct': 1.0, 'est': True})
        self.assertEqual(result['allowance']['since'], {'Claude': allowance.local_day(T0)})
        self.assertNotIn('allow', data['/one']['events'][0])


if __name__ == '__main__':
    unittest.main()

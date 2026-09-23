import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SMITH_AGENTS_CONFIG_DIR", tempfile.mkdtemp(prefix="widget-hermes-account-"))

from smith_agents import core, hermes_account, hermes_usage, hermes_usage_ui, tucked
from smith_agents.dashboard import account


def reading(**extra):
    return dict({'left': 15.75, 'plan': 'Plus', 'plan_spent': 22.0, 'plan_total': 22.0,
                 'plan_left': 0.0, 'topup_left': 15.75, 'status': 'healthy',
                 'renews': 'Oct 17, 2026', 'renews_at': '2026-10-17T06:48:46Z'}, **extra)


class Install(unittest.TestCase):
    def fake(self, folder):
        code = Path(folder) / 'hermes-agent'
        (code / 'agent').mkdir(parents=True)
        (code / 'agent' / 'billing_usage.py').write_text('')
        python = code / ('venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python')
        python.parent.mkdir(parents=True)
        python.write_text('')
        return code, python

    def test_no_install_is_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(hermes_account.install(folder))
            with self.assertRaises(hermes_account.Unavailable):
                hermes_account.read(folder)

    def test_only_figures_are_kept_from_the_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            code, python = self.fake(folder)
            printed = json.dumps(dict(reading(), available=True, access_token='secret', left=15.75))
            calls = []
            def run(argv, **kwargs):
                calls.append((argv, kwargs))
                return SimpleNamespace(returncode=0, stdout='noise\n' + printed + '\n')
            result = hermes_account.read(folder, run=run)
        self.assertEqual(result['left'], 15.75)
        self.assertEqual(result['plan'], 'Plus')
        self.assertNotIn('access_token', result)
        self.assertNotIn('secret', json.dumps(result))
        argv, kwargs = calls[0]
        self.assertEqual(argv[0], str(python))
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)

    def test_failures_and_sign_out_are_reported_not_raised_raw(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fake(folder)
            for out, code in (('', 1), ('not json', 0), (json.dumps({'available': False}), 0),
                              (json.dumps({'available': True, 'left': None}), 0)):
                with self.subTest(out=out):
                    with self.assertRaises(hermes_account.Unavailable):
                        hermes_account.read(folder, run=lambda *a, **k: SimpleNamespace(returncode=code, stdout=out))
            def timeout(*a, **k):
                raise subprocess.TimeoutExpired('python', 1)
            with self.assertRaises(hermes_account.Unavailable):
                hermes_account.read(folder, run=timeout)


class SpendTracking(unittest.TestCase):
    def test_spend_and_pace_come_from_balance_drops(self):
        now = [1000.0]
        spend = hermes_account.Spend(clock=lambda: now[0])
        first = spend.add(20.0)
        self.assertEqual((first['spent'], first['pace']), (0.0, None))
        now[0] += 1800
        result = spend.add(19.0)
        self.assertEqual(result['spent'], 1.0)
        self.assertAlmostEqual(result['pace'], 2.0)
        self.assertAlmostEqual(result['hours_left'], 9.5)

    def test_a_top_up_is_not_negative_spend(self):
        now = [0.0]
        spend = hermes_account.Spend(clock=lambda: now[0])
        spend.add(10.0); now[0] += 600; spend.add(9.0)
        now[0] += 600
        result = spend.add(30.0)                    # topped up
        self.assertEqual(result['spent'], 1.0)
        self.assertIsNone(result['pace'])

    def test_pace_waits_for_readings_some_minutes_apart(self):
        now = [0.0]
        spend = hermes_account.Spend(clock=lambda: now[0])
        spend.add(10.0); now[0] += 60
        self.assertIsNone(spend.add(9.9)['pace'])

    def test_words_for_money_and_time(self):
        self.assertEqual(hermes_account.money(15.748), '$15.75')
        self.assertEqual(hermes_account.money(None), '—')
        self.assertEqual(hermes_account.duration(15.6), '~16 h')
        self.assertEqual(hermes_account.duration(0.2), '~12 min')
        self.assertEqual(hermes_account.duration(72), '~3 d')


class TodayCost(unittest.TestCase):
    def test_a_session_across_midnight_counts_its_share_of_today(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE sessions (id TEXT, started_at REAL, estimated_cost_usd REAL)')
                db.execute('CREATE TABLE messages (session_id TEXT, role TEXT, timestamp REAL)')
                midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
                db.execute("INSERT INTO sessions VALUES ('a', ?, 4.0)", (midnight - 3600,))
                db.execute("INSERT INTO sessions VALUES ('old', ?, 9.0)", (midnight - 86400 * 3,))
                for at in (midnight - 60, midnight + 60, midnight + 120, midnight + 180):
                    db.execute("INSERT INTO messages VALUES ('a', 'assistant', ?)", (at,))
                db.execute("INSERT INTO messages VALUES ('old', 'assistant', ?)", (midnight - 86400 * 3,))
            data = hermes_usage.fetch(folder)
        self.assertAlmostEqual(data['today_cost'], 3.0)


class WidgetDrawing(unittest.TestCase):
    def data(self, with_account=True):
        data = hermes_usage.select(hermes_usage.demo_data(), None, [], 0)
        data['today_cost'] = 4.32
        if with_account:
            data['account'] = {'reading': reading(), 'updated': time.time(), 'error': None,
                               'spend': {'spent': 0.42, 'since': time.time() - 1500, 'pace': 1.01, 'hours_left': 15.6}}
        return data

    def test_the_header_fields_are_left_and_today_with_a_balance(self):
        labels = [r[0] for r in hermes_usage_ui.square_readings(self.data())]
        self.assertEqual(labels, ['Left', 'Today'])
        self.assertEqual(hermes_usage_ui._amounts(self.data()), ('$15.75', '$4.32'))
        # One cell is a dollar for the balance; the scale says so.
        self.assertEqual(hermes_usage_ui.square_readings(self.data())[0][2], '$1/□')

    def test_without_a_balance_the_session_fields_stay(self):
        labels = [r[0] for r in hermes_usage_ui.square_readings(self.data(False))]
        self.assertEqual(labels, ['Tokens', 'Est. cost'])

    def test_the_account_replaces_the_session_pages_and_every_view_renders(self):
        from PIL import Image, ImageDraw
        data, plain = self.data(), self.data(False)
        self.assertLess(core.usage_height([], data), core.usage_height([], plain))
        pen = ImageDraw.Draw(Image.new('RGBA', (core.CONSOLE_W, core.px(700))))
        # With a balance there is no saved-session pager; without one it stays.
        self.assertEqual(hermes_usage_ui.panel(pen, dict(data, sessions=data['sessions'] * 2), 0), [])
        two = dict(plain, sessions=plain['sessions'] * 2)      # the pager needs two to page
        kinds = {box[0] for box in hermes_usage_ui.panel(pen, two, 0)}
        self.assertIn('hermes-session', kinds)
        now = time.time()
        for item in (data, plain):
            with self.subTest(account=bool(item.get('account'))):
                core.render_console([], item, item, [], now, tab='usage', provider='hermes')
                for side in ('right', 'top'):
                    tucked.render([], [], None, now, side=side, provider='hermes', usage_open=True,
                                  usage_data=item, max_width=core.px(900), max_height=core.px(700))


class DashboardBalance(unittest.TestCase):
    def test_only_figures_reach_the_page(self):
        raw = dict(reading(), today=4.32, token='secret', spend={'spent': 0.5, 'pace': 1.0, 'extra': 'x'})
        clean = account.Readings.balance(raw)
        self.assertEqual(clean['left'], 15.75)
        self.assertEqual(clean['spend'], {'spent': 0.5, 'since': None, 'pace': 1.0, 'hours_left': None})
        self.assertNotIn('secret', json.dumps(clean))
        self.assertIsNone(account.Readings.balance({'left': 'lots'}))

    def test_a_widget_balance_is_a_reading_not_an_error(self):
        readings = account.Readings(lambda: [{'provider': 'Hermes', 'metrics': [], 'credits': None,
                                              'balance': dict(reading(), today=1.0),
                                              'observed_at': time.time(), 'error': None}])
        provider = readings.snapshot()['providers'][0]
        self.assertIsNone(provider['error'])
        self.assertFalse(provider['stale'])
        self.assertEqual(provider['balance']['plan'], 'Plus')

    def test_hermes_is_collected_only_where_installed(self):
        with patch.object(hermes_account, 'install', return_value=None):
            self.assertEqual([n for n, _ in account.collectors()], ['Claude', 'Codex'])
        with patch.object(hermes_account, 'install', return_value=('x', 'y')):
            self.assertEqual([n for n, _ in account.collectors()], ['Claude', 'Codex', 'Hermes'])


if __name__ == '__main__':
    unittest.main()

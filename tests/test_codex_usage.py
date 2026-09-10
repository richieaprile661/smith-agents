"""Account reads, data semantics, and switching providers without changing agents."""
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from smith_agents import codex_usage as usage, tucked
from test_core import core, SmithAgentsWidget


def limits():
    return {"rateLimitsByLimitId": {
        "extra": {"limitName": "Extra model", "primary": {"usedPercent": 12, "windowDurationMins": 15}},
        "codex": {"primary": {"usedPercent": 71, "windowDurationMins": 10080, "resetsAt": 1800000000},
                  "secondary": {"usedPercent": 31, "windowDurationMins": 300}}},
        "rateLimits": {"primary": {"usedPercent": 99, "windowDurationMins": 300}}}


class UsageDataTests(unittest.TestCase):
    def test_actual_windows_multiple_buckets_and_no_double_counting_legacy_view(self):
        metrics = usage.build_metrics(limits())
        self.assertEqual(len(metrics), 3)
        self.assertEqual([m['label'] for m in metrics], ['1w', '5h', '15m'])
        self.assertEqual([m['pct'] for m in metrics], [71, 31, 12])
        self.assertIn('+00:00', metrics[0]['resets_at'])
        self.assertEqual(metrics[0]['key'], 'codex:codex:primary')

    def test_unknown_invalid_and_zero_limits_remain_distinct(self):
        for value in (None, True, -1, '37', float('nan'), float('inf')):
            self.assertEqual(usage.build_metrics({'rateLimits': {'primary': {'usedPercent': value}}}), [])
        self.assertEqual(usage.build_metrics({}), [])
        self.assertEqual(usage.build_metrics({'rateLimits': {'primary': {'usedPercent': 0}}})[0]['pct'], 0)
        self.assertIsNone(usage.build_metrics({'rateLimits': {'primary': {'usedPercent': 20, 'resetsAt': 1e100}}})[0]['resets_at'])

    def test_daily_stats_filter_dates_keep_unknown_summary_and_avoid_cumulative_sum(self):
        stats = usage.build_stats({'summary': {'lifetimeTokens': 9000, 'currentStreakDays': None},
            'dailyUsageBuckets': [{'startDate':'2026-09-08','tokens':100}, {'startDate':'2026-09-09','tokens':200},
                {'startDate':'2026-09-10','tokens':50}, {'startDate':'2026-09-11','tokens':9999},
                {'startDate':'2026-08-01','tokens':9999}]}, days=3, today=date(2026,9,10))
        self.assertEqual(stats['spark'], [100,200,50])
        self.assertEqual(stats['tokens'],350)
        self.assertEqual(stats['lifetime_tokens'],9000)
        self.assertIsNone(stats['current_streak_days'])
        empty = usage.build_stats({})
        self.assertIsNone(empty['tokens']);self.assertFalse(empty['spark_known'])
        self.assertEqual(usage.build_stats({'dailyUsageBuckets': []})['tokens'],0)

    def test_codex_metric_cycle_includes_each_actual_bucket(self):
        metrics=usage.build_metrics(limits());mode='worst';seen=[]
        for _ in metrics:
            seen.append(core.bar_reading(metrics, None, mode)['key'])
            mode=core.next_bar_mode(metrics,mode)
        self.assertEqual(len(set(seen)),3);self.assertEqual(mode,'worst')


class AccountProtocolTests(unittest.TestCase):
    def test_windows_npm_shim_resolves_native_binary_without_shell(self):
        import psutil
        with tempfile.TemporaryDirectory() as directory:
            shim=Path(directory)/'codex.cmd'
            binary=Path(directory)/'node_modules/@openai/codex/vendor/windows/bin/codex.exe'
            binary.parent.mkdir(parents=True);binary.write_bytes(b'fixture')
            with patch.object(psutil,'process_iter',return_value=[]), patch.object(usage.shutil,'which',return_value=str(shim)), \
                 patch.object(usage.sys,'platform','win32'):
                self.assertEqual(usage.executable(),str(binary))

    def test_handshake_and_account_reads_only_with_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'server.py';log=Path(directory)/'methods.jsonl'
            script.write_text('''import json,sys
for line in sys.stdin:
    item=json.loads(line)
    with open(sys.argv[1], 'a') as f:f.write(json.dumps(item)+'\\n')
    if 'id' in item:
        print('non-json diagnostic',flush=True)
        print(json.dumps({'method':'notification','params':{}}),flush=True)
        print(json.dumps({'id':item['id'],'result':{}}),flush=True)
''')
            client=usage.AccountClient([sys.executable,str(script),str(log)],timeout=1)
            with patch.object(usage,'AccountClient',return_value=client):
                self.assertEqual(usage.fetch(),{'limits':{},'activity':{}})
            methods=[json.loads(line)['method'] for line in log.read_text().splitlines()]
            self.assertEqual(methods,['initialize','initialized','account/rateLimits/read','account/usage/read'])
            self.assertIsNotNone(client.child.poll());self.assertFalse(client.reader.is_alive())

    def test_error_on_limits_still_reads_activity(self):
        class Client:
            closed=False
            def request(self, method, params=None):
                if method=='account/rateLimits/read':raise usage.Unavailable('Sign in to Codex')
                return {'summary':{}} if method=='account/usage/read' else {}
            def send(self,*args):pass
            def close(self):self.closed=True
        client=Client()
        with patch.object(usage,'AccountClient',return_value=client):result=usage.fetch()
        self.assertIn('limits_error',result);self.assertIn('activity',result);self.assertTrue(client.closed)

    def test_timeout_is_bounded_and_only_our_spawned_process_is_closed(self):
        client=usage.AccountClient([sys.executable,'-c','import time; time.sleep(20)'],timeout=.05)
        try:
            with self.assertRaisesRegex(usage.Unavailable,'timed out'):client.request('initialize')
        finally:client.close()
        self.assertIsNotNone(client.child.poll())


class ProviderViewTests(unittest.TestCase):
    def widget(self):
        w=SmithAgentsWidget.__new__(SmithAgentsWidget)
        w.lock=threading.Lock();w.config={'usage_provider':'claude','bar_mode':'weekly','console_tab':'usage'}
        w.metrics=[{'key':'weekly','pct':15}];w.stats={'tokens':123};w.spend=None;w.plan=None
        w.active=['project'];w.error=None;w.updated_at=None
        w.agents=core.demo_agents()
        w.codex_data={'metrics':usage.build_metrics(limits()),'stats':usage.build_stats({}),
                      'usage_error':None,'stats_error':'Stats unavailable'}
        return w

    def test_selection_changes_usage_and_stats_not_agents_and_resets_metric_cycle(self):
        w=self.widget();agents=w.agents
        with patch.object(w,'_save_config'):
            w.set_usage_provider('codex')
            self.assertEqual(w._snapshot()[0][0]['pct'],71)
            self.assertIsNone(w._snapshot()[-1]['tokens'])
            self.assertEqual(w.config['bar_mode'],'worst');self.assertIs(w.agents,agents)
            w.set_usage_provider('claude')
            self.assertEqual(w._snapshot()[0][0]['pct'],15)
            self.assertEqual(w._snapshot()[-1]['tokens'],123)

    def test_provider_buttons_beat_bar_click_and_tucked_buttons_stay_inside_rail(self):
        metrics=usage.build_metrics(limits());stats=usage.build_stats({})
        for tab in ('usage','stats','agents'):
            for expanded in (False,True):
                image,boxes=core.render_console(metrics,None,stats,core.demo_agents(),0,tab=tab,provider='codex',expanded=expanded)
                target=next(b for b in boxes if b[0]=='provider:claude')
                x,y=(target[1]+target[3])/2,(target[2]+target[4])/2
                first=next(b for b in boxes if b[1]<=x<=b[3] and b[2]<=y<=b[4])
                self.assertEqual(first[0],'provider:claude')
        for edge in tucked.EDGES:
            image,boxes,layout=tucked.render(core.demo_agents(),metrics,metrics[0],0,side=edge,provider='codex')
            box=next(b for b in boxes if b[0]=='provider:toggle')
            self.assertGreaterEqual(box[1],0);self.assertLessEqual(box[3],image.width)
            self.assertLessEqual(box[4],image.height)

    def test_stats_error_does_not_replace_successful_limits_or_claude(self):
        w=self.widget();w.config.update(usage_provider='codex',console_tab='stats')
        self.assertEqual(w._data_notice(),'Stats unavailable')
        self.assertIsNone(w._snapshot()[4])
        w.config['console_tab']='usage';self.assertIsNone(w._data_notice())
        w.codex_data['usage_error']='Offline';w.codex_data['metrics_updated']=10
        self.assertIn('Last reading',w._data_notice())
        w.config['usage_provider']='claude';self.assertIsNone(w._data_notice())


if __name__=='__main__':unittest.main()

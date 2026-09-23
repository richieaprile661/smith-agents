"""The packaged dashboard: its reader, its local server, and the widget's
shortcut to it. Every record here is synthetic; nothing reads real history."""
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

_config = tempfile.TemporaryDirectory(prefix="widget-dashboard-tests-")
os.environ["SMITH_AGENTS_CONFIG_DIR"] = _config.name
# No test reads the real Hermes history on this computer.
os.environ["HERMES_HOME"] = os.path.join(_config.name, "no-hermes")

from smith_agents import core
from smith_agents.app import SmithAgentsWidget
from smith_agents.dashboard import account, history
from smith_agents.dashboard.history import (claude_parts, normalize_sessions, parse_claude_transcript,
                                            parse_rollout, parts, project_id, stamp)
from smith_agents.dashboard.service import DashboardService

PROJECT = Path('/test/project')


def session(identity='root', receipts=None, counters=None, source='cli'):
    return {'meta': {'id': identity, 'cwd': str(PROJECT), 'source': source,
                     'timestamp': '2026-09-17T10:00:00Z'},
            'receipts': receipts or [], 'counters': counters or [], 'actions': [],
            'model': 'test-model', 'invalid': 0}


def receipt(identity='response', owner='root', tokens=None):
    return {'id': identity, 'owner': owner, 'at': '2026-09-17T13:00:00+03:00',
            'tokens': tokens or [10, 80, 5]}


def jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
    return path


class Accounting(unittest.TestCase):
    """Ported from the design trial: the receipt arithmetic is the product."""

    def test_cache_and_reasoning_are_not_double_counted(self):
        self.assertEqual(parts({'input_tokens': 100, 'cached_input_tokens': 80,
                                'output_tokens': 20, 'reasoning_output_tokens': 15}), [20, 80, 20])
        self.assertIsNone(parts({'input_tokens': 5, 'cached_input_tokens': 8, 'output_tokens': 1}))
        self.assertIsNone(parts({'input_tokens': True, 'cached_input_tokens': 0, 'output_tokens': 1}))

    def test_receipts_override_counters_and_deduplicate(self):
        data = session(receipts=[receipt(), receipt()],
                       counters=[('2026-09-17T13:00:00+03:00', [1000, 8000, 500])])
        sessions, events, _, coverage = normalize_sessions([data, data], PROJECT)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sum(sum(e['tokens']) for e in events), 95)
        self.assertEqual(coverage['duplicateResponses'], 1)

    def test_global_dedup_and_foreign_receipts(self):
        one = session(receipts=[receipt(), receipt('foreign', 'other')])
        two = session('child', [receipt(owner='child'), receipt('new', 'child')])
        two['meta']['parent_thread_id'] = 'root'
        sessions, events, _, _ = normalize_sessions([one, two], PROJECT)
        self.assertEqual(len(events), 2)
        self.assertEqual(sum(sum(e['tokens']) for e in events), 190)
        self.assertTrue(sessions[1]['helper'])

    def test_internal_sessions_and_other_projects_excluded(self):
        guardian = session('guard', [receipt('review', 'guard')],
                           source={'subagent': {'other': 'guardian'}})
        other = session('outside', [receipt('outside', 'outside')])
        other['meta']['cwd'] = '/somewhere/else'
        sessions, events, _, coverage = normalize_sessions([guardian, other], PROJECT)
        self.assertEqual((sessions, events), ([], []))
        self.assertEqual(coverage['excludedInternalSessions'], 1)

    def test_counter_fallback_excludes_unknown_start_and_resets(self):
        rows = [('2026-09-17T13:00:00+03:00', [10, 80, 5]), ('2026-09-17T14:00:00+03:00', [15, 100, 7]),
                ('2026-09-17T15:00:00+03:00', [2, 10, 1]), ('2026-09-17T16:00:00+03:00', [5, 15, 3])]
        _, events, _, coverage = normalize_sessions([session(counters=rows)], PROJECT)
        self.assertEqual([e['tokens'] for e in events], [[5, 20, 2], [3, 5, 2]])
        self.assertEqual(coverage['undatedTokens'], 108)
        self.assertEqual(coverage['counterResets'], 1)

    def test_parser_exports_call_categories_without_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = jsonl(Path(directory) / 'test.jsonl', [
                {'type': 'session_meta', 'payload': session()['meta']},
                {'type': 'response_item', 'timestamp': '2026-09-17T10:00:00Z',
                 'payload': {'type': 'function_call', 'name': 'exec', 'call_id': 'a',
                             'arguments': 'tools.exec_command({cmd:"private command text"})'}},
                {'type': 'token_usage_record', 'timestamp': '2026-09-17T10:00:01Z',
                 'payload': {'thread_id': 'root', 'response_id': 'resp',
                             'usage': {'input_tokens': 100, 'cached_input_tokens': 80,
                                       'output_tokens': 20}}}])
            data = parse_rollout(path)
            self.assertEqual(data['actions'][0]['label'], 'Command calls')
            normalized = normalize_sessions([data], PROJECT)
            self.assertNotIn('private command text', json.dumps(normalized))
            self.assertEqual(normalized[1][0]['tokens'], [20, 80, 20])

    def test_cache_reads_are_cached_and_cache_writes_are_fresh(self):
        self.assertEqual(claude_parts({'input_tokens': 3, 'cache_creation_input_tokens': 7,
                                       'cache_read_input_tokens': 90, 'output_tokens': 10}), [10, 90, 10])
        self.assertIsNone(claude_parts({'input_tokens': 3, 'output_tokens': 1}))

    def assistant(self, message_id, block, **extra):
        return dict({'type': 'assistant', 'timestamp': '2026-09-20T11:00:00Z', 'sessionId': 'sess',
                     'cwd': str(PROJECT),
                     'message': {'id': message_id, 'model': 'claude-opus-5',
                                 'usage': {'input_tokens': 3, 'cache_creation_input_tokens': 7,
                                           'cache_read_input_tokens': 90, 'output_tokens': 10},
                                 'content': [block]}}, **extra)

    def test_a_streamed_message_counts_once_and_errors_do_not_count(self):
        with tempfile.TemporaryDirectory() as folder:
            path = jsonl(Path(folder) / 'sess.jsonl', [
                {'type': 'user', 'timestamp': '2026-09-20T10:59:00Z', 'sessionId': 'sess',
                 'cwd': str(PROJECT), 'message': {'role': 'user', 'content': 'hi'}},
                self.assistant('m1', {'type': 'text', 'text': 'a'}),
                self.assistant('m1', {'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {}}),
                self.assistant('m2', {'type': 'tool_use', 'id': 't2', 'name': 'Edit', 'input': {}}),
                self.assistant('err', {'type': 'text', 'text': 'x'}, isApiErrorMessage=True),
                self.assistant('syn', {'type': 'text', 'text': 'x'},
                               message={'id': 'syn', 'model': '<synthetic>', 'usage': {}, 'content': []})])
            data = parse_claude_transcript(path)
            self.assertEqual((data['meta']['id'], data['meta']['provider']), ('sess', 'Claude Code'))
            self.assertEqual([r['id'] for r in data['receipts']], ['m1', 'm2'])
            self.assertEqual(sum(sum(r['tokens']) for r in data['receipts']), 220)
            self.assertEqual([a['label'] for a in data['actions']], ['Command calls', 'File-edit calls'])
            sessions, events, _, _ = normalize_sessions([data], PROJECT)
            self.assertEqual((sessions[0]['provider'], len(events)), ('Claude Code', 2))

    def test_a_subagent_log_is_a_helper_of_its_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path = jsonl(Path(folder) / 'sess/subagents/agent-a1.jsonl',
                         [self.assistant('m9', {'type': 'text', 'text': 'a'},
                                         agentId='a1', isSidechain=True)])
            data = parse_claude_transcript(path)
            self.assertEqual((data['meta']['id'], data['meta']['parent_thread_id']), ('a1', 'sess'))
            sessions, _, _, _ = normalize_sessions([data], PROJECT)
            self.assertEqual((sessions[0]['helper'], sessions[0]['parent']), (True, 'sess'))

    def test_codex_title_is_the_first_real_request(self):
        with tempfile.TemporaryDirectory() as folder:
            path = jsonl(Path(folder) / 'rollout.jsonl', [
                {'type': 'session_meta', 'timestamp': '2026-09-20T10:00:00Z',
                 'payload': {'id': 's', 'cwd': str(PROJECT)}},
                {'type': 'response_item', 'timestamp': '2026-09-20T10:00:01Z',
                 'payload': {'type': 'message', 'role': 'user',
                             'content': [{'type': 'input_text', 'text': '<environment_context>\n<cwd>x</cwd>'}]}},
                {'type': 'response_item', 'timestamp': '2026-09-20T10:00:02Z',
                 'payload': {'type': 'message', 'role': 'user',
                             'content': [{'type': 'input_text', 'text': '  fix the   tuck bar\nplease '}]}}])
            self.assertEqual(parse_rollout(path)['title'], 'Fix the tuck bar please')

    def test_codex_helper_is_named_by_its_task(self):
        data = session('h')
        data['meta']['source'] = {'subagent': {'thread_spawn': {
            'parent_thread_id': 'root', 'agent_path': '/root/sol_homepage-fixes',
            'agent_nickname': 'Halley'}}}
        sessions, _, _, _ = normalize_sessions([data], PROJECT)
        self.assertEqual((sessions[0]['title'], sessions[0]['nickname'], sessions[0]['helper']),
                         ('Sol homepage fixes', 'Halley', True))

    def test_no_session_text_is_carried_into_a_snapshot(self):
        """The reader keeps titles and counts; it never ships reviewed copy."""
        sessions, _, _, _ = normalize_sessions([session(receipts=[receipt()])], PROJECT)
        self.assertNotIn('highlights', sessions[0])


class LocalDays(unittest.TestCase):
    """Days are grouped in the reader's own zone, not one baked in here."""

    def zone(self, name):
        previous = os.environ.get('TZ')
        os.environ['TZ'] = name
        time.tzset()
        self.addCleanup(lambda: (os.environ.__setitem__('TZ', previous)
                                 if previous is not None else os.environ.pop('TZ', None),
                                 time.tzset()))

    @unittest.skipUnless(hasattr(time, 'tzset'), 'needs tzset')
    def test_the_same_instant_lands_on_the_machine_local_day(self):
        self.zone('Pacific/Auckland')
        auckland = stamp('2026-09-17T22:30:00Z')
        self.zone('America/Los_Angeles')
        los_angeles = stamp('2026-09-17T22:30:00Z')
        self.assertEqual(auckland[:10], '2026-09-18')
        self.assertEqual(los_angeles[:10], '2026-09-17')
        # Different wall clocks, one instant: nothing is shifted, only named.
        self.assertEqual(datetime.fromisoformat(auckland), datetime.fromisoformat(los_angeles))
        self.assertEqual(datetime.fromisoformat(auckland),
                         datetime(2026, 9, 17, 22, 30, tzinfo=timezone.utc))

    @unittest.skipUnless(hasattr(time, 'tzset'), 'needs tzset')
    def test_the_zone_label_follows_the_machine(self):
        self.zone('Pacific/Auckland')
        self.assertEqual(history.zone_label(), datetime.now().astimezone().tzname())

    def test_an_unreadable_timestamp_is_dropped_rather_than_guessed(self):
        for value in (None, '', 'yesterday', 12345):
            self.assertIsNone(stamp(value))


class ProjectIdentity(unittest.TestCase):
    def test_an_id_is_stable_and_reveals_no_path(self):
        first = project_id('/Users/someone/Projects/thing')
        self.assertEqual(first, project_id(Path('/Users/someone/Projects/thing')))
        self.assertNotEqual(first, project_id('/Users/someone/Projects/other'))
        self.assertNotIn('Users', first)
        self.assertNotIn('thing', first)
        self.assertEqual(len(first), 16)

    def codex_home(self, entries):
        home = Path(tempfile.mkdtemp(prefix='codex-home-'))
        for name, cwd in entries:
            jsonl(home / 'sessions' / name, [{'type': 'session_meta', 'payload': {'cwd': cwd, 'id': name}}])
        return home

    def discover(self, root, entries):
        home = self.codex_home(entries)
        # The synthetic workspaces live in the system temp folder, which the
        # real filter treats as scratch; the scratch rule has its own test.
        with patch.object(history, 'SCRATCH', ('/no-such-scratch',)), \
             patch.dict(os.environ, {'CODEX_HOME': str(home),
                                     'CLAUDE_CONFIG_DIR': str(Path(root) / 'no-claude')}):
            return history.discover_projects()

    def test_only_workspaces_that_still_exist_are_offered(self):
        with tempfile.TemporaryDirectory() as root:
            alive = Path(root) / 'alive'
            alive.mkdir()
            found = self.discover(root, [('a.jsonl', str(alive)),
                                         ('b.jsonl', str(Path(root) / 'deleted'))])
        self.assertEqual([p['name'] for p in found], ['alive'])
        self.assertEqual(found[0]['id'], project_id(str(alive)))
        self.assertEqual(found[0]['sources'], {'Codex': 1, 'Claude Code': 0, 'Hermes': 0})

    def test_a_scratch_checkout_is_not_offered_as_a_project(self):
        with tempfile.TemporaryDirectory() as root:
            scratch = Path(tempfile.gettempdir()) / 'smith-scratch-test'
            scratch.mkdir(exist_ok=True)
            self.addCleanup(scratch.rmdir)
            home = self.codex_home([('c.jsonl', str(scratch))])
            with patch.dict(os.environ, {'CODEX_HOME': str(home),
                                         'CLAUDE_CONFIG_DIR': str(Path(root) / 'no-claude')}):
                self.assertEqual(history.discover_projects(), [])

    def test_two_workspaces_with_one_folder_name_stay_distinct(self):
        with tempfile.TemporaryDirectory() as root:
            for parent in ('one', 'two'):
                (Path(root) / parent / 'app').mkdir(parents=True)
            found = self.discover(root, [('a.jsonl', str(Path(root) / 'one/app')),
                                         ('b.jsonl', str(Path(root) / 'two/app'))])
        self.assertEqual(sorted(p['name'] for p in found), ['one/app', 'two/app'])
        self.assertEqual(len({p['id'] for p in found}), 2)


class Service(unittest.TestCase):
    def setUp(self):
        self.service = DashboardService()
        self.addCleanup(self.service.stop)

    def get(self, path, key=None, host=None, port=None):
        port = port or self.service.server.server_port
        request = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path))
        request.add_header('Host', host or '127.0.0.1:%d' % port)
        if key is not None:
            request.add_header('X-Smith-Key', key)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def test_repeated_opens_reuse_one_listener(self):
        first = self.service.start()
        for _ in range(4):
            self.assertEqual(self.service.start(), first)
        self.assertTrue(self.service.running)
        self.assertTrue(first[1] and len(first[1]) >= 32)

    def test_shutdown_releases_the_port(self):
        port, _ = self.service.start()
        self.service.stop()
        self.assertFalse(self.service.running)
        with socket.socket() as probe:
            probe.settimeout(2)
            with self.assertRaises(OSError):
                probe.connect(('127.0.0.1', port))
        # Stopping twice, or before ever starting, is not an error.
        self.service.stop()
        DashboardService().stop()

    def test_a_restart_mints_a_new_key(self):
        _, first = self.service.start()
        self.service.stop()
        _, second = self.service.start()
        self.assertNotEqual(first, second)

    def test_the_page_loads_but_its_data_needs_this_run_key(self):
        _, key = self.service.start()
        for path in ('/', '/app.js', '/session.js', '/style.css', '/plan-usage.js'):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 200)
        for path in ('/api/projects', '/api/snapshot', '/api/allowance'):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 403)
                self.assertEqual(self.get(path, key='wrong-key')[0], 403)
        with patch.object(self.service, '_projects', return_value=[]):
            self.assertEqual(self.get('/api/projects', key=key)[0], 200)

    def test_another_hostname_is_refused(self):
        _, key = self.service.start()
        port = self.service.server.server_port
        self.assertEqual(self.get('/', host='dashboard.example.com')[0], 403)
        self.assertEqual(self.get('/api/projects', key=key, host='attacker.test:%d' % port)[0], 403)
        self.assertEqual(self.get('/', host='localhost:%d' % port)[0], 200)

    def test_there_is_no_route_that_reads_a_file_path(self):
        self.service.start()
        for path in ('/../../etc/hosts', '/smith_agents/core.py', '/web/app.js', '/logo.png/../app.js'):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 404)

    def test_every_reply_carries_the_page_policy(self):
        _, key = self.service.start()
        _, _, headers = self.get('/')
        self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_the_browser_is_never_told_where_a_project_lives(self):
        _, key = self.service.start()
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / 'workspace'
            folder.mkdir()
            identity = self.service.remember(folder)
            with patch.object(history, 'discover_projects', return_value=[]), \
                 patch.object(history, 'overview', return_value=[]), \
                 patch.object(history, 'snapshot', return_value={'events': [], 'sessions': []}):
                status, listed, _ = self.get('/api/projects', key=key)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(listed), [{'id': identity, 'name': 'workspace',
                                                       'sessions': 0, 'sources': {}}])
                self.assertNotIn(str(folder), listed.decode())
                _, body, _ = self.get('/api/snapshot?project=' + identity, key=key)
                self.assertNotIn(str(folder), body.decode())

    def test_an_unknown_project_falls_back_rather_than_failing(self):
        self.service.start()
        rows = [{'id': 'aaa', 'name': 'first', 'path': '/one', 'sessions': 3, 'sources': {}},
                {'id': 'bbb', 'name': 'second', 'path': '/two', 'sessions': 1, 'sources': {}}]
        with patch.object(self.service, '_projects', return_value=rows):
            self.assertEqual(self.service._chosen('bbb')['name'], 'second')
            self.assertEqual(self.service._chosen('deleted-id')['name'], 'first')
            self.assertEqual(self.service._chosen('')['name'], 'first')
        with patch.object(self.service, '_projects', return_value=[]):
            self.assertIsNone(self.service._chosen('aaa'))
            self.assertIn('No local', self.service.snapshot('aaa')['error'])

    def test_a_snapshot_is_read_once_and_named_by_the_project_list(self):
        self.service.start()
        rows = [{'id': 'aaa', 'name': 'one/app', 'path': '/one/app', 'sessions': 3, 'sources': {}}]
        reader = Mock(return_value={'project': 'app', 'events': [], 'sessions': []})
        with patch.object(self.service, '_projects', return_value=rows), \
             patch.object(self.service, '_overview', return_value=[]), \
             patch.object(history, 'snapshot', reader):
            first = self.service.snapshot('aaa')
            self.service.snapshot('aaa')
        self.assertEqual(first['project'], 'one/app')
        reader.assert_called_once_with('/one/app')

    def test_the_url_hands_over_the_key_where_a_server_never_sees_it(self):
        from urllib.parse import parse_qs, urlsplit
        url = self.service.url('a-project-id')
        parts = urlsplit(url)
        self.assertEqual((parts.scheme, parts.hostname, parts.path), ('http', '127.0.0.1', '/'))
        self.assertEqual(parts.port, self.service.server.server_port)
        # Nothing secret in the request line: no query, and the key only in
        # the fragment, which a browser does not send.
        self.assertEqual(parts.query, '')
        self.assertNotIn(self.service.token, url.partition('#')[0])
        fragment = parse_qs(parts.fragment)
        self.assertEqual(fragment['k'], [self.service.token])
        self.assertEqual(fragment['project'], ['a-project-id'])
        self.assertNotIn('project', parse_qs(urlsplit(self.service.url()).fragment))
        # Asking for a URL is what starts the server, once.
        self.assertTrue(self.service.running)


class AccountReadings(unittest.TestCase):
    def metric(self, key='session', pct=10.0, resets_at=None, label='5h'):
        # A window that resets in two hours, so the test does not age out.
        resets_at = resets_at or datetime.fromtimestamp(
            time.time() + 7200, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        return {'key': key, 'label': label, 'detail': label, 'pct': pct, 'resets_at': resets_at}

    def widget(self, readings):
        return account.Readings(lambda: readings)

    def reading(self, metrics, observed=None, **extra):
        return dict({'provider': 'Claude', 'metrics': metrics, 'credits': None,
                     'observed_at': observed if observed is not None else time.time(),
                     'error': None}, **extra)

    def test_a_running_widget_is_read_instead_of_the_account(self):
        readings = self.widget([self.reading([self.metric()])])
        with patch.object(account, 'read_claude', side_effect=AssertionError('no request')), \
             patch.object(account, 'read_codex', side_effect=AssertionError('no request')):
            snapshot = readings.snapshot()
        provider = snapshot['providers'][0]
        self.assertEqual(provider['source'], 'widget')
        self.assertEqual(provider['limits'][0]['remaining'], 90)
        self.assertFalse(provider['stale'])
        self.assertNotIn('retry_at', provider)

    def test_change_is_measured_from_the_first_reading_of_a_window(self):
        state = [self.reading([self.metric(pct=10.0)], observed=time.time() - 300)]
        readings = self.widget(state)
        readings.snapshot()
        state[0] = self.reading([self.metric(pct=22.0)])
        limit = readings.snapshot()['providers'][0]['limits'][0]
        self.assertEqual(limit['baseline_used'], 10.0)
        self.assertEqual(limit['change'], 12.0)
        self.assertGreater(limit['elapsed_seconds'], 200)

    def test_a_reset_or_a_decrease_starts_the_comparison_again(self):
        state = [self.reading([self.metric(pct=40.0)])]
        readings = self.widget(state)
        readings.snapshot()
        state[0] = self.reading([self.metric(pct=5.0)])            # a new window
        self.assertEqual(readings.snapshot()['providers'][0]['limits'][0]['change'], 0)
        state[0] = self.reading([self.metric(pct=60.0, resets_at='2026-09-23T09:00:00Z')])
        limit = readings.snapshot()['providers'][0]['limits'][0]
        self.assertEqual((limit['baseline_used'], limit['change']), (60.0, 0))

    def test_a_passed_reset_is_marked_rather_than_counted(self):
        readings = self.widget([self.reading([self.metric(pct=80.0, resets_at='2000-01-01T00:00:00Z')])])
        limit = readings.snapshot()['providers'][0]['limits'][0]
        self.assertTrue(limit['expired'])
        self.assertEqual(limit['change'], 0)

    def test_an_old_or_failed_reading_says_so(self):
        old = self.widget([self.reading([self.metric()], observed=time.time() - 4000)])
        self.assertTrue(old.snapshot()['providers'][0]['stale'])
        failed = self.widget([self.reading([], observed=0, error='Sign in to Claude')])
        provider = failed.snapshot()['providers'][0]
        self.assertTrue(provider['stale'])
        self.assertEqual(provider['error'], 'Sign in to Claude')

    def test_unreadable_percentages_are_dropped_not_shown_as_zero(self):
        readings = self.widget([self.reading([self.metric(pct=None), self.metric(key='w', pct=True),
                                              self.metric(key='x', pct=float('nan')),
                                              self.metric(key='ok', pct=137.0)])])
        limits = readings.snapshot()['providers'][0]['limits']
        self.assertEqual([(l['key'], l['used']) for l in limits], [('ok', 100)])

    def test_only_the_credit_line_leaves_the_process(self):
        readings = self.widget([self.reading([self.metric()], credits={
            'text': '$12.40 left', 'on_credits': True, 'raw': {'token': 'secret'}})])
        self.assertEqual(readings.snapshot()['providers'][0]['credits'],
                         {'text': '$12.40 left', 'on_credits': True, 'reset': None})
        self.assertIsNone(self.widget([self.reading([self.metric()], credits={})])
                          .snapshot()['providers'][0]['credits'])

    def test_without_a_widget_the_collector_backs_off_and_stays_readable(self):
        readings = account.Readings()
        error = core.UsageError('ratelimit', 'slow down', retry_after=300)
        with patch.object(account, 'read_claude', side_effect=error), \
             patch.object(account, 'read_codex', side_effect=OSError('offline')):
            first = readings.snapshot()['providers']
            claude = next(p for p in first if p['provider'] == 'Claude')
            self.assertEqual(claude['error'], account.BUSY_MESSAGE)
            self.assertTrue(claude['stale'])
            self.assertGreaterEqual(readings.state['Claude']['retry_at'] - time.time(), 290)
            self.assertGreaterEqual(readings.state['Codex']['retry_at'] - time.time(),
                                    account.MIN_INTERVAL - 5)
            # Inside the backoff, no second request is made.
            with patch.object(account, 'read_claude', side_effect=AssertionError('too soon')):
                readings.snapshot()

    def test_a_widget_that_cannot_answer_does_not_take_the_page_down(self):
        readings = account.Readings(Mock(side_effect=RuntimeError('locked')))
        with patch.object(account, 'read_claude', return_value=([self.metric()], None, time.time())), \
             patch.object(account, 'read_codex', return_value=([], None, time.time())):
            providers = readings.snapshot()['providers']
        self.assertEqual(providers[0]['source'], 'collector')


class ConsoleShortcut(unittest.TestCase):
    """The footer row: it is there on every tab, and it is clickable."""

    def boxes(self, **kwargs):
        options = dict(tab='agents', dashboard_project='smith-agents')
        options.update(kwargs)
        return core.render_console([], None, {}, options.pop('agents', core.demo_agents()),
                                   0, **options)

    def test_every_expanded_tab_ends_with_the_shortcut(self):
        for tab in ('agents', 'usage', 'stats'):
            for agents in ([], core.demo_agents()):
                with self.subTest(tab=tab, agents=len(agents)):
                    image, boxes = self.boxes(tab=tab, agents=agents)
                    kinds = [b[0] for b in boxes]
                    self.assertEqual(kinds.count('dashboard'), 1)
                    self.assertEqual(kinds.count('settings'), 1)
                    for kind in ('dashboard', 'settings'):
                        box = next(b for b in boxes if b[0] == kind)
                        self.assertGreaterEqual(box[1], 0)
                        self.assertLessEqual(box[3], core.CONSOLE_W)
                        self.assertLessEqual(box[4], image.height - 2 * core.SHADOW_PAD)

    def test_the_collapsed_bar_has_no_shortcut_to_click(self):
        _, boxes = self.boxes(expanded=False)
        self.assertNotIn('dashboard', [b[0] for b in boxes])

    def test_the_shortcut_never_sits_on_another_target(self):
        for tab in ('agents', 'usage', 'stats'):
            for agents in ([], core.demo_agents(),
                           [dict(a, state='closed') for a in core.demo_agents() if not a.get('sub')]):
                with self.subTest(tab=tab, agents=len(agents)):
                    _, boxes = self.boxes(tab=tab, agents=agents)
                    new_rows = [b for b in boxes if b[0] in ('dashboard', 'settings')]
                    for one in new_rows:
                        for other in boxes:
                            if other is one:
                                continue
                            overlap = (one[1] < other[3] and other[1] < one[3]
                                       and one[2] < other[4] and other[2] < one[4])
                            self.assertFalse(overlap, '%s overlaps %s' % (one[0], other[0]))

    def test_the_shortcut_fits_at_every_offered_size_and_theme(self):
        """Sizes and themes are baked in at import, so each needs its own run."""
        import json
        import subprocess
        import sys as _sys
        code = ('import json,time\n'
                'from smith_agents import core as c\n'
                'i,b=c.render_console([],None,{},c.demo_agents(),time.time(),'
                'dashboard_project="a-workspace-with-a-fairly-long-name")\n'
                'rows={k:(x0,y0,x1,y1) for k,x0,y0,x1,y1,_ in b}\n'
                'assert "dashboard" in rows and "settings" in rows, sorted(rows)\n'
                'assert rows["dashboard"][2] < rows["settings"][0], rows\n'
                'assert rows["settings"][2] <= c.CONSOLE_W, rows\n'
                'assert rows["settings"][3] <= i.height-2*c.SHADOW_PAD, rows\n'
                'print(c.THEME_NAME, c.ZOOM)\n')
        for theme in core.THEMES:
            for zoom in core.ZOOM_STEPS:
                with self.subTest(theme=theme, zoom=zoom), tempfile.TemporaryDirectory() as directory:
                    (Path(directory) / 'config.json').write_text(json.dumps({'theme': theme, 'zoom': zoom}))
                    env = dict(os.environ, SMITH_AGENTS_CONFIG_DIR=directory)
                    result = subprocess.run([_sys.executable, '-c', code], env=env,
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.split()[0], theme)

    def test_a_long_workspace_name_is_cut_rather_than_pushed_off_the_edge(self):
        _, boxes = self.boxes(dashboard_project='a-very-long-workspace-name-' * 4)
        dashboard = next(b for b in boxes if b[0] == 'dashboard')
        settings = next(b for b in boxes if b[0] == 'settings')
        self.assertLess(dashboard[3], settings[1])
        self.assertLessEqual(settings[3], core.CONSOLE_W)

    def test_the_shortcut_stays_put_while_the_agent_list_scrolls(self):
        rows = core.demo_agents()
        height = core.BAR_H + core.TAB_H + core.FOOT_H + core.px(120)
        seen = set()
        for scroll in (0, core.px(80), core.px(4000)):
            image, boxes = self.boxes(agents=rows, max_height=height, scroll=scroll)
            kinds = [b[0] for b in boxes]
            self.assertIn('dashboard', kinds)
            self.assertIn('settings', kinds)
            seen.add(tuple(b[1:5] for b in boxes if b[0] in ('dashboard', 'settings')))
            for box in (b for b in boxes if b[0] in ('dashboard', 'settings')):
                self.assertLessEqual(box[4], image.height - 2 * core.SHADOW_PAD)
        self.assertEqual(len(seen), 1, 'the footer row must not move with the scroll')

    def test_the_clear_closed_action_and_the_shortcut_share_the_footer(self):
        rows = [dict(a, state='closed') for a in core.demo_agents() if not a.get('sub')]
        _, boxes = self.boxes(agents=rows)
        kinds = [b[0] for b in boxes]
        self.assertIn('clear', kinds)
        self.assertIn('dashboard', kinds)
        clear = next(b for b in boxes if b[0] == 'clear')
        dashboard = next(b for b in boxes if b[0] == 'dashboard')
        self.assertLess(clear[4], dashboard[2], 'the shortcut sits below the count line')


class WidgetShortcut(unittest.TestCase):
    def widget(self, **config):
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = dict(core.DEFAULTS, **config)
        widget.agents = []
        widget.agent_open = None
        widget._save_config = Mock()
        widget._repaint = Mock()
        widget.root = Mock()
        return widget

    def agent(self, identity, cwd, **extra):
        return dict({'id': identity, 'cwd': cwd, 'state': 'working', 'sub': False}, **extra)

    def test_an_open_session_decides_which_workspace_opens(self):
        widget = self.widget()
        widget.agents = [self.agent('a', '/work/one'), self.agent('b', '/work/two')]
        widget.agent_open = 'b'
        self.assertEqual(widget._dashboard_target(), '/work/two')
        self.assertEqual(widget._dashboard_label(), 'two')

    def test_one_running_session_needs_no_selection(self):
        widget = self.widget()
        widget.agents = [self.agent('a', '/work/one'),
                         self.agent('helper', '/work/one', sub=True),
                         self.agent('old', '/work/three', state='closed')]
        self.assertEqual(widget._dashboard_target(), '/work/one')

    def test_several_sessions_and_no_selection_falls_back_instead_of_guessing(self):
        widget = self.widget(dashboard_project='/work/remembered')
        widget.agents = [self.agent('a', '/work/one'), self.agent('b', '/work/two')]
        self.assertEqual(widget._dashboard_target(), '/work/remembered')
        widget.config['dashboard_project'] = None
        self.assertIsNone(widget._dashboard_target())
        self.assertIsNone(widget._dashboard_label())

    def test_clicking_the_shortcut_opens_the_dashboard(self):
        widget = self.widget()
        widget.open_dashboard = Mock()
        _, widget.agent_rows = core.render_console([], None, {}, [], 0, dashboard_project='work')
        box = next(b for b in widget.agent_rows if b[0] == 'dashboard')
        widget._on_console_click(SimpleNamespace(x=(box[1] + box[3]) / 2, y=(box[2] + box[4]) / 2))
        widget.open_dashboard.assert_called_once()

    def test_the_settings_glyph_opens_the_widget_own_menu(self):
        widget = self.widget()
        _, widget.agent_rows = core.render_console([], None, {}, [], 0, dashboard_project='work')
        box = next(b for b in widget.agent_rows if b[0] == 'settings')
        event = SimpleNamespace(x=(box[1] + box[3]) / 2, y=(box[2] + box[4]) / 2)
        with patch('smith_agents.app.platform') as fake:
            widget._on_console_click(event)
        fake.popup_menu.assert_called_once_with(widget, event)

    def test_opening_runs_off_the_paint_thread_and_remembers_the_workspace(self):
        widget = self.widget()
        widget.agents = [self.agent('a', '/work/one')]
        with patch('smith_agents.dashboard.launch', return_value='http://127.0.0.1:1/#k=x') as launch, \
             patch('smith_agents.dashboard.SERVICE') as service, \
             patch('os.path.isdir', return_value=True), \
             patch('threading.Thread') as thread, \
             patch('smith_agents.app.platform') as fake:
            widget.open_dashboard()
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs['args'], ('/work/one',))
            self.assertTrue(thread.call_args.kwargs['daemon'])
            thread.call_args.kwargs['target'](*thread.call_args.kwargs['args'])
            launch.assert_called_once_with('/work/one')
            service.share_account_readings.assert_called_once()
            # The window is shown from the UI thread, not the worker.
            fake.open_dashboard_window.assert_not_called()
            widget.root.after.assert_called_once()
            widget.root.after.call_args[0][1]()
        fake.open_dashboard_window.assert_called_once_with('http://127.0.0.1:1/#k=x')
        self.assertEqual(widget.config['dashboard_project'], '/work/one')

    def test_a_window_that_fails_to_open_is_reported_and_does_not_raise(self):
        widget = self.widget()
        with patch('smith_agents.app.platform') as fake, \
             patch('smith_agents.app.log_line'):
            fake.open_dashboard_window.side_effect = RuntimeError('no webview')
            widget._show_dashboard('http://127.0.0.1:1/', '/work/one')
        fake.show_error.assert_called_once()
        self.assertNotEqual(widget.config.get('dashboard_project'), '/work/one')

    @unittest.skipUnless(sys.platform == 'win32', 'the Windows platform module')
    def test_windows_opens_one_webview_window_and_hands_it_each_address(self):
        from smith_agents import platform_win32 as win
        child = MagicMock()
        child.poll.return_value = None
        with patch.object(win, '_dashboard', None), \
             patch('importlib.util.find_spec', return_value=object()), \
             patch.object(win.subprocess, 'Popen', return_value=child) as popen:
            win.open_dashboard_window('http://127.0.0.1:1/#k=x')
            win.open_dashboard_window('http://127.0.0.1:1/#k=x&project=p')
        popen.assert_called_once()
        argv = popen.call_args[0][0]
        self.assertEqual(argv[1:], ['-m', 'smith_agents.dashboard.window'])
        self.assertNotIn('k=x', ' '.join(argv))        # the key never rides on a command line
        self.assertEqual([c.args[0] for c in child.stdin.write.call_args_list],
                         ['http://127.0.0.1:1/#k=x\n', 'http://127.0.0.1:1/#k=x&project=p\n'])

    @unittest.skipUnless(sys.platform == 'win32', 'the Windows platform module')
    def test_windows_without_pywebview_falls_back_to_edge_then_the_browser(self):
        from smith_agents import platform_win32 as win
        with patch.object(win, '_dashboard', None), \
             patch('importlib.util.find_spec', return_value=None), \
             patch.object(win, '_edge_path', return_value='C:/Edge/msedge.exe'), \
             patch.object(win.subprocess, 'Popen') as popen, \
             patch('webbrowser.open') as browser:
            win.open_dashboard_window('http://127.0.0.1:1/#k=x')
        self.assertEqual(popen.call_args[0][0][:2],
                         ['C:/Edge/msedge.exe', '--app=http://127.0.0.1:1/#k=x'])
        browser.assert_not_called()
        with patch.object(win, '_edge_path', return_value=None), \
             patch('webbrowser.open', return_value=True) as browser:
            win.open_in_browser('http://127.0.0.1:1/')
        browser.assert_called_once_with('http://127.0.0.1:1/')

    def test_git_history_never_flashes_a_console_window(self):
        with patch.object(history.subprocess, 'run',
                          return_value=SimpleNamespace(stdout='')) as run:
            history.git_milestones('/work/one')
        self.assertEqual(run.call_args.kwargs['creationflags'], history.NO_WINDOW)
        self.assertEqual(run.call_args.kwargs['stdin'], history.subprocess.DEVNULL)

    def test_the_window_process_opens_the_first_address_and_follows_later_ones(self):
        from smith_agents.dashboard import window as dashboard_window
        fake = MagicMock()
        view = fake.create_window.return_value
        fake.start.side_effect = lambda func, args, **_: func(*args)
        stdin = io.StringIO('http://127.0.0.1:1/#k=x\n'
                            'http://127.0.0.1:1/#k=x\n'
                            'http://127.0.0.1:1/#k=x&project=p\n')
        with patch.dict(sys.modules, {'webview': fake}):
            self.assertEqual(dashboard_window.main(stdin), 0)
        self.assertEqual(fake.create_window.call_args[0][1], 'http://127.0.0.1:1/#k=x')
        self.assertEqual(fake.start.call_args.kwargs['gui'], 'edgechromium')
        # The same address only raises the window; another one reloads it.
        view.evaluate_js.assert_called_once()
        self.assertIn('project=p', view.evaluate_js.call_args[0][0])
        self.assertEqual(view.show.call_count, 2)
        # The widget's exit closes stdin, and the window with it.
        view.destroy.assert_called_once()

    def test_the_window_process_falls_back_to_a_browser_without_webview2(self):
        from smith_agents.dashboard import window as dashboard_window
        fake = MagicMock()
        fake.start.side_effect = RuntimeError('WebView2 runtime missing')
        browser = MagicMock()
        with patch.dict(sys.modules, {'webview': fake,
                                      'smith_agents.platform_win32': SimpleNamespace(open_in_browser=browser)}), \
             patch('smith_agents.core.log_line') as log:
            self.assertEqual(dashboard_window.main(io.StringIO('http://127.0.0.1:1/#k=x\n')), 1)
        browser.assert_called_once_with('http://127.0.0.1:1/#k=x')
        self.assertIn('WebView2', log.call_args[0][0])

    def test_a_deleted_workspace_opens_the_dashboard_without_one(self):
        widget = self.widget(dashboard_project='/work/gone')
        with patch('smith_agents.dashboard.launch') as launch, \
             patch('smith_agents.dashboard.SERVICE'), \
             patch('os.path.isdir', return_value=False):
            widget._launch_dashboard('/work/gone')
        launch.assert_called_once_with(None)

    def test_a_failure_to_open_is_reported_and_does_not_raise(self):
        from smith_agents import dashboard
        widget = self.widget()
        for error in (dashboard.DashboardError('No browser here'), RuntimeError('boom')):
            with self.subTest(error=type(error).__name__):
                widget.root.reset_mock()
                with patch('smith_agents.dashboard.launch', side_effect=error), \
                     patch('smith_agents.dashboard.SERVICE'), \
                     patch('smith_agents.app.platform') as fake, \
                     patch('smith_agents.app.log_line'):
                    widget._launch_dashboard(None)
                    # The message is queued for the UI thread, never shown from
                    # the worker; run it here, still inside the patch.
                    widget.root.after.assert_called_once()
                    widget.root.after.call_args[0][1]()
                fake.show_error.assert_called_once()
                self.assertTrue(fake.show_error.call_args[0][0])

    def test_the_readings_handed_over_are_the_ones_the_widget_already_has(self):
        widget = self.widget()
        widget.lock = __import__('threading').Lock()
        widget.metrics = [{'key': 'session', 'pct': 12.0, 'label': '5h', 'resets_at': None}]
        widget.error = None
        widget.updated_at = datetime(2026, 9, 21, 12, 0)
        widget.codex_data = {'metrics': [{'key': 'codex:5h', 'pct': 30.0, 'label': '5h'}],
                             'credits': {'text': '$3 left', 'on_credits': False},
                             'metrics_updated': time.time(), 'usage_error': None}
        readings = widget._account_readings()
        self.assertEqual([r['provider'] for r in readings], ['Claude', 'Codex'])
        self.assertEqual(readings[0]['metrics'][0]['pct'], 12.0)
        self.assertEqual(readings[1]['credits']['text'], '$3 left')
        # And they normalize into what the page renders, with no account call.
        with patch.object(account, 'read_claude', side_effect=AssertionError('no request')):
            snapshot = account.Readings(widget._account_readings).snapshot()
        self.assertEqual(snapshot['providers'][0]['limits'][0]['remaining'], 88.0)

    def test_quitting_releases_the_dashboard_port(self):
        widget = self.widget()
        widget.stopping = Mock()
        widget.wake = widget.codex_wake = widget.hermes_wake = Mock()
        widget.tray = Mock()
        with patch('smith_agents.dashboard.shutdown') as shutdown:
            widget.quit()
        shutdown.assert_called_once_with()


class PackagedAssets(unittest.TestCase):
    def test_the_page_ships_inside_the_application(self):
        from smith_agents.dashboard import service
        for source, _ in service.ASSETS.values():
            path = source() if callable(source) else source
            if isinstance(path, bytes):                 # drawn at run time
                self.assertTrue(path.startswith(b'\x89PNG'))
                continue
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), '%s is missing from the package' % path)

    def test_nothing_points_at_the_repository_or_a_design_folder(self):
        package = Path(core.__file__).resolve().parent / 'dashboard'
        for path in sorted(package.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
            with self.subTest(file=path.name):
                for forbidden in ('design/real-project-dashboard', '.venv', '8768', '8770',
                                  'Asia/Jerusalem'):
                    self.assertNotIn(forbidden, text)

    def test_no_sample_allowance_percentage_is_presented_as_a_reading(self):
        page = (Path(core.__file__).resolve().parent / 'dashboard/web/app.js').read_text()
        self.assertNotIn('5% → 17%', page)
        self.assertIn('allowance not recorded', page)


class HermesHistory(unittest.TestCase):
    def database(self, folder):
        import sqlite3
        path = Path(folder) / 'state.db'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE sessions (id TEXT, parent_session_id TEXT, started_at REAL, '
                       'ended_at REAL, model TEXT, title TEXT, cwd TEXT, input_tokens INTEGER, '
                       'output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER)')
            db.execute('CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, '
                       'tool_name TEXT, timestamp REAL)')
            day = 1790000000.0                      # any fixed instant
            db.execute("INSERT INTO sessions VALUES ('main', NULL, ?, ?, 'm', 'Plan the work', '/work/one', 90, 30, 500, 10)",
                       (day, day + 86400 * 2))
            db.execute("INSERT INTO sessions VALUES ('kid', 'main', ?, NULL, 'm', NULL, '/work/one', 4, 2, 0, 0)", (day,))
            db.execute("INSERT INTO sessions VALUES ('else', NULL, ?, NULL, 'm', 'x', '/work/two', 1, 1, 0, 0)", (day,))
            for at in (day + 10, day + 20, day + 86400 * 2):
                db.execute("INSERT INTO messages VALUES ('main', 'assistant', 'private reply', NULL, ?)", (at,))
            db.execute("INSERT INTO messages VALUES ('main', 'tool', 'private output', 'terminal', ?)", (day + 15,))
            db.execute("INSERT INTO messages VALUES ('kid', 'user', 'Check the tests', NULL, ?)", (day,))
        return path.parent

    def test_hermes_totals_are_split_by_reply_day_and_kept_whole(self):
        with tempfile.TemporaryDirectory() as folder:
            parsed = history.hermes_sessions('/work/one', self.database(folder))
            sessions, events, actions, _ = history.normalize_sessions(parsed, '/work/one')
        main = next(s for s in sessions if s['id'] == 'hermes:main')
        self.assertEqual((main['provider'], main['title'], main['helper']), ('Hermes', 'Plan the work', False))
        kid = next(s for s in sessions if s['id'] == 'hermes:kid')
        self.assertEqual((kid['parent'], kid['helper'], kid['title']), ('hermes:main', True, 'Check the tests'))
        own = [e for e in events if e['session'] == 'hermes:main']
        self.assertEqual(len(own), 2)                               # two reply days
        self.assertTrue(all(e['estimated'] for e in own))
        # Fresh input includes cache writes; nothing is lost or added in the split.
        self.assertEqual([sum(e['tokens'][i] for e in own) for i in range(3)], [100, 500, 30])
        self.assertEqual([a['label'] for a in actions if a['session'] == 'hermes:main'], ['Command calls'])
        self.assertNotIn('private', json.dumps([sessions, events, actions]))

    def test_hermes_cost_is_split_like_tokens_and_models_are_listed(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            root = self.database(folder)
            with sqlite3.connect(Path(root) / 'state.db') as db:
                db.execute('ALTER TABLE sessions ADD COLUMN estimated_cost_usd REAL')
                db.execute("UPDATE sessions SET estimated_cost_usd = 3.0 WHERE id = 'main'")
                db.execute('CREATE TABLE session_model_usage (session_id TEXT, model TEXT, task TEXT, '
                           'api_call_count INTEGER, estimated_cost_usd REAL)')
                db.execute("INSERT INTO session_model_usage VALUES ('main', 'x/big', '', 5, 2.5)")
                db.execute("INSERT INTO session_model_usage VALUES ('main', 'x/small', 'approval', 2, 0.5)")
            sessions, events, *_ = history.normalize_sessions(history.hermes_sessions('/work/one', root), '/work/one')
        own = sorted((e for e in events if e['session'] == 'hermes:main'), key=lambda e: e['at'])
        self.assertAlmostEqual(sum(e['cost'] for e in own), 3.0)
        self.assertAlmostEqual(own[0]['cost'], 2.0)                 # two of three replies that day
        main = next(s for s in sessions if s['id'] == 'hermes:main')
        self.assertEqual([(m['model'], m['task'], m['calls']) for m in main['models']],
                         [('x/big', 'main work', 5), ('x/small', 'approval', 2)])
        # A database without the cost column still yields its sessions, with no cost.
        with tempfile.TemporaryDirectory() as folder:
            parsed = history.hermes_sessions('/work/one', self.database(folder))
        self.assertTrue(parsed)
        self.assertTrue(all('cost' not in r for p in parsed for r in p['receipts']))

    def test_workspaces_and_counts_include_hermes(self):
        with tempfile.TemporaryDirectory() as folder:
            database = self.database(folder)
            self.assertEqual(history.hermes_workspaces(database), {'/work/one': 2, '/work/two': 1})
            sessions, *_ = history.normalize_sessions(history.hermes_sessions('/work/one', database), '/work/one')
        self.assertEqual(history.session_counts(sessions),
                         {'sessions': 1, 'helpers': 1, 'providers': {'Hermes': 1}})

    def test_sessions_without_activity_are_not_counted(self):
        sessions = [{'id': 'a', 'provider': 'Codex'}, {'id': 'b', 'provider': 'Codex'},
                    {'id': 'h', 'provider': 'Codex', 'helper': True}]
        events = [{'session': 'a', 'at': '2026-09-01T10:00:00', 'tokens': [1, 0, 1]}]
        actions = [{'session': 'h', 'at': '2026-09-01T10:00:00', 'label': 'Command calls'}]
        self.assertEqual(history.session_counts(sessions, events, actions),
                         {'sessions': 1, 'helpers': 1, 'providers': {'Codex': 1}})

    def test_a_missing_hermes_database_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(history.hermes_sessions('/work/one', folder), [])
            self.assertEqual(history.hermes_workspaces(folder), {})


class BrandMark(unittest.TestCase):
    def test_the_header_logo_follows_the_theme(self):
        from smith_agents import artwork, core
        from smith_agents.dashboard import service
        with patch.object(core, 'PORTRAITS', True):
            self.assertEqual(service.brand_mark(), artwork.MATRIX_TRAY)
        with patch.object(core, 'PORTRAITS', False):
            self.assertEqual(service.brand_mark().name, 'tray.png')
        self.assertTrue(service.brand_mark().is_file())

    def test_the_app_icon_is_a_dark_tile_with_the_themes_mark(self):
        from smith_agents import artwork
        for portraits in (True, False):
            icon = artwork.app_icon(portraits)
            self.assertEqual(icon.size, (1024, 1024))
            self.assertEqual(icon.getpixel((0, 0))[3], 0)            # outside the tile
            self.assertEqual(icon.getpixel((150, 512))[:3], (0x16, 0x1a, 0x1e))
        self.assertNotEqual(artwork.app_icon(True).tobytes(), artwork.app_icon(False).tobytes())


if __name__ == '__main__':
    unittest.main()


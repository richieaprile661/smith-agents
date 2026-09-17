from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from smith_agents import hermes_usage as h, core, tucked
from smith_agents.app import SmithAgentsWidget


class HermesUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'state.db'
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('''
                CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL,
                    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
                    reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL);
                INSERT INTO sessions VALUES ('older','Older',1,100,20,80,10,.2,0);
                INSERT INTO sessions VALUES ('newer','Newer',2,0,0,0,0,0,0);
                CREATE TABLE session_model_usage (session_id TEXT, model TEXT,
                    input_tokens INTEGER, output_tokens INTEGER, estimated_cost_usd REAL);
                INSERT INTO session_model_usage VALUES ('older','model-a',90,10,.1);
                INSERT INTO session_model_usage VALUES ('older','model-b',10,10,.1);
            ''')

    def test_read_only_totals_and_model_breakdown(self):
        before = self.path.read_bytes()
        result = h.fetch(self.root)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([s['id'] for s in result['sessions']], ['newer', 'older'])
        session = h.select(result, 'older')['session']
        self.assertEqual(session['tokens'], 120)  # Cache/reasoning and model totals aren't added.
        self.assertEqual(session['estimated_cost_usd'], .2)  # Default actual=0 isn't a bill.
        self.assertEqual([m['tokens'] for m in session['models']], [100,20])
        self.assertIsNone(session['cache_write_tokens'])
        self.assertEqual(h.select(result)['tokens'], 0)

    def test_missing_and_old_schema_preserve_unknown(self):
        with self.assertRaises(h.Unavailable):
            h.fetch(self.root / 'missing')
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('DROP TABLE sessions; DROP TABLE session_model_usage; '
                             'CREATE TABLE sessions(id TEXT, started_at REAL); '
                             "INSERT INTO sessions VALUES ('old',1);")
        session = h.select(h.fetch(self.root))['session']
        self.assertIsNone(session['tokens'])
        self.assertIsNone(session['estimated_cost_usd'])
        self.assertEqual(session['models'], [])

    def test_invalid_numbers_are_unknown(self):
        for value in (-1, float('inf'), float('nan'), '123', True):
            result = h.normalize(dict(input_tokens=value, output_tokens=1, estimated_cost_usd=value))
            self.assertIsNone(result['tokens'])
            self.assertIsNone(result['estimated_cost_usd'])

    def test_selection_prefers_live_then_explicit_and_clamps_model(self):
        result = h.fetch(self.root)
        self.assertEqual(h.select(result, live_ids=['older'])['session']['id'], 'older')
        self.assertEqual(h.select(result, 'newer', ['older'])['session']['id'], 'newer')
        self.assertEqual(h.select(result, 'older', model_index=99)['model']['model'], 'model-b')
        self.assertEqual(h.select(result, 'deleted')['session']['id'], 'newer')

    def test_limits_are_visible(self):
        with patch.object(h, 'SESSION_LIMIT', 1), patch.object(h, 'MODEL_LIMIT', 1):
            result = h.fetch(self.root)
            self.assertTrue(result['limited'])
        with patch.object(h, 'MODEL_LIMIT', 1):
            session = h.select(h.fetch(self.root), 'older')['session']
            self.assertTrue(session['models_limited'])
            self.assertEqual(len(session['models']), 1)

    def widget(self):
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.lock = threading.Lock()
        widget.config = dict(core.DEFAULTS, usage_provider='hermes')
        widget.hermes_data = h.fetch(self.root)
        widget.hermes_session_id = 'older'
        widget.hermes_model_index = 0
        widget._save_config = Mock()
        widget._repaint = Mock()
        return widget

    def test_navigation_updates_header_and_breakdown(self):
        widget = self.widget()
        data = widget._snapshot()[6]
        _, widget.agent_rows = core.render_console([],data,data,[],0,tab='usage',provider='hermes')
        for kind, expected in [('hermes-model','model-b'), ('hermes-session','newer')]:
            box = next(b for b in widget.agent_rows if b[0] == kind and b[-1] == 1)
            widget._on_console_click(SimpleNamespace(x=(box[1]+box[3])/2,y=(box[2]+box[4])/2))
            data = widget._snapshot()[6]
            self.assertEqual(data['model']['model'] if kind == 'hermes-model' else data['session']['id'],expected)
        self.assertEqual(widget._snapshot()[1]['tokens'],0)

    def test_tucked_drawers_open_usage_on_every_edge(self):
        widget = self.widget()
        data = widget._snapshot()[6]
        widget.set_tuck = Mock()
        for edge in tucked.EDGES:
            _, widget.agent_rows, _ = tucked.render([],[],None,0,side=edge,provider='hermes',usage_open=True,usage_data=data)
            box = next(b for b in widget.agent_rows if b[0] == 'peek-hermes-usage')
            widget._on_peek_click(SimpleNamespace(x=(box[1]+box[3])/2,y=(box[2]+box[4])/2))
            widget.set_tuck.assert_called_with(False)
            self.assertEqual(widget.config['console_tab'],'usage')

    def test_poll_failure_retains_reading_and_marks_stale(self):
        widget = self.widget()
        widget.stopping = threading.Event()
        widget.hermes_wake = Mock()
        widget.hermes_wake.wait.side_effect = lambda _: widget.stopping.set()
        with patch.object(h,'fetch',side_effect=h.Unavailable('Database busy')):
            widget._hermes_poll_loop()
        self.assertEqual(widget._snapshot()[6]['tokens'],120)
        self.assertEqual(widget._snapshot()[4],('hermes','Database busy'))
        self.assertIn('Last reading',widget._data_notice())
        widget.hermes_wake.wait.assert_called_once_with(30)

    def test_square_scales_are_explicit_and_do_not_claim_quota(self):
        from smith_agents.hermes_usage_ui import square_readings
        for value, cells, scale in [(0,0,'1k/□'), (15598,15,'1k/□'),
                                    (100000,100,'1k/□'), (140715,14,'10k/□')]:
            reading = square_readings({'session': {'tokens': value}})[0]
            self.assertEqual(reading, ('Tokens', {'pct': cells}, scale))
        reading = square_readings({'session': {'estimated_cost_usd': .31}})[1]
        self.assertEqual(reading, ('Est. cost', {'pct': 31}, '1¢/□'))
        self.assertEqual(square_readings({})[0], ('Tokens', None, '—'))

    def test_tray_accepts_hermes_session_reading_without_breaking_paint_loop(self):
        widget = self.widget()
        widget.tray = Mock()
        widget._tray_cache = None
        with patch('smith_agents.app.render_tray', return_value=object()):
            widget._update_tray(None, False, None)
        self.assertIn('Hermes tokens: 120', widget.tray.title)
        self.assertIn('Estimated cost: $0.200', widget.tray.title)
        self.assertNotIn('Credits', widget.tray.title)
        widget.hermes_data = {'sessions': []}
        widget._update_tray(None, False, None)
        self.assertIn('Hermes tokens: —', widget.tray.title)

"""Saved Hermes usage; cumulative session counters, never context or quotas."""
from contextlib import closing
from pathlib import Path
import math
import sqlite3
import time

from .hermes_sessions import home, _text

COUNTERS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
            'reasoning_tokens', 'api_call_count', 'tool_call_count', 'message_count')
NUMBERS = COUNTERS + ('estimated_cost_usd',)
SESSION_LIMIT = 100
MODEL_LIMIT = 100


class Unavailable(Exception):
    pass


def normalize(row):
    result = dict(row)
    for key in NUMBERS:
        value = result.get(key)
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        valid = valid and math.isfinite(value) and value >= 0
        result[key] = (int(value) if key in COUNTERS else value) if valid else None
    # Cache and reasoning are breakdowns, not additional total tokens.
    pair = [result[key] for key in ('input_tokens', 'output_tokens')]
    result['tokens'] = sum(pair) if all(v is not None for v in pair) else None
    for key in ('title', 'model', 'cwd', 'billing_provider', 'task'):
        result[key] = _text(result.get(key), 250)
    return result


def fetch(root=None):
    path = Path(root) / 'state.db' if root is not None else home() / 'state.db'
    if not path.is_file():
        raise Unavailable('No saved Hermes database')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=.2)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            deadline = time.monotonic()+1
            db.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
            columns = {r[1] for r in db.execute('PRAGMA table_info(sessions)')}
            if not {'id', 'started_at'} <= columns:
                raise Unavailable('Hermes usage schema unavailable')
            fields = ('id', 'title', 'cwd', 'model', 'started_at') + NUMBERS
            query = ','.join('"'+key+'"' if key in columns else 'NULL AS "'+key+'"' for key in fields)
            raw = db.execute('SELECT '+query+' FROM sessions ORDER BY started_at DESC, id DESC LIMIT ?',
                             (SESSION_LIMIT+1,)).fetchall()
            sessions = [normalize(row) for row in raw[:SESSION_LIMIT]]
            model_columns = {r[1] for r in db.execute('PRAGMA table_info(session_model_usage)')}
            fields = ('model', 'billing_provider', 'task') + NUMBERS
            query = ','.join('"'+key+'"' if key in model_columns else 'NULL AS "'+key+'"' for key in fields)
            for session in sessions:
                session['models'] = []
                session['models_limited'] = False
                if {'session_id', 'model'} <= model_columns:
                    models = db.execute('SELECT '+query+' FROM session_model_usage WHERE session_id=? '
                                        'ORDER BY model LIMIT ?', (session['id'], MODEL_LIMIT+1)).fetchall()
                    session['models'] = [normalize(row) for row in models[:MODEL_LIMIT]]
                    session['models_limited'] = len(models) > MODEL_LIMIT
            return {'sessions': sessions, 'limited': len(raw) > SESSION_LIMIT, 'updated': time.time(),
                    'today_cost': _today_cost(db, columns)}
    except sqlite3.Error as exc:
        raise Unavailable('Hermes database unavailable · retrying') from exc


def _today_cost(db, columns):
    """Today's estimated cost. A session that ran across midnight counts
    here in proportion to its replies written today."""
    if 'estimated_cost_usd' not in columns:
        return None
    start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    try:
        rows = _today_rows(db, start)
    except sqlite3.Error:
        return None                 # an optional reading never hides the sessions
    total = 0.0
    for estimate, replies, today in rows:
        if isinstance(estimate, (int, float)) and math.isfinite(estimate) and estimate > 0 and replies:
            total += estimate * today / replies
    return total


def _today_rows(db, start):
    return db.execute("SELECT s.estimated_cost_usd, COUNT(m.rowid), "
                      "SUM(CASE WHEN m.timestamp >= ? THEN 1 ELSE 0 END) FROM sessions s "
                      "JOIN messages m ON m.session_id = s.id AND m.role = 'assistant' "
                      "GROUP BY s.id HAVING MAX(m.timestamp) >= ?", (start, start)).fetchall()


def select(data, identity=None, live_ids=(), model_index=0):
    sessions = data.get('sessions', [])
    selected = next((s for s in sessions if s['id'] == identity), None)
    if selected is None:
        selected = next((s for s in sessions if s['id'] in live_ids), sessions[0] if sessions else None)
    selected = selected or {}
    models = selected.get('models', [])
    index = max(0, min(model_index, len(models)-1))
    return dict(data, provider='hermes', session=selected, tokens=selected.get('tokens'),
                session_index=sessions.index(selected) if selected else 0,
                model_index=index, model=models[index] if models else {})


def cost(value):
    if value is None:
        return '—'
    if 0 < value < .001:
        return '<$0.001'
    return '$%.3f' % value if value < 10 else '$%.2f' % value


def session_name(session):
    cwd = session.get('cwd')
    if cwd and Path(cwd).expanduser() != Path.home():
        return Path(cwd).name or cwd
    return session.get('title') or session.get('model') or 'Saved session'


def demo_data():
    model = normalize(dict(model='deepseek/deepseek-v3.2', billing_provider='OpenRouter', task='chat',
                           input_tokens=128400, output_tokens=9200, cache_read_tokens=98000,
                           cache_write_tokens=0, reasoning_tokens=1400, estimated_cost_usd=.086,
                           api_call_count=12, tool_call_count=8, message_count=24))
    return {'sessions': [dict(model, id='demo-hermes', title='Build the widget', cwd='/demo/smith-agents',
                              models=[model])], 'updated': time.time()}

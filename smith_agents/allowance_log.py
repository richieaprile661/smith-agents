"""A local record of account allowance readings, kept from now on.

The providers report only the current percentage of each limit, never its
history, so the dashboard can show how a limit went down over a day only for
readings the widget saved at the time. The widget already polls both accounts;
each successful reading is appended here as one line of JSON:

    {"at": 1790178930.5, "provider": "Claude", "window": "week",
     "pct": 65.0, "reset": "2026-09-25T16:00:00+00:00"}

Only the percentage, the window and its reset time are kept - no tokens,
credentials or account identity. A reading identical to the last one is saved
again only after HEARTBEAT, which is enough to tell "nothing used" from "the
widget was not running". Lines older than KEEP_DAYS are dropped.
"""
from datetime import datetime
import json
import math
import os
import threading
import time

from . import core

PATH = os.path.join(core.CONFIG_DIR, 'allowance-history.jsonl')
HEARTBEAT = 30 * 60
KEEP_DAYS = 90

_lock = threading.Lock()
_last = {}          # (path, provider, window) -> (pct, reset, at) last written
_pruned = set()     # paths already pruned by this process


def window_of(provider, metric):
    """'5h' or 'week' for the account-wide limits, None for anything else.

    Claude's model-scoped weekly limit and Codex's per-model buckets are left
    out: the dashboard follows the one limit every session draws on."""
    key = str(metric.get('key') or '')
    if provider == 'Claude':
        return {'session': '5h', 'weekly': 'week'}.get(key)
    if provider == 'Codex' and key.startswith('codex:codex:'):
        label = str(metric.get('label') or '')
        if label == '5h':
            return '5h'
        if label in ('1w', '7d'):
            return 'week'
    return None


def _entries(provider, metrics, at):
    for metric in metrics or ():
        window = window_of(provider, metric)
        pct = metric.get('pct')
        if window is None or isinstance(pct, bool) or not isinstance(pct, (int, float)) \
                or not math.isfinite(pct):
            continue
        reset = metric.get('resets_at') if isinstance(metric.get('resets_at'), str) else None
        yield {'at': round(at, 1), 'provider': provider, 'window': window,
               'pct': float(min(100, max(0, pct))), 'reset': reset}


def _prune(path, now):
    cutoff = now - KEEP_DAYS * 86400
    try:
        with open(path, encoding='utf-8') as stream:
            lines = stream.readlines()
    except OSError:
        return
    kept = [line for line in lines if (_parse(line) or {}).get('at', 0) >= cutoff]
    if len(kept) == len(lines):
        return
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as stream:
        stream.writelines(kept)
    os.replace(temporary, path)


def _seed(path):
    """The last saved reading per limit, so a restart does not re-save them."""
    for row in read(path):
        _last[(path, row['provider'], row['window'])] = (row['pct'], row['reset'], row['at'])


def record(provider, metrics, at=None, path=None):
    """Save a fresh account reading. Never raises: a full disk or a read-only
    folder must not stop the widget from polling."""
    path = path or PATH
    at = time.time() if at is None else at
    try:
        with _lock:
            if path not in _pruned:
                _pruned.add(path)
                _prune(path, at)
                _seed(path)
            lines = []
            for entry in _entries(provider, metrics, at):
                key = (path, provider, entry['window'])
                last = _last.get(key)
                if last and last[0] == entry['pct'] and last[1] == entry['reset'] \
                        and at - last[2] < HEARTBEAT:
                    continue
                _last[key] = (entry['pct'], entry['reset'], at)
                lines.append(json.dumps(entry, separators=(',', ':')) + '\n')
            if lines:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'a', encoding='utf-8') as stream:
                    stream.writelines(lines)
    except (OSError, ValueError, TypeError):
        pass


def _parse(line):
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict):
        return None
    at, pct = row.get('at'), row.get('pct')
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (at, pct)):
        return None
    if row.get('provider') not in ('Claude', 'Codex') or row.get('window') not in ('5h', 'week'):
        return None
    reset = row.get('reset') if isinstance(row.get('reset'), str) else None
    return {'at': float(at), 'provider': row['provider'], 'window': row['window'],
            'pct': float(pct), 'reset': reset}


def read(path=None):
    """Every saved reading, oldest first. A damaged line is skipped."""
    try:
        with open(path or PATH, encoding='utf-8') as stream:
            rows = [row for row in map(_parse, stream) if row]
    except OSError:
        return []
    rows.sort(key=lambda row: row['at'])
    return rows


def reset_time(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None

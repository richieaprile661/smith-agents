"""The Nous Portal balance behind Hermes, and real spend measured from it.

Hermes reads the account with its own login, so the reading runs in Hermes's
own Python: the widget never loads, refreshes or sees the Nous token. Only
plan and dollar figures come back. Nous reports balances, not a charge per
request, so real spend is how much the balance drops between readings.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .hermes_sessions import home

READ_TIMEOUT = 25
# Printed by Hermes's interpreter; only these figures leave that process.
_READER = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from agent.billing_usage import build_usage_model
m = build_usage_model(timeout=15)
bar = m.plan_bar
print(json.dumps({"available": bool(m.available), "status": getattr(m, "status", None),
    "plan": m.plan_name, "renews_at": m.renews_at, "renews": m.renews_display,
    "plan_left": m.subscription_remaining_usd, "topup_left": m.topup_remaining_usd,
    "left": m.total_spendable_usd,
    "plan_spent": bar.spent_usd if bar else None, "plan_total": bar.total_usd if bar else None}))
'''


class Unavailable(Exception):
    pass


def install(root=None):
    """Hermes's checkout and interpreter, where the installer puts them."""
    base = Path(root) if root is not None else home()
    code = base / 'hermes-agent'
    python = code / ('venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python')
    return (code, python) if (code / 'agent' / 'billing_usage.py').is_file() and python.is_file() else None


def _money(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value) else None


def read(root=None, run=subprocess.run):
    found = install(root)
    if found is None:
        raise Unavailable('Hermes is not installed here')
    code, python = found
    try:
        result = run([str(python), '-c', _READER, str(code)], capture_output=True, text=True,
                     timeout=READ_TIMEOUT, cwd=str(code), stdin=subprocess.DEVNULL,
                     # Hermes's python.exe is a console program: keep its window hidden.
                     creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unavailable('Nous balance unavailable · retrying') from exc
    lines = [line for line in (result.stdout or '').splitlines() if line.startswith('{')]
    try:
        data = json.loads(lines[-1]) if result.returncode == 0 and lines else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise Unavailable('Nous balance unavailable · retrying')
    if not data.get('available'):
        raise Unavailable('Not signed in to Nous Portal')
    reading = {key: _money(data.get(key)) for key in
               ('plan_left', 'topup_left', 'left', 'plan_spent', 'plan_total')}
    for key in ('status', 'plan', 'renews_at', 'renews'):
        reading[key] = data[key] if isinstance(data.get(key), str) else None
    if reading['left'] is None:
        raise Unavailable('Nous balance unavailable · retrying')
    return reading


class Spend:
    """Real spend while the widget runs, from successive balance readings.

    A rise in the balance is a top-up or a renewal, not negative spend: the
    comparison starts again from it. Pace needs two readings some minutes
    apart, so it stays empty until then."""
    PACE_WINDOW = 3600
    MIN_SPAN = 240

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.points = []            # (time, balance) since the last top-up
        self.spent = 0.0
        self.since = None

    def add(self, left):
        now = self.clock()
        with self.lock:
            if self.since is None:
                self.since = now
            if self.points and left > self.points[-1][1] + 1e-9:
                self.points = []    # topped up or renewed
            elif self.points:
                self.spent += self.points[-1][1] - left
            self.points.append((now, left))
            self.points = [p for p in self.points if now - p[0] <= self.PACE_WINDOW] or self.points[-1:]
            return self.summary(now)

    def summary(self, now=None):
        now = self.clock() if now is None else now
        pace = None
        if len(self.points) > 1 and self.points[-1][0] - self.points[0][0] >= self.MIN_SPAN:
            span = self.points[-1][0] - self.points[0][0]
            pace = max(0.0, (self.points[0][1] - self.points[-1][1]) / span * 3600)
        left = self.points[-1][1] if self.points else None
        hours = left / pace if pace and left is not None else None
        return {'spent': round(self.spent, 4), 'since': self.since, 'pace': pace, 'hours_left': hours}


def money(value):
    """Dollars as the widget prints them."""
    if value is None:
        return '—'
    return '$%.2f' % value


def duration(hours):
    if hours is None:
        return None
    if hours >= 48:
        return '~%d d' % round(hours / 24)
    if hours >= 1:
        return '~%d h' % round(hours)
    return '~%d min' % max(1, round(hours * 60))

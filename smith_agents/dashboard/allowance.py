"""How much of an account limit each session used, from saved readings.

The widget saves each account reading (see `allowance_log`). Between two
readings of the same limit window the percentage rises by some amount; that
rise is split between the token receipts recorded in the same interval, in
proportion to their weight. When one session alone was active, its share is
the whole rise and is exact. When several overlapped, the split is an estimate
and is marked so. A rise with no local receipts at all - claude.ai, another
computer - stays unattributed rather than being pinned on a session.

Days before the first saved reading have no allowance figure at all.
"""
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
import hashlib
import json

from ..allowance_log import reset_time

# Receipt weight, shaped like API pricing: cached input is cheap, output dear.
# Limits are not published per token, so this only decides how an observed
# rise is divided between overlapping sessions; it never creates one.
WEIGHTS = (1.0, 0.1, 5.0)       # fresh input, cached input, output
# Two resets closer than this are the same window; providers report the
# reset time with a little jitter between reads.
SAME_RESET = 600
# Readings further apart than this left the widget unwatched in between.
GAP = 45 * 60
# Session sources as history names them, and the account each draws on.
ACCOUNTS = {'Claude Code': 'Claude', 'Codex': 'Codex'}


def weight(event):
    return sum(w * v for w, v in zip(WEIGHTS, event['tokens']))


def local_day(at):
    return datetime.fromtimestamp(at).strftime('%Y-%m-%d')


def interval(a, b):
    """(start, rise) for the change from reading a to reading b.

    A new window starts from zero at the old reset time, so the whole of b is
    the rise since then. A fall inside one window - a free reset, a provider
    recalculation - is never counted as use."""
    old, new = reset_time(a['reset']), reset_time(b['reset'])
    if old is not None and new is not None and abs(new - old) >= SAME_RESET:
        if new < old:
            return a['at'], 0.0
        return (old if a['at'] < old <= b['at'] else a['at']), b['pct']
    return a['at'], max(0.0, b['pct'] - a['pct'])


def attribute(readings, events):
    """Split every observed rise between the receipts inside it.

    `events` carry 'id', 'session', 'at' (ISO time), 'tokens' and 'provider'
    ('Claude' or 'Codex'). Returns per-receipt shares, per-day account
    figures, and when each account's record begins."""
    receipts = defaultdict(list)
    for event in events:
        try:
            at = datetime.fromisoformat(event['at']).timestamp()
        except (ValueError, TypeError, KeyError):
            continue
        if weight(event) > 0:
            receipts[event['provider']].append((at, event))
    for rows in receipts.values():
        rows.sort(key=lambda item: item[0])

    series = defaultdict(list)
    for row in readings:
        series[(row['provider'], row['window'])].append(row)

    shares, since = {}, {}
    days = defaultdict(lambda: defaultdict(dict))

    def figures(day, provider, window):
        return days[day][provider].setdefault(window, {
            'used': 0.0, 'unattributed': 0.0, 'start': None, 'end': None,
            'first': None, 'reset': False, 'gap': False})

    for (provider, window), rows in series.items():
        rows.sort(key=lambda row: row['at'])
        since[provider] = min(since.get(provider, rows[0]['at']), rows[0]['at'])
        found = receipts.get(provider, [])
        times = [at for at, _ in found]
        previous = None
        for row in rows:
            today = figures(local_day(row['at']), provider, window)
            if today['start'] is None:
                today['start'], today['first'] = row['pct'], row['at']
            today['end'] = row['pct']
            if previous is None:
                previous = row
                continue
            start, rise = interval(previous, row)
            if start != previous['at'] or row['pct'] < previous['pct']:
                today['reset'] = True
            if row['at'] - previous['at'] > GAP:
                today['gap'] = True
            previous = row
            if rise <= 0:
                continue
            inside = found[bisect_right(times, start):bisect_right(times, row['at'])]
            if not inside:
                today['unattributed'] += rise
                continue
            total = sum(weight(event) for _, event in inside)
            estimated = len({event['session'] for _, event in inside}) > 1
            for at, event in inside:
                part = rise * weight(event) / total
                share = shares.setdefault(event['id'], {}).setdefault(window, {'pct': 0.0, 'est': False})
                share['pct'] += part
                share['est'] = share['est'] or estimated
                figures(local_day(at), provider, window)['used'] += part

    for share in shares.values():
        for value in share.values():
            value['pct'] = round(value['pct'], 4)
    for accounts in days.values():
        for windows in accounts.values():
            for value in windows.values():
                value['used'] = round(value['used'] + value['unattributed'], 4)
                value['unattributed'] = round(value['unattributed'], 4)
    result = {'shares': shares, 'days': {d: dict(a) for d, a in days.items()},
              'since': {p: local_day(at) for p, at in since.items()},
              'sinceAt': since}
    result['version'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:12]
    return result

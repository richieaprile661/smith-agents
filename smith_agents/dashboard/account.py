"""Account readings for the dashboard. Browser responses carry no credentials.

The widget already polls both providers, so when it is running the dashboard
reads *its* results rather than opening a second poller against the same
accounts. Only a dashboard started without a widget falls back to the shared
collectors, and then at most one request per provider every 30 seconds, with
the provider's own backoff on a rate limit.

Every reading is account-wide. It includes other sessions, other projects and
other devices signed in to the same account, so nothing here attributes usage
to one session.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import math
import threading
import time

from .. import codex_usage, core, hermes_account, hermes_usage

# A widget reading older than this is shown as the last available one rather
# than as the current state of the account.
STALE_AFTER = 600
# The floor between two direct account requests when no widget is sharing its
# readings, and the floor after a provider asks us to slow down.
MIN_INTERVAL = 30
RATELIMIT_BACKOFF = 120
HERMES_INTERVAL = 120

BUSY_MESSAGE = 'Usage endpoint is busy; retrying later.'
UNAVAILABLE_MESSAGE = 'Unable to read account usage. Check sign-in in the provider app.'


def read_claude():
    """The widget's own cache when it is fresh, otherwise one direct read."""
    payload, _, fetched = core.load_cache()
    if not payload or not 0 <= time.time() - fetched < 45:
        payload, _ = core.fetch_usage()
        fetched = time.time()
    return core.build_metrics(payload), None, fetched


def read_codex():
    client = codex_usage.AccountClient(timeout=12)
    try:
        client.request('initialize', {'clientInfo': {'name': 'smith_dashboard', 'version': '1'}})
        client.send({'method': 'initialized', 'params': {}})
        payload = client.request('account/rateLimits/read')
        return codex_usage.build_metrics(payload), codex_usage.build_credits(payload), time.time()
    finally:
        client.close()


_HERMES_SPEND = hermes_account.Spend()


def read_hermes():
    """The Nous balance behind Hermes, and Hermes's own estimate for today."""
    reading = hermes_account.read()
    try:
        today = hermes_usage.fetch().get('today_cost')
    except hermes_usage.Unavailable:
        today = None
    return [], None, time.time(), dict(reading, spend=_HERMES_SPEND.add(reading['left']), today=today)


def collectors():
    """Resolved per call, so a reader can be substituted for a test. Hermes
    is offered only where it is installed."""
    readers = [('Claude', read_claude), ('Codex', read_codex)]
    if hermes_account.install() is not None:
        readers.append(('Hermes', read_hermes))
    return tuple(readers)


class Readings:
    """Sanitized limits plus the change observed since this process started.

    The baseline is in memory on purpose: it says what changed while the
    dashboard watched, and it restarts whenever the comparison would stop
    being meaningful - a new reset window, a decrease, or a restart. It is not
    a persistent history of allowance over time, and cannot be read as one.
    """

    def __init__(self, source=None):
        # ``source`` is the running widget's own readings, when there is one.
        self.source = source
        self.lock = threading.Lock()
        self.baselines = {}
        self.state = {}

    # -- normalizing -------------------------------------------------------
    def limits(self, provider, metrics, observed):
        result = []
        for metric in metrics or ():
            pct = metric.get('pct')
            if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not math.isfinite(pct):
                continue
            pct = min(100, max(0, pct))
            reset = metric.get('resets_at')
            try:
                reset_time = datetime.fromisoformat(reset.replace('Z', '+00:00')).timestamp() if reset else None
            except (ValueError, TypeError, AttributeError):
                reset, reset_time = None, None
            expired = reset_time is not None and reset_time <= observed
            key = (provider, metric.get('key'))
            baseline = self.baselines.get(key)
            # Never compare across resets or silently treat a decrease as use.
            if not baseline or baseline['reset'] != reset or pct < baseline['pct'] or expired:
                baseline = {'reset': reset, 'pct': pct, 'at': observed}
                self.baselines[key] = baseline
            result.append({'key': metric.get('key'), 'label': metric.get('detail') or metric.get('label'),
                           'used': pct, 'remaining': 100 - pct, 'resets_at': reset,
                           'expired': expired, 'baseline_used': baseline['pct'],
                           'baseline_at': baseline['at'], 'change': round(pct - baseline['pct'], 3),
                           'elapsed_seconds': max(0, observed - baseline['at'])})
        return result

    @staticmethod
    def credits(value):
        """Only the facts the page prints: a balance, whether it is in use, and a
        free reset Codex has granted. Nothing else from the payload."""
        if not isinstance(value, dict):
            return None
        text = value.get('text') if isinstance(value.get('text'), str) and value.get('text') else None
        reset = value.get('reset') if isinstance(value.get('reset'), dict) else None
        count = reset.get('count') if reset else None
        reset = ({'count': count, 'expires_at': reset.get('expires_at')
                  if isinstance(reset.get('expires_at'), (int, float)) else None}
                 if isinstance(count, int) and count > 0 else None)
        if text is None and reset is None:
            return None
        return {'text': text, 'on_credits': bool(value.get('on_credits')), 'reset': reset}

    @staticmethod
    def balance(value):
        """A money reading: dollar figures, plan words and dates only."""
        if not isinstance(value, dict):
            return None
        number = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
            and math.isfinite(v) else None
        result = {k: number(value.get(k)) for k in
                  ('left', 'plan_left', 'topup_left', 'plan_spent', 'plan_total', 'today')}
        if result['left'] is None:
            return None
        for key in ('plan', 'status', 'renews', 'renews_at'):
            result[key] = value[key] if isinstance(value.get(key), str) else None
        spend = value.get('spend') if isinstance(value.get('spend'), dict) else {}
        result['spend'] = {k: number(spend.get(k)) for k in ('spent', 'since', 'pace', 'hours_left')}
        return result

    # -- collecting --------------------------------------------------------
    def from_widget(self):
        """The widget's last readings, with no account request of our own."""
        try:
            readings = self.source()
        except Exception:
            return None
        if not isinstance(readings, list):
            return None
        now = time.time()
        providers = []
        for reading in readings:
            name = reading.get('provider')
            observed = reading.get('observed_at') or 0
            limits = self.limits(name, reading.get('metrics'), observed or now)
            balance = self.balance(reading.get('balance'))
            error = reading.get('error') or (None if limits or balance else UNAVAILABLE_MESSAGE)
            stale = bool(error) or not observed or now - observed > STALE_AFTER
            providers.append({'provider': name, 'limits': limits, 'balance': balance,
                              'credits': self.credits(reading.get('credits')),
                              'observed_at': observed or None, 'stale': stale,
                              'error': error, 'source': 'widget'})
        return providers or None

    def collect(self, provider, reader):
        """One provider, read directly, no more often than the floor allows."""
        previous = self.state.get(provider, {})
        now = time.time()
        if previous.get('retry_at', 0) > now:
            return previous
        try:
            metrics, credits, observed, *extra = reader()
            limits = self.limits(provider, metrics, observed)
            balance = self.balance(extra[0]) if extra else None
            if not limits and not balance:
                raise ValueError('No limits')
            value = {'provider': provider, 'limits': limits, 'balance': balance,
                     'credits': self.credits(credits),
                     'observed_at': observed, 'stale': False, 'error': None,
                     'source': 'collector',
                     # The Nous balance is read at the widget's own two-minute pace.
                     'retry_at': now + (HERMES_INTERVAL if provider == 'Hermes' else MIN_INTERVAL)}
        except Exception as error:
            rate_limited = isinstance(error, core.UsageError) and error.kind == 'ratelimit'
            delay = (max(RATELIMIT_BACKOFF, getattr(error, 'retry_after', 0) or 0)
                     if rate_limited else MIN_INTERVAL)
            value = {**previous, 'provider': provider, 'limits': previous.get('limits', []),
                     'stale': True, 'source': 'collector',
                     'error': BUSY_MESSAGE if rate_limited else UNAVAILABLE_MESSAGE,
                     'retry_at': now + delay}
        self.state[provider] = value
        return value

    def snapshot(self):
        with self.lock:
            providers = self.from_widget() if self.source else None
            if providers is None:
                readers = collectors()
                with ThreadPoolExecutor(max_workers=len(readers)) as pool:
                    futures = [pool.submit(self.collect, name, reader) for name, reader in readers]
                    providers = [future.result() for future in futures]
            # retry_at is scheduling, not a reading; it never leaves the process.
            return {'providers': [{k: v for k, v in p.items() if k != 'retry_at'} for p in providers],
                    'generated': datetime.now(timezone.utc).isoformat()}

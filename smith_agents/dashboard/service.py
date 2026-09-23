"""The widget's own dashboard server: loopback, lazy, and started once.

Nothing runs until someone opens the dashboard. The first open binds a
listener on 127.0.0.1 with an OS-assigned port and mints a key for that run;
every later open reuses them. Quitting the widget closes the listener and
releases the port.

Only the page's own files are served, from a fixed table - there is no route
that turns a request into a filesystem path. The data routes additionally
require the run's key, so another local program that happens to guess the port
cannot read this machine's project history.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
import json
import secrets
import threading
import time

from .. import allowance_log
from . import allowance, history
from .account import Readings

WEB = Path(__file__).resolve().parent / 'web'
ASSETS = {
    '/': (WEB / 'index.html', 'text/html'),
    '/session.js': (WEB / 'session.js', 'text/javascript'),
    '/app.js': (WEB / 'app.js', 'text/javascript'),
    '/style.css': (WEB / 'style.css', 'text/css'),
    '/plan-usage.js': (WEB / 'plan-usage.js', 'text/javascript'),
    '/plan-usage.css': (WEB / 'plan-usage.css', 'text/css'),
    '/logo.png': (lambda: brand_mark(), 'image/png'),
    # The widget's own glowing provider marks, drawn by the same code.
    '/source/claude.png': (lambda: source_mark('claude'), 'image/png'),
    '/source/codex.png': (lambda: source_mark('codex'), 'image/png'),
    '/source/hermes.png': (lambda: source_mark('hermes'), 'image/png'),
}


def source_mark(provider):
    """A provider's logo with its coloured halo, as the widget shows the
    selected account, encoded once per run."""
    if provider not in _MARKS:
        import io
        from .. import core
        stream = io.BytesIO()
        core.provider_lamp(provider, True, 18).save(stream, format='PNG')
        _MARKS[provider] = stream.getvalue()
    return _MARKS[provider]


_MARKS = {}


def brand_mark():
    """The header logo follows the theme: Matrix, the default, wears Smith's
    face; Claude and E-ink keep the line man."""
    from .. import artwork, core
    if core.PORTRAITS:
        return artwork.MATRIX_TRAY
    return Path(__file__).resolve().parents[1] / 'assets/approved/tray.png'
# One shared reading of the project list, so several open windows and the
# 30-second page refresh do not each re-scan the local transcripts.
SNAPSHOT_TTL = 30
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; "
       "object-src 'none'")


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'SmithDashboard'
    sys_version = ''

    @property
    def service(self):
        return self.server.service

    def do_GET(self):
        # Reject cross-origin requests and DNS-rebinding hostnames: only the
        # loopback names this listener was opened under are answered.
        port = self.server.server_port
        if self.headers.get('Host') not in ('127.0.0.1:%d' % port, 'localhost:%d' % port):
            self.fail(403)
            return
        path = urlsplit(self.path).path
        if path.startswith('/api/'):
            self.serve_api(path)
            return
        asset = ASSETS.get(path)
        if asset is None:
            self.fail(404)
            return
        try:
            source = asset[0]() if callable(asset[0]) else asset[0]
            body = source if isinstance(source, bytes) else source.read_bytes()
        except OSError:
            self.fail(404)
            return
        self.reply(body, asset[1])

    def serve_api(self, path):
        # The key is minted per run and handed over in the URL fragment, which
        # a browser never sends to a server. Only the page that was opened
        # from this widget can ask for local session data.
        if not secrets.compare_digest(self.headers.get('X-Smith-Key', ''), self.service.token):
            self.reply(b'{"error":"Reopen the dashboard from the widget."}', 'application/json', 403)
            return
        try:
            if path == '/api/allowance':
                self.reply(self.json(self.service.allowance()), 'application/json')
            elif path == '/api/projects':
                self.reply(self.json(self.service.projects()), 'application/json')
            elif path == '/api/snapshot':
                wanted = parse_qs(urlsplit(self.path).query).get('project', [''])[0]
                self.reply(self.json(self.service.snapshot(wanted)), 'application/json')
            else:
                self.fail(404)
        except Exception:
            self.reply(b'{"error":"Local history could not be read. Try refreshing."}',
                       'application/json', 500)

    @staticmethod
    def json(value):
        return json.dumps(value).encode()

    def fail(self, status):
        self.reply(b'', 'text/plain', status)

    def reply(self, data, content_type, status=200):
        try:
            self.send_response(status)
            if content_type.startswith('text/') or content_type.endswith('json'):
                content_type += '; charset=utf-8'
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', CSP)
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass  # the browser closed the tab mid-response

    def log_message(self, *args):
        pass  # request paths would name local projects in a shared log


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # A closed tab leaves a socket behind; never let one hold up the quit.
    block_on_close = False


class DashboardService:
    """Owns the listener. Start is idempotent; stop releases the port."""

    def __init__(self):
        self.lock = threading.RLock()
        # Reading the local transcripts is the slow part. It runs under its own
        # lock so that several browser windows share one scan, and so that
        # opening the dashboard never waits behind one.
        self.scan_lock = threading.RLock()
        self.server = None
        self.thread = None
        self.token = ''
        self.readings = Readings()
        self.named = {}          # ids the widget vouched for: id -> path
        self.cache = self._empty()

    # -- lifecycle ---------------------------------------------------------
    def share_account_readings(self, source):
        """Read the running widget's own account results instead of polling."""
        self.readings = Readings(source)

    @property
    def running(self):
        return self.server is not None and self.thread is not None and self.thread.is_alive()

    def start(self):
        """Bind once and return (port, key). Safe to call from any thread.

        The socket is listening before this returns, so a caller can open the
        browser immediately without polling for readiness.
        """
        with self.lock:
            if self.running:
                return self.server.server_port, self.token
            self.stop()          # clear a listener whose thread has died
            self.token = secrets.token_urlsafe(32)
            server = _Server(('127.0.0.1', 0), Handler)
            server.service = self
            thread = threading.Thread(target=server.serve_forever, name='smith-dashboard',
                                      daemon=True)
            thread.start()
            self.server, self.thread = server, thread
            return server.server_port, self.token

    def stop(self):
        with self.lock:
            server, thread = self.server, self.thread
            self.server = self.thread = None
            self.token = ''
            self.cache = self._empty()
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    def url(self, project=None):
        """The address to open. The key rides in the fragment, which stays in
        the browser: it never reaches this server's request line."""
        port, token = self.start()
        fragment = {'k': token}
        if project:
            fragment['project'] = project
        return 'http://127.0.0.1:%d/#%s' % (port, urlencode(fragment))

    # -- data --------------------------------------------------------------
    def remember(self, path):
        """Vouch for a workspace the widget knows about, whether or not local
        history mentions it yet, and return its opaque id."""
        identity = history.project_id(path)
        with self.lock:
            self.named[identity] = str(path)
        return identity

    @staticmethod
    def _empty():
        return {'at': 0.0, 'projects': [], 'overview': None, 'snapshots': {}, 'allowance': None}

    def _projects(self):
        """Every workspace local history mentions, at most 30 seconds old."""
        with self.scan_lock:
            with self.lock:
                if time.monotonic() - self.cache['at'] < SNAPSHOT_TTL and self.cache['projects']:
                    return self.cache['projects']
                named = dict(self.named)
            projects = history.discover_projects()
            known = {p['id'] for p in projects}
            for identity, path in named.items():
                if identity not in known and Path(path).is_dir():
                    projects.append({'id': identity, 'name': Path(path).name, 'path': path,
                                     'sessions': 0, 'sources': {}})
            with self.lock:
                self.cache = {'at': time.monotonic(), 'projects': projects,
                              'overview': None, 'snapshots': {}, 'allowance': None}
            return projects

    def _overview(self, projects):
        """Per-project daily totals, read once per project-list refresh."""
        with self.scan_lock:
            with self.lock:
                if self.cache['overview'] is not None:
                    return self.cache['overview']
            rows = [history.overview_row(p, self._project_snapshot(p)) for p in projects]
            with self.lock:
                if self.cache['projects'] is projects:
                    self.cache['overview'] = rows
            return rows

    def _chosen(self, wanted):
        """Only a discovered project, or one the widget vouched for, is ever
        opened - never a path that arrived in a request. An id the browser no
        longer recognises falls back to the most active workspace."""
        projects = self._projects()
        if not projects:
            return None
        return next((p for p in projects if p['id'] == wanted), projects[0])

    def projects(self):
        return [{k: v for k, v in p.items() if k != 'path'} for p in self._projects()]

    def _project_snapshot(self, chosen):
        """One project's history, read once per project-list refresh."""
        with self.lock:
            cached = self.cache['snapshots'].get(chosen['id'])
        if cached is None:
            with self.scan_lock:
                with self.lock:
                    cached = self.cache['snapshots'].get(chosen['id'])
                if cached is None:
                    cached = history.snapshot(chosen['path'])
                    # The disambiguated name from the project list, so two
                    # workspaces with the same folder name stay distinct.
                    cached['project'] = chosen['name']
                    with self.lock:
                        self.cache['snapshots'][chosen['id']] = cached
        return cached

    def _allowance(self, projects):
        """Saved account readings split across every project's receipts.

        A limit is shared by all of an account's sessions, whatever folder
        they ran in, so the split looks at every project, not only the one
        on screen."""
        with self.lock:
            cached = self.cache['allowance']
        if cached is not None:
            return cached
        events, seen = [], set()
        for project in projects:
            data = self._project_snapshot(project)
            accounts = {s['id']: allowance.ACCOUNTS.get(s['provider']) for s in data['sessions']}
            for event in data['events']:
                account = accounts.get(event['session'])
                if account and event['id'] not in seen:
                    seen.add(event['id'])
                    events.append(dict(event, provider=account))
        result = allowance.attribute(allowance_log.read(), events)
        with self.lock:
            if self.cache['projects'] is projects:
                self.cache['allowance'] = result
        return result

    def snapshot(self, wanted):
        chosen = self._chosen(wanted)
        if chosen is None:
            return {'error': 'No local Codex, Claude Code or Hermes history was found on this computer.',
                    'project': None, 'projects': [], 'sessions': [], 'events': [], 'actions': [],
                    'commits': [], 'coverage': {}, 'timezone': history.zone_label()}
        cached = self._project_snapshot(chosen)
        projects = self._projects()
        shares = self._allowance(projects)
        result = dict(cached, projects=self._overview(projects),
                      allowance={'days': shares['days'], 'since': shares['since']},
                      version=cached.get('version', '') + shares['version'])
        result['events'] = [dict(e, allow=shares['shares'][e['id']]) if e['id'] in shares['shares'] else e
                            for e in cached['events']]
        return result

    def allowance(self):
        return self.readings.snapshot()


SERVICE = DashboardService()

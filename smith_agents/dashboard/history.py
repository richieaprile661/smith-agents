"""Read-only project snapshot for the design trial. Never reads credentials.

Codex receipt input includes cached input; reasoning is already part of output.
Prefer per-response receipts, never add them to cumulative counters. The fallback
uses counter differences and marks its first observation as undated coverage.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile

FILE_CACHE = {}
# Scratch checkouts are not projects anyone wants a build story for.
SCRATCH = tuple(dict.fromkeys(
    ('/tmp', '/private/tmp', '/var/folders', os.path.realpath(tempfile.gettempdir()),
     tempfile.gettempdir())))


def zone_label():
    """The machine's own time zone, named as the platform names it."""
    return datetime.now().astimezone().tzname() or 'local time'


def project_id(path):
    """An opaque, stable handle for a workspace.

    The browser never receives or sends a filesystem path, so a crafted request
    cannot point the reader at a directory the local history never mentioned."""
    return hashlib.sha256(os.fspath(path).encode('utf-8', 'surrogateescape')).hexdigest()[:16]


def workspace(value):
    """A recorded cwd as a plain path: newer logs write it as a file:// URL."""
    if not isinstance(value, str):
        return None
    if value.startswith('file://'):
        value = value[len('file://'):]
    return value or None


def codex_home(value=None):
    return Path(value or os.environ.get('CODEX_HOME') or Path.home() / '.codex')


def claude_projects(value=None):
    """Claude Code keeps one folder per workspace under its config directory."""
    return Path(value or os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects'


CLAUDE_TOOLS = {'Bash': 'Command calls', 'Edit': 'File-edit calls', 'Write': 'File-edit calls',
                'MultiEdit': 'File-edit calls', 'NotebookEdit': 'File-edit calls',
                'Read': 'File reads', 'Grep': 'File reads', 'Glob': 'File reads',
                'Agent': 'Helper launches', 'AskUserQuestion': 'Clarification calls',
                'WebFetch': 'Web lookups', 'WebSearch': 'Web lookups'}


def claude_parts(usage):
    """Claude Code splits input three ways. Cache reads are reused material, so
    they are the cached share; tokens written to the cache are new input."""
    if not isinstance(usage, dict):
        return None
    values = [number(usage.get(k)) for k in ('input_tokens', 'cache_creation_input_tokens',
                                             'cache_read_input_tokens', 'output_tokens')]
    if None in values:
        return None
    return [values[0] + values[1], values[2], values[3]]


def clean_title(text, limit=90):
    """A session's own words as a title: one line, no context wrapper, cut short."""
    if not isinstance(text, str):
        return None
    text = re.sub(r'\s+', ' ', text).strip()
    if not text or text.startswith('<'):
        return None
    text = text[0].upper() + text[1:]
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def first_cwd(path, limit=40):
    """The workspace a Claude Code transcript belongs to, from its first records."""
    try:
        with path.open(encoding='utf-8') as stream:
            for _ in range(limit):
                line = stream.readline()
                if not line:
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and isinstance(record.get('cwd'), str):
                    return record['cwd']
    except OSError:
        pass
    return None


def parse_claude_transcript(path):
    """A Claude Code session or subagent log in the rollout shape ``normalize_sessions``
    reads. Streaming writes one assistant record per content block, all carrying the
    message's usage, so a receipt is the message id and counts once."""
    stat = path.stat()
    signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if path in FILE_CACHE and FILE_CACHE[path][0] == signature:
        return FILE_CACHE[path][1]
    meta, receipts, actions = {}, [], []
    seen, invalid, model = set(), 0, None
    ai_title = first_request = None
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                invalid += 1
                continue
            if not isinstance(record, dict):
                continue
            if record.get('type') == 'ai-title' and not ai_title:
                ai_title = clean_title(record.get('aiTitle'))
            if record.get('type') == 'user' and not first_request and not record.get('isMeta'):
                content = (record.get('message') or {}).get('content')
                if isinstance(content, list):
                    content = ' '.join(c.get('text', '') for c in content
                                       if isinstance(c, dict) and c.get('type') == 'text')
                first_request = clean_title(content)
            if 'cwd' not in meta and isinstance(record.get('cwd'), str):
                meta['cwd'] = record['cwd']
                meta['timestamp'] = record.get('timestamp')
                meta['id'] = record.get('agentId') or record.get('sessionId') or path.stem
                if record.get('agentId') and record.get('sessionId'):
                    meta['parent_thread_id'] = record['sessionId']
            if record.get('type') != 'assistant' or record.get('isApiErrorMessage'):
                continue
            message = record.get('message')
            if not isinstance(message, dict) or message.get('model') == '<synthetic>':
                continue
            model = message.get('model') or model
            at = stamp(record.get('timestamp'))
            identity = message.get('id')
            if identity and identity not in seen and at:
                values = claude_parts(message.get('usage'))
                if values is None:
                    invalid += 1
                else:
                    seen.add(identity)
                    receipts.append({'id': identity, 'owner': None, 'at': at, 'tokens': values})
            for item in message.get('content') or []:
                if isinstance(item, dict) and item.get('type') == 'tool_use' and at:
                    actions.append({'at': at, 'id': item.get('id'),
                                    'label': CLAUDE_TOOLS.get(item.get('name'), 'Other tool calls')})
    meta['provider'] = 'Claude Code'
    meta.setdefault('id', path.stem)
    title = ai_title or first_request
    described = path.with_suffix('.meta.json')       # a subagent's task, written by Claude Code
    if described.is_file():
        try:
            with described.open(encoding='utf-8') as handle:
                title = clean_title(json.load(handle).get('description')) or title
        except (OSError, ValueError):
            pass
    data = {'meta': meta, 'receipts': receipts, 'counters': [], 'actions': actions,
            'model': model, 'invalid': invalid, 'title': title}
    FILE_CACHE[path] = (signature, data)
    return data


def claude_logs(project, projects_dir=None):
    """Every Claude Code session log for a workspace, and their subagent logs."""
    logs = []
    root = claude_projects(projects_dir)
    if not root.is_dir():
        return logs
    for folder in root.iterdir():
        for path in folder.glob('*.jsonl'):
            if workspace(first_cwd(path)) == str(project):
                logs.append(path)
                logs.extend(sorted((folder / path.stem / 'subagents').glob('*.jsonl')))
    return logs


def discover_projects(home=None):
    """Every workspace the local Codex history mentions, most sessions first.
    Only directories that still exist count, and scratch folders are left out."""
    home = codex_home(home)
    logs = {}          # log file -> workspace; a session indexed twice counts once
    for database in sorted(home.glob('state_*.sqlite'), reverse=True):
        try:
            with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.5) as db:
                for cwd, path in db.execute('SELECT cwd, rollout_path FROM threads'):
                    if workspace(cwd) and path:
                        logs[Path(path)] = workspace(cwd)
            break
        except sqlite3.Error:
            continue
    for folder in ('sessions', 'archived_sessions'):
        for path in (home / folder).rglob('*.jsonl'):
            if path in logs:
                continue
            try:
                with path.open() as stream:
                    for _ in range(8):
                        line = stream.readline()
                        if not line:
                            break
                        record = json.loads(line)
                        if record.get('type') == 'session_meta':
                            cwd = workspace(record.get('payload', {}).get('cwd'))
                            if cwd:
                                logs[path] = cwd
                            break
            except (OSError, ValueError):
                continue
    codex = Counter(logs.values())
    claude = Counter()
    root = claude_projects()
    if root.is_dir():
        for folder in root.iterdir():
            for path in folder.glob('*.jsonl'):
                cwd = workspace(first_cwd(path))
                if cwd:
                    claude[cwd] += 1
    counts = codex + claude
    projects = []
    for cwd, sessions in counts.most_common():
        path = Path(cwd)
        if not path.is_dir() or cwd.startswith(SCRATCH):
            continue
        projects.append({'id': project_id(cwd), 'name': path.name, 'path': cwd,
                         'sessions': sessions,
                         'sources': {'Codex': codex[cwd], 'Claude Code': claude[cwd]}})
    names = Counter(p['name'] for p in projects)
    for project in projects:
        if names[project['name']] > 1:
            project['name'] = Path(project['path']).parent.name + '/' + project['name']
    return projects


def number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parts(usage):
    if not isinstance(usage, dict):
        return None
    values = [number(usage.get(k)) for k in ('input_tokens', 'cached_input_tokens', 'output_tokens')]
    if None in values or values[1] > values[0]:
        return None
    return [values[0] - values[1], values[1], values[2]]


def stamp(value):
    """A recorded instant as a local wall-clock time.

    Days are grouped the way the person reading them lived them, so the reader
    converts to whatever zone this machine is set to rather than a fixed one.
    ``astimezone()`` with no argument re-reads the offset per value, so a
    history that spans a daylight-saving change still lands on the right day."""
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().isoformat(timespec='seconds')
    except (ValueError, TypeError, AttributeError):
        return None


def tool_label(name, arguments):
    """Describe the recorded call, never export its arguments or output."""
    text = arguments if isinstance(arguments, str) else ''
    if 'image_gen' in name or 'image_gen__imagegen' in text:
        return 'Image-generation calls'
    if 'apply_patch' in name or 'tools.apply_patch(' in text:
        return 'File-edit calls'
    if name in ('exec_command', 'functions.exec_command') or 'tools.exec_command(' in text:
        return 'Command calls'
    if 'view_image' in name or 'tools.view_image(' in text:
        return 'Image inspections'
    if name in ('spawn_agent', 'collaboration.spawn_agent'):
        return 'Helper launches'
    if 'request_user_input' in name:
        return 'Clarification calls'
    return 'Other tool calls'


def parse_rollout(path):
    stat = path.stat()
    signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if path in FILE_CACHE and FILE_CACHE[path][0] == signature:
        return FILE_CACHE[path][1]
    meta, receipts, counters, actions = {}, [], [], []
    invalid = 0
    model = title = None
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                invalid += 1
                continue
            if not isinstance(record, dict):
                continue
            payload = record.get('payload')
            if not isinstance(payload, dict):
                continue
            kind, at = record.get('type'), stamp(record.get('timestamp'))
            if kind == 'session_meta':
                meta.update(payload)
            elif kind == 'event_msg' and payload.get('type') == 'user_message' and not title:
                title = clean_title(payload.get('message'))
            elif kind == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user' and not title:
                # The user's own turns; the first is the environment block, which clean_title drops.
                content = payload.get('content')
                if isinstance(content, list):
                    content = ' '.join(c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') == 'input_text')
                title = clean_title(content)
            elif kind == 'turn_context':
                model = payload.get('model') or model
            elif kind == 'token_usage_record':
                values = parts(payload.get('usage'))
                if values is not None and at and payload.get('response_id'):
                    receipts.append({'id': payload['response_id'], 'owner': payload.get('thread_id'),
                                     'at': at, 'tokens': values})
                else:
                    invalid += 1
            elif kind == 'event_msg' and payload.get('type') == 'token_count':
                info = payload.get('info')
                values = parts(info.get('total_token_usage')) if isinstance(info, dict) else None
                if values is not None and at:
                    counters.append((at, values))
            elif kind == 'response_item' and payload.get('type') in ('function_call', 'custom_tool_call') and at:
                actions.append({'at': at, 'id': payload.get('call_id'),
                                'label': tool_label(payload.get('name', ''), payload.get('arguments', payload.get('input')))})
    data = {'meta': meta, 'receipts': receipts, 'counters': counters, 'actions': actions,
            'model': model, 'invalid': invalid, 'title': title}
    FILE_CACHE[path] = (signature, data)
    return data


def normalize_sessions(parsed, project):
    sessions, events, actions = [], [], []
    seen_responses, seen_calls, seen_sessions = set(), set(), set()
    coverage = Counter()
    for data in parsed:
        meta = data['meta']
        if workspace(meta.get('cwd')) != str(project):
            continue
        source = meta.get('source')
        sub = source.get('subagent', {}) if isinstance(source, dict) else {}
        if isinstance(sub, dict) and sub.get('other') == 'guardian':
            coverage['excludedInternalSessions'] += 1
            continue
        identity = meta.get('id')
        if not identity or identity in seen_sessions:
            continue
        seen_sessions.add(identity)
        spawn = sub.get('thread_spawn', {}) if isinstance(sub, dict) else {}
        parent = meta.get('parent_thread_id') or (spawn.get('parent_thread_id') if isinstance(spawn, dict) else None)
        title = data.get('title')
        if isinstance(spawn, dict) and spawn.get('agent_path'):
            # Codex names a helper by the task it was spawned for.
            title = clean_title(Path(spawn['agent_path']).name.replace('_', ' ').replace('-', ' '))
        session = {'id': identity, 'parent': parent, 'model': data['model'],
                   'provider': meta.get('provider', 'Codex'), 'title': title,
                   'nickname': spawn.get('agent_nickname') if isinstance(spawn, dict) else None,
                   'started': stamp(meta.get('timestamp')), 'helper': bool(parent), 'method': 'response receipts'}
        own = [r for r in data['receipts'] if r['owner'] in (None, identity)]
        if own:
            for receipt in own:
                if receipt['id'] in seen_responses:
                    coverage['duplicateResponses'] += 1
                    continue
                seen_responses.add(receipt['id'])
                events.append({k: receipt[k] for k in ('id', 'at', 'tokens')} | {'session': identity})
        else:
            # Never assign the first cumulative count to the hour we found it.
            session['method'] = 'counter differences'
            previous = None
            for i, (at, current) in enumerate(data['counters']):
                if previous is None:
                    coverage['undatedTokens'] += sum(current)
                elif any(a < b for a, b in zip(current, previous)):
                    coverage['counterResets'] += 1
                    coverage['undatedTokens'] += sum(current)
                else:
                    delta = [a - b for a, b in zip(current, previous)]
                    if sum(delta):
                        events.append({'id': f'{identity}:counter:{i}', 'session': identity, 'at': at, 'tokens': delta})
                previous = current
        for action in data['actions']:
            key = (identity, action['id'])
            if action['id'] and key not in seen_calls:
                seen_calls.add(key)
                actions.append({'at': action['at'], 'label': action['label'], 'session': identity})
        if data['counters']:
            session['lastCounterTotal'] = sum(data['counters'][-1][1])
        coverage['unreadableRecords'] += data['invalid']
        sessions.append(session)
    return sessions, events, actions, dict(coverage)


def git_milestones(project):
    try:
        result = subprocess.run(['git', 'log', '--all', '--format=%H%x00%cI%x00%s'], cwd=project,
                                capture_output=True, text=True, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError):
        return []          # not a repository, or git is unavailable
    commits = []
    for line in result.stdout.splitlines():
        fields = line.split('\0', 2)
        if len(fields) == 3 and (at := stamp(fields[1])):
            commits.append({'id': fields[0][:10], 'at': at, 'title': fields[2][:180]})
    return commits


def snapshot(project, home=None):
    project = Path(project)
    home = codex_home(home)
    paths, indexed = set(), set()
    for database in sorted(home.glob('state_*.sqlite'), reverse=True):
        try:
            with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.5) as db:
                rows = list(db.execute('SELECT id,rollout_path FROM threads WHERE cwd=?', (str(project),)))
            for identity, path in rows:
                indexed.add(identity)
                paths.add(Path(path))
            break
        except sqlite3.Error:
            continue
    # Also inspect metadata of unindexed logs; all-time includes retained history.
    for folder in ('sessions', 'archived_sessions'):
        for path in (home / folder).rglob('*.jsonl'):
            if path in paths:
                continue
            try:
                with path.open() as stream:
                    for _ in range(8):
                        line = stream.readline()
                        if not line:
                            break
                        record = json.loads(line)
                        if record.get('type') == 'session_meta':
                            if workspace(record.get('payload', {}).get('cwd')) == str(project):
                                paths.add(path)
                            break
            except (OSError, ValueError):
                continue
    parsed, missing = [], 0
    for path in sorted(paths):
        try:
            parsed.append(parse_rollout(path))
        except OSError:
            missing += 1
    claude = claude_logs(project)
    for path in claude:
        try:
            parsed.append(parse_claude_transcript(path))
        except OSError:
            missing += 1
    sessions, events, actions, coverage = normalize_sessions(parsed, project)
    coverage['missingFiles'] = missing
    coverage['indexedSessions'] = len(indexed)
    coverage['sourceFiles'] = {'Codex': len(paths), 'Claude Code': len(claude)}
    events.sort(key=lambda e: e['at'])
    now = datetime.now().astimezone().isoformat(timespec='seconds')
    data = {'project': project.name, 'id': project_id(project), 'timezone': zone_label(),
            'generated': now,
            'sources': [name for name, count in coverage['sourceFiles'].items() if count],
            'today': now[:10], 'sessions': sessions, 'events': events, 'actions': actions,
            'commits': git_milestones(project), 'coverage': coverage,
            'first': events[0]['at'] if events else None, 'last': events[-1]['at'] if events else None}
    data['version'] = hashlib.sha256(json.dumps({k:v for k,v in data.items() if k != 'generated'}, sort_keys=True).encode()).hexdigest()[:16]
    return data


def overview(projects, home=None):
    """Daily token totals per project, enough for a share panel and no more."""
    rows = []
    for project in projects:
        days = {}
        for event in snapshot(project['path'], home)['events']:
            total = days.setdefault(event['at'][:10], [0, 0, 0])
            for i, value in enumerate(event['tokens']):
                total[i] += value
        rows.append({'id': project['id'], 'name': project['name'],
                     'sessions': project['sessions'], 'days': days})
    return rows

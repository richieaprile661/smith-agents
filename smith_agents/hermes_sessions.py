"""Observe local Hermes chat leases and saved messages without importing Hermes.

The active-session registry establishes ownership; state.db alone never makes
a conversation live. These are best-effort local formats, not a Hermes API.
No credentials, configuration, system prompts, or reasoning fields are read.
"""
from collections import Counter
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time


def home():
    """Where Hermes keeps its state, found the way Hermes finds it: HERMES_HOME,
    else %LOCALAPPDATA%\\hermes on Windows and ~/.hermes elsewhere."""
    if os.environ.get("HERMES_HOME", "").strip():
        return Path(os.path.expandvars(os.environ["HERMES_HOME"].strip())).expanduser()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        return (Path(local) if local else Path.home() / "AppData" / "Local") / "hermes"
    return Path.home() / ".hermes"


def _number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) and value >= 0 else None
    return None


def _text(value, limit=2000):
    return " ".join(value[:limit * 2].split())[:limit] if isinstance(value, str) else ""


def _content(value):
    # Hermes serializes multimodal content as JSON. Only public text blocks
    # belong in the preview; images and reasoning blocks must stay out.
    if isinstance(value, str) and value.lstrip().startswith("["):
        try:
            blocks = json.loads(value)
        except ValueError:
            return ""
        if isinstance(blocks, list):
            return _text(" ".join(block["text"] for block in blocks
                                  if isinstance(block, dict) and block.get("type") == "text"
                                  and isinstance(block.get("text"), str)))
    return _text(value)


def _hermes_command(executable, argv):
    """Match launch positions, never a mention of Hermes in a prompt argument."""
    name = Path(executable).name.lower()
    if name in ("hermes", "hermes.exe"):
        return True
    if not name.startswith(("python", "pypy")) or len(argv) < 2:
        return False
    if argv[1:3] in (["-m", "hermes_cli"], ["-m", "hermes_cli.main"]):
        return True
    script = Path(argv[1])
    return (script.name.lower() in ("hermes", "hermes.exe", "hermes-script.py")
            or (script.name == "main.py" and script.parent.name == "hermes_cli")
            or (script.name == "cli.py" and (script.parent / "hermes_cli").is_dir()))


def owner(entry):
    import psutil
    pid, started = entry.get("pid"), _number(entry.get("process_start_time"))
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not started:
        return None
    try:
        process = psutil.Process(pid)
        created = process.create_time()
        if abs(created - started) > .01 or not _hermes_command(process.exe(), process.cmdline()):
            return None
        try:
            cwd = process.cwd()
        except psutil.Error:
            cwd = ""
        return {"pid": pid, "created": created, "cwd": cwd}
    except (psutil.Error, OSError, ValueError):
        return None


def _entries(root):
    try:
        with (root / "runtime" / "active_sessions.json").open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return []
        data = json.loads(raw)
        entries = data.get("entries") if isinstance(data, dict) else data
        return [e for e in entries[:256] if isinstance(e, dict)] if isinstance(entries, list) else []
    except (OSError, ValueError):
        return []


def _columns(db, table):
    return {row[1] for row in db.execute("PRAGMA table_info(" + table + ")")}


def _select(columns, wanted):
    # Both sets originate in our allowlists or PRAGMA, never registry contents.
    return ", ".join("substr(%s, 1, 8192) AS %s" % (key, key)
                     if key in {"content", "tool_calls", "title", "cwd"} else key
                     for key in wanted if key in columns)


def _details(db, session, session_columns, message_columns):
    fields = _select(session_columns, ("id", "title", "model", "cwd", "started_at"))
    saved = db.execute("SELECT " + fields + " FROM sessions WHERE id = ?", (session,)).fetchone()
    fields = _select(message_columns, ("id", "role", "content", "timestamp", "tool_calls", "tool_name", "finish_reason"))
    filters = ["session_id = ?", "role IN ('user', 'assistant', 'tool')"]
    if "active" in message_columns:
        filters.append("active = 1")
    if "display_kind" in message_columns:
        filters.append("COALESCE(display_kind, '') <> 'hidden'")
    if "_compressed_summary" in message_columns:
        filters.append("COALESCE(_compressed_summary, 0) = 0")
    messages = db.execute("SELECT " + fields + " FROM messages WHERE " + " AND ".join(filters)
                          + " ORDER BY timestamp DESC, id DESC LIMIT 32", (session,)).fetchall()
    return dict(saved) if saved else {}, [dict(row) for row in reversed(messages)]


def _row(entry, process, saved, messages, now):
    session = entry["session_id"]
    saved_cwd = saved.get("cwd")
    cwd = saved_cwd if isinstance(saved_cwd, str) and saved_cwd.strip() else process.get("cwd", "")
    title = _text(saved.get("title"), 160)
    # Match the other providers: project identity first, chat title secondary.
    # A chat launched from the home directory has no project identity to show.
    path = cwd.replace("\\", "/").rstrip("/")
    is_home = os.path.normcase(os.path.normpath(os.path.expanduser(cwd))) == os.path.normcase(str(Path.home()))
    project = path.rsplit("/", 1)[-1] if path and not is_home else ""
    since = _number(saved.get("started_at")) or _number(entry.get("started_at")) or process["created"]
    row = dict(provider="hermes", id="hermes:" + session, session_id=session,
               pid=process["pid"], process_created=process["created"],
               name=project or title or "Hermes session", session_name=title or None,
               cwd=cwd, since=since, entrypoint="hermes-" + (_text(entry.get("surface"), 40) or "cli"),
               model=_text(saved.get("model"), 160) or None, sub=False,
               state="needs", status_detail="Activity unknown", idle=None,
               active=False, stale=False, can_terminate=False,
               context_tokens=None, context_capacity=None, permissions=[], tail=[],
               last_request=None, latest_message=None, last_tool=None)
    for message in messages:
        role, value = message.get("role"), _content(message.get("content"))
        if role in ("user", "assistant") and value:
            row["last_request" if role == "user" else "latest_message"] = value
            row["tail"].append(("text", value[:160]))
        try:
            calls = json.loads(message.get("tool_calls") or "[]")
        except (ValueError, TypeError):
            calls = []
        if isinstance(calls, list):
            for call in calls[:32]:
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(function, dict) and _text(function.get("name")):
                    row["last_tool"] = _text(function["name"], 160)
                    row["tail"].append(("cmd", row["last_tool"]))
        if role == "tool" and _text(message.get("tool_name")):
            row["last_tool"] = _text(message["tool_name"], 160)
    row["tail"] = row["tail"][-8:]
    if messages:
        last = messages[-1]
        at = _number(last.get("timestamp"))
        row["idle"] = max(0, now - at) if at is not None else None
        row["stale"] = row["idle"] is not None and row["idle"] > 1800
        # A saved assistant message can be mid-turn. Only explicit stop evidence
        # earns a ready figure; quiet unfinished work is not an approval request.
        if last.get("role") == "assistant" and last.get("finish_reason") == "stop" and last.get("tool_calls") in (None, "", "[]", "null"):
            row.update(state="done", status_detail="Last reply saved")
        elif row["idle"] is not None:
            recent = row["idle"] < 90
            row.update(state="working" if recent else "needs", active=recent,
                       status_detail="Recent activity" if recent else "Quiet · check Hermes")
    return row


class Scanner:
    def __init__(self, owner_reader=owner):
        self.owner_reader = owner_reader
        self.previous = {}

    def scan(self, now=None):
        now = time.time() if now is None else now
        root = home()
        entries = [entry for entry in _entries(root)
                   if isinstance(entry.get("session_id"), str) and entry["session_id"].strip()]
        counts = Counter(entry["session_id"] for entry in entries)
        live = [(entry, self.owner_reader(entry)) for entry in entries if counts[entry["session_id"]] == 1]
        live = [(entry, process) for entry, process in live if process]
        details = {}
        if live:
            try:
                with closing(sqlite3.connect((root / "state.db").resolve().as_uri() + "?mode=ro", uri=True, timeout=.1)) as db:
                    db.row_factory = sqlite3.Row
                    db.execute("BEGIN")
                    # Bound work even if a future schema loses its message index.
                    deadline = time.monotonic() + .15
                    db.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
                    sc, mc = _columns(db, "sessions"), _columns(db, "messages")
                    if "id" in sc and {"id", "session_id", "role", "timestamp"} <= mc:
                        for entry, _ in live:
                            details[entry["session_id"]] = _details(db, entry["session_id"], sc, mc)
            except (OSError, sqlite3.Error):
                pass  # Keep verified live rows when history is unavailable.
        rows = {}
        for entry, process in live:
            saved, messages = details.get(entry["session_id"], ({}, []))
            row = _row(entry, process, saved, messages, now)
            rows[row["id"]] = row
        for key, old in self.previous.items():
            if key not in rows:
                closed_at = old.get("closed_at", now)
                if now - closed_at < 120:
                    rows[key] = dict(old, state="closed", active=False, closed_at=closed_at,
                                     status_detail=None, permissions=[])
        self.previous = rows
        return list(rows.values())


_scanner = Scanner()


def list_agents():
    return _scanner.scan()

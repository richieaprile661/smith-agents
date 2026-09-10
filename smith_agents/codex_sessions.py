"""Local Codex sessions, independent of the terminal or editor hosting them.

Open rollout/writer-lock handles establish ownership; a historical transcript
alone never establishes liveness. Rollouts are a versioned, best-effort fallback
for clients without hooks, not a public Codex API. No credentials are read.
"""
from collections import deque
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import time


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError):
        return 0


def count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def text(value, limit=2000):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


class Transcript:
    """Incremental, bounded reader; incomplete JSON lines wait for the next scan."""
    MAX_LINE = 2 * 1024 * 1024
    BUDGET = 4 * 1024 * 1024

    def __init__(self):
        self.offset = 0
        self.identity = None
        self.meta = {}
        self.state = "needs"
        self.state_at = 0
        self.turn = None
        self.model = None
        self.tokens = self.capacity = None
        self.request = self.message = self.tool = None
        self.tail = deque(maxlen=8)

    def read(self, path):
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self.identity != identity or stat.st_size < self.offset:
            self.__init__()
            self.identity = identity
        with path.open("rb") as stream:
            stream.seek(self.offset)
            end = self.offset + self.BUDGET
            while stream.tell() < end:
                start = stream.tell()
                raw = stream.readline(self.MAX_LINE + 1)
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    if len(raw) <= self.MAX_LINE:
                        break
                    # Skip an oversized record without retaining its contents.
                    while raw and not raw.endswith(b"\n"):
                        raw = stream.readline(self.MAX_LINE + 1)
                    if not raw:
                        stream.seek(start)
                        break
                else:
                    try:
                        self.record(json.loads(raw))
                    except (ValueError, TypeError, AttributeError):
                        pass
                self.offset = stream.tell()
        return self.offset >= stat.st_size, stat.st_mtime

    def transition(self, state, at, turn=None):
        if at >= self.state_at:
            self.state, self.state_at = state, at
            if turn:
                self.turn = turn

    def record(self, record):
        if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
            return
        payload = record["payload"]
        kind, event = record.get("type"), payload.get("type")
        at = timestamp(record.get("timestamp"))
        if kind == "session_meta":
            self.meta.update({key: payload[key] for key in (
                "id", "session_id", "parent_thread_id", "cwd", "timestamp", "source",
                "originator", "cli_version", "agent_nickname", "agent_role", "thread_source") if key in payload})
        elif kind == "turn_context":
            self.model = payload.get("model") or self.model
        elif kind == "compacted" or (kind == "event_msg" and event == "context_compacted"):
            self.tokens = None
        elif kind == "event_msg":
            if event in ("task_started", "turn_started"):
                self.transition("working", at, payload.get("turn_id"))
            elif event in ("task_complete", "turn_complete", "turn_aborted"):
                if not self.turn or not payload.get("turn_id") or payload["turn_id"] == self.turn:
                    self.transition("done", at)
            elif event == "token_count":
                info = payload.get("info")
                if isinstance(info, dict):
                    usage = info.get("last_token_usage") or {}
                    # Cached input is a subset of input_tokens in Codex.
                    self.tokens = count(usage.get("input_tokens")) if isinstance(usage, dict) else None
                    self.capacity = count(info.get("model_context_window"))
            elif event in ("user_message", "agent_message"):
                self.prose("user" if event == "user_message" else "assistant", payload.get("message"))
            elif event == "item_completed" and isinstance(payload.get("item"), dict):
                self.item(payload["item"])
        elif kind == "response_item":
            self.item(payload)

    def prose(self, role, value):
        value = text(value)
        if not value or value.startswith(("<environment_context>", "<permissions instructions>", "<INSTRUCTIONS>")):
            return
        if role == "user":
            self.request = value
        elif role == "assistant":
            self.message = value
        else:
            return
        line = ("text", value[:160])
        if not self.tail or self.tail[-1] != line:
            self.tail.append(line)

    def item(self, item):
        kind = item.get("type")
        if kind in ("UserMessage", "AgentMessage"):
            content = item.get("content")
            if isinstance(content, list):
                self.prose("user" if kind == "UserMessage" else "assistant",
                           " ".join(block["text"] for block in content if isinstance(block, dict)
                                     and isinstance(block.get("text"), str)))
        elif kind == "ContextCompaction":
            self.tokens = None
        if kind == "message":
            content = item.get("content")
            if isinstance(content, list):
                self.prose(item.get("role"), " ".join(block.get("text", "") for block in content
                           if isinstance(block, dict) and isinstance(block.get("text"), str)
                           and block.get("type") in ("input_text", "output_text", "text")))
        elif kind in ("function_call", "custom_tool_call"):
            name = text(item.get("name"), 120)
            arg = item.get("arguments", item.get("input", ""))
            try:
                parsed = json.loads(arg) if isinstance(arg, str) else arg
            except ValueError:
                parsed = arg
            if isinstance(parsed, dict):
                arg = parsed.get("cmd") or parsed.get("command") or parsed.get("file_path") or ""
            self.tool = (name + " " + text(arg)).strip()
            self.tail.append(("cmd", self.tool[:160]))


def processes():
    """Return only verified native Codex executables, with their open files."""
    import psutil
    result = []
    for process in psutil.process_iter(["pid", "name"]):
        if (process.info.get("name") or "").lower() not in ("codex", "codex.exe"):
            continue
        try:
            if Path(process.exe()).name.lower() not in ("codex", "codex.exe"):
                continue
            try:
                files = [item.path for item in process.open_files()]
                files_known = True
            except psutil.Error:
                files = []  # Hooks can still identify this process on Windows.
                files_known = False
            result.append({"pid": process.pid, "created": process.create_time(),
                           "server": "app-server" in process.cmdline(), "files": files,
                           "files_known": files_known})
        except psutil.Error:
            continue
    return result


def locked_rollouts(root, ids):
    """Resolve locks through a read-only index, tolerating missing/new schemas."""
    found = {}
    if not ids:
        return found
    for database in sorted(root.glob("state_*.sqlite"), reverse=True):
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=.1)) as connection:
                marks = ",".join("?" for _ in ids)
                for session, path in connection.execute(
                        "SELECT id, rollout_path FROM threads WHERE id IN (" + marks + ")", list(ids)):
                    candidate = Path(path)
                    if candidate.is_file():
                        found[session] = candidate
        except (sqlite3.Error, OSError, ValueError):
            continue
        if ids <= found.keys():
            break
    for session in ids - found.keys():
        matches = list((root / "sessions").glob("*/*/*/rollout-*-" + session + ".jsonl"))
        if len(matches) == 1:
            found[session] = matches[0]
    return found


class Scanner:
    def __init__(self, process_source=processes):
        self.process_source = process_source
        self.readers = {}
        self.previous = {}
        self.closed = {}

    def scan(self, now=None):
        from .codex_hooks import snapshots
        now = time.time() if now is None else now
        owners = {}
        live = self.process_source()
        roots = {home()}
        for process in live:
            paths, locks = set(), {}
            for raw in process["files"]:
                path = Path(raw)
                if path.name.startswith("rollout-") and path.suffix == ".jsonl":
                    paths.add(path)
                elif path.parent.name == "thread-writer-locks" and path.suffix == ".lock" and not path.name.startswith("."):
                    locks.setdefault(path.parent.parent, set()).add(path.stem)
                    roots.add(path.parent.parent)
            for root, ids in locks.items():
                # Usually the rollout is already open, avoiding an index query.
                missing = {session for session in ids if not any(p.name.endswith(session + ".jsonl") for p in paths)}
                paths.update(locked_rollouts(root, missing).values())
            for path in paths:
                owners.setdefault(path, []).append(process)
        hooks = {}
        for root in roots:
            for snapshot in snapshots(root):
                owner = snapshot.get("owner") or {}
                matches = [process for process in live if process["pid"] == owner.get("pid")
                           and process["created"] == owner.get("created")]
                if len(matches) != 1 or snapshot.get("state") == "closed":
                    continue
                hook_path = snapshot.get("transcript")
                if not isinstance(hook_path, str) or not hook_path:
                    continue
                path = Path(hook_path)
                hooks[path] = snapshot
                if path not in owners:
                    owners[path] = matches
        rows = {}
        for path, candidates in owners.items():
            if len(candidates) != 1:
                continue  # Ambiguous ownership cannot authorize controls.
            process = candidates[0]
            reader = self.readers.setdefault(path, Transcript())
            try:
                caught_up, modified = reader.read(path)
            except OSError:
                continue
            meta = reader.meta
            session = meta.get("id")
            if not isinstance(session, str) or not session:
                continue
            source = meta.get("source")
            parent = meta.get("parent_thread_id")
            if isinstance(source, dict):
                sub = source.get("subagent")
                spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
                if isinstance(spawn, dict):
                    parent = parent or spawn.get("parent_thread_id")
            if not isinstance(parent, str) or parent == session:
                parent = None
            cwd = meta.get("cwd") if isinstance(meta.get("cwd"), str) else ""
            entry = "codex-vscode" if source == "vscode" or meta.get("originator") == "codex_vscode" else "codex-cli"
            state = reader.state if caught_up else "needs"
            hook = hooks.get(path)
            if hook and hook.get("id") == session and hook.get("updated", 0) >= reader.state_at:
                state = hook["state"]
            else:
                hook = None
            idle = max(0, now - modified)
            row = {"provider": "codex", "id": "codex:" + session, "session_id": session,
                   "pid": process["pid"], "process_created": process["created"],
                   "name": (meta.get("agent_nickname") or meta.get("agent_role") or "Helper") if parent else Path(cwd).name or "Codex session",
                   "cwd": cwd, "entrypoint": entry, "state": state, "idle": idle,
                   "since": timestamp(meta.get("timestamp")), "transcript": str(path),
                   "sub": bool(parent), "parent": "codex:" + parent if parent else None,
                   "model": reader.model or (hook or {}).get("model"), "context_tokens": reader.tokens,
                   "context_capacity": reader.capacity, "last_request": reader.request,
                   "latest_message": reader.message, "last_tool": reader.tool,
                   "tail": list(reader.tail), "permissions": [], "active": state == "working",
                   "stale": idle > 1800, "can_terminate": False,
                   "status_detail": "Loading session…" if not caught_up else
                       "Activity unknown" if not reader.state_at else None}
            if hook:
                row["status_detail"] = None
                row["permissions"] = [{"actionable": False, "request": request}
                                      for request in hook.get("pending", {}).values()]
            # A loaded helper can stay open after finishing; only show it during
            # work or briefly after completion, rather than forever beside Ready.
            if parent and state == "done" and idle > 120:
                continue
            rows[row["id"]] = row
        # Keep only children whose parent is visible; never invent parent links
        # from a common directory or a shared process.
        rows = {key: row for key, row in rows.items() if not row["sub"] or row["parent"] in rows}
        for key, row in self.previous.items():
            if key not in rows and not row.get("sub"):
                unreadable = any(p["pid"] == row["pid"] and p["created"] == row["process_created"]
                                 and not p.get("files_known", True) for p in live)
                if unreadable:
                    rows[key] = dict(row, state="needs", active=False, permissions=[],
                                     status_detail="Status unavailable", idle=None)
                else:
                    self.closed[key] = (now, dict(row, state="closed", active=False, can_terminate=False))
        for key in rows:
            self.closed.pop(key, None)
        self.closed = {key: pair for key, pair in self.closed.items() if now - pair[0] < 120}
        self.previous = rows
        self.readers = {path: reader for path, reader in self.readers.items() if path in owners}
        return list(rows.values()) + [dict(row, idle=max(0, now - at)) for at, row in self.closed.values()]


_scanner = Scanner()


def list_agents():
    return _scanner.scan()

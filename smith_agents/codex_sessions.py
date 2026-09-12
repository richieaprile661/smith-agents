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
import re
from pathlib import Path
import sqlite3
import time

from .activity import Activity, output, apply_activity, merge_activity


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
        self.activity = Activity()
        self.running_commands = {}
        self.polls = {}

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
                self.activity.transition("running", at, explicit=True)
            elif event in ("task_complete", "turn_complete", "turn_aborted"):
                if not self.turn or not payload.get("turn_id") or payload["turn_id"] == self.turn:
                    self.transition("done", at)
                    self.activity.transition("stopped" if event == "turn_aborted" else "completed", at, explicit=True)
                    if text(payload.get("last_agent_message")):
                        self.message = text(payload["last_agent_message"])
            elif event == "token_count":
                info = payload.get("info")
                if isinstance(info, dict):
                    usage = info.get("last_token_usage") or {}
                    # Cached input is a subset of input_tokens in Codex.
                    self.tokens = count(usage.get("input_tokens")) if isinstance(usage, dict) else None
                    self.capacity = count(info.get("model_context_window"))
                    total = info.get("total_token_usage") or {}
                    if isinstance(total, dict) and count(total.get("total_tokens")) is not None:
                        self.activity.data["total_tokens"] = total["total_tokens"]
            elif event in ("user_message", "agent_message"):
                self.prose("user" if event == "user_message" else "assistant", payload.get("message"))
            elif event == "item_completed" and isinstance(payload.get("item"), dict):
                self.item(payload["item"], at)
            elif event == "exec_command_begin":
                command = payload.get("command")
                self.activity.start(payload.get("call_id"), "Command", " ".join(command) if isinstance(command, list) else command, at)
            elif event == "exec_command_end":
                self.activity.finish(payload.get("call_id"), payload.get("aggregated_output") or payload.get("stdout"), at,
                                     payload.get("exit_code") not in (None, 0))
        elif kind == "response_item":
            self.item(payload, at)

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

    def item(self, item, at=0):
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
            self.activity.start(item.get("call_id"), name, item.get("arguments", item.get("input", "")), at)
            if isinstance(parsed, dict) and name.rsplit(".", 1)[-1] in ("write_stdin", "wait"):
                session = parsed.get("session_id", parsed.get("cell_id"))
                original = self.running_commands.get(str(session))
                if original:
                    self.polls[item.get("call_id")] = original
                    if len(self.polls) > 256:
                        del self.polls[next(iter(self.polls))]
        elif kind in ("function_call_output", "custom_tool_call_output"):
            value = item.get("output")
            key = item.get("call_id")
            decoded = value
            if isinstance(value, str):
                try:
                    decoded = json.loads(value)
                except ValueError:
                    pass
            session = None
            if isinstance(decoded, dict) and decoded.get("exit_code") is None:
                session = decoded.get("session_id")
            elif isinstance(value, str):
                match = re.search(r"(?:Process running with session ID|Script running with cell ID)\s+(\S+)", value)
                session = match.group(1) if match else None
            original = self.polls.pop(key, None)
            if session is not None:
                self.running_commands[str(session)] = original or key
                if len(self.running_commands) > 256:
                    del self.running_commands[next(iter(self.running_commands))]
                self.activity.observe_output(original or key, value, at)
                if original:
                    self.activity.finish(key, value, at)
            else:
                self.activity.finish(key, value, at)
                if original:
                    self.activity.finish(original, value, at)
                    self.running_commands = {s: k for s, k in self.running_commands.items() if k != original}
            if item.get("call_id") in self.activity.data["tools"]:
                self.tail.append(("out", text(output(value), 160)))
        elif kind == "commandExecution":
            key = item.get("id")
            self.activity.start(key, "Command", item.get("command"), at)
            if item.get("status") in ("completed", "failed", "declined"):
                self.activity.finish(key, item.get("aggregatedOutput"), at, item.get("exitCode") not in (None, 0))


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
        from .codex_hooks import snapshots, child_snapshots
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
        hooks, children, retained = {}, {}, set()
        for root in roots:
            for snapshot in child_snapshots(root):
                owner = snapshot.get("owner") or {}
                matches = [p for p in live if p["pid"] == owner.get("pid") and p["created"] == owner.get("created")]
                if len(matches) != 1:
                    continue
                children[snapshot["id"]] = snapshot
                path = snapshot.get("transcript")
                resolved = {snapshot["id"]: Path(path)} if path else locked_rollouts(root, {snapshot["id"]})
                if snapshot["id"] in resolved:
                    path = resolved[snapshot["id"]]
                    if path not in owners:
                        owners[path] = matches
                        retained.add(path)
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
        # A helper can close its rollout handle when done. Read its remaining
        # output while the verified process and parent still exist.
        for previous in list(self.previous.values())[:512]:
            if not previous.get("sub") or not previous.get("transcript"):
                continue
            matches = [p for p in live if p["pid"] == previous["pid"] and p["created"] == previous["process_created"]]
            path = Path(previous["transcript"])
            if len(matches) == 1 and path not in owners:
                owners[path] = matches
                retained.add(path)
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
            child_event = children.get(session)
            if child_event and child_event.get("parent") != parent:
                child_event = None  # A path cannot override transcript identity.
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
            activity = reader.activity.snapshot()
            activity["assignment"] = reader.request
            if hook:
                row["status_detail"] = None
                row["permissions"] = [{"actionable": False, "request": request}
                                      for request in hook.get("pending", {}).values()]
                observed = hook.get("activity") or {}
                # Hook lifetime follows the actual command (including unified
                # exec polls); a function-call result may just yield a session.
                activity = merge_activity(activity, observed)
            if child_event and child_event["updated"] >= activity.get("state_at", 0):
                activity.update(status=child_event["status"], state_at=child_event["updated"], explicit=True,
                                result=child_event.get("result"), started_at=child_event.get("started_at"))
                if child_event.get("ended_at"):
                    activity["ended_at"] = child_event["ended_at"]
            if path in retained and activity.get("status") == "running" and not hook:
                activity.update(status="unknown", disconnected=True)
            if not caught_up:
                activity.update(status="unknown", loading=True)
            apply_activity(row, activity, now)
            rows[row["id"]] = row
        for child in children.values():
            key, parent_key = "codex:" + child["id"], "codex:" + child["parent"]
            if key in rows or parent_key not in rows:
                continue
            parent = rows[parent_key]
            if parent["pid"] != child["owner"]["pid"] or parent["process_created"] != child["owner"]["created"]:
                continue
            row = dict(parent, id=key, session_id=child["id"], parent=parent_key, sub=True,
                       name=child.get("name") or "Helper", transcript=None, permissions=[],
                       context_tokens=None, context_capacity=None, model=None, tail=[],
                       last_request=None, latest_message=child.get("result"), last_tool=None,
                       since=child.get("started_at"), idle=max(0, now-child["updated"]))
            data = {k: child[k] for k in ("status", "result", "started_at", "ended_at") if k in child}
            data.update(explicit=True, state_at=child["updated"], tools={})
            apply_activity(row, data, now)
            rows[key] = row
        # Keep only children whose parent is visible; never invent parent links
        # from a common directory or a shared process.
        def has_root(row):
            seen = set()
            while row.get("sub"):
                if row["id"] in seen:
                    return False
                seen.add(row["id"])
                row = rows.get(row["parent"])
                if row is None:
                    return False
            return True
        rows = {key: row for key, row in rows.items() if has_root(row)}
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

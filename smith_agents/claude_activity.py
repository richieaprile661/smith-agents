"""Observe Claude's existing task stream/status line without changing it."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from .activity import Activity, number, plain, stamp, merge_activity


def save(directory, session, source, data, now=None):
    if not isinstance(session, str) or not session:
        return
    now = time.time() if now is None else now
    Path(directory).mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(Path(directory) / "activity.sqlite", timeout=.1)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS activity (session TEXT, source TEXT, updated REAL, data TEXT, PRIMARY KEY(session, source))")
        db.execute("INSERT OR REPLACE INTO activity VALUES (?, ?, ?, ?)", (session, source, now, json.dumps(data)))
        db.execute("DELETE FROM activity WHERE updated < ?", (now - 7 * 86400,))


def read(directory, parent, now=None):
    now = time.time() if now is None else now
    path = Path(directory) / "activity.sqlite"
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.05)) as db:
            rows = db.execute("SELECT source, updated, data FROM activity WHERE session=? ORDER BY updated",
                              (parent["id"],)).fetchall()
        tasks = {}
        for source, updated, raw in rows:
            data = json.loads(raw)
            # Do not reuse a previous process's running state after a resume.
            if updated < (parent.get("started_at") or 0):
                continue
            disconnected = not data.get("connected", True) or source == "statusline" and now - updated > 30
            owner = data.get("owner")
            if owner:
                from .runtime import backend
                disconnected |= not backend().pid_alive(owner["pid"], owner["started"])
            for key, value in data.get("tasks", {}).items():
                row = dict(value)
                row["disconnected"] = disconnected
                if disconnected and row.get("status") == "running":
                    row["status"] = "unknown"
                key = key.removeprefix("agent-")
                tasks[key] = merge_activity(tasks.get(key, {}), row)
        return tasks
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError, KeyError):
        return {}


def capture_status(payload, directory, now=None):
    now = time.time() if now is None else now
    previous = {}
    path = Path(directory) / "activity.sqlite"
    if path.is_file():
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.05)) as db:
                saved = db.execute("SELECT data FROM activity WHERE session=? AND source='statusline'",
                                   (payload.get("session_id"),)).fetchone()
                if saved:
                    previous = json.loads(saved[0]).get("tasks", {})
        except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
            pass
    tasks = {}
    for task in payload.get("tasks", [])[:256]:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str) or "status" not in task:
            continue
        status = {"running": "running", "completed": "completed", "failed": "failed",
                  "killed": "stopped", "stopped": "stopped"}.get(task["status"], "unknown")
        row = {"status": status, "explicit": True, "state_at": now,
               "name": plain(task.get("description") or task.get("name"), 120),
               "assignment": plain(task.get("description")), "summary": plain(task.get("label")),
               "model": plain(task.get("model")), "tools": {}}
        start = number(task.get("startTime"))
        if start is not None:
            row["started_at"] = start / 1000
        if status in ("completed", "failed", "stopped"):
            old = previous.get(task["id"], {})
            row["ended_at"] = (old.get("ended_at") if old.get("status") == status
                               and old.get("started_at") == row.get("started_at") else None) or now
            row["state_at"] = row["ended_at"]
        tasks[task["id"]] = row
    if tasks:
        save(directory, payload.get("session_id"), "statusline", {"tasks": tasks}, now)


class TaskStream:
    def __init__(self, directory, owner=None):
        self.directory, self.owner = directory, owner
        self.sessions = {}
        self.pending = []

    def persist(self, session):
        source = "stream" if not self.owner else f"stream:{self.owner['pid']}:{self.owner['started']}"
        save(self.directory, session, source, dict(self.sessions[session], owner=self.owner))

    def record(self, row):
        if not isinstance(row, dict) or not isinstance(row.get("session_id"), str):
            return
        session = row["session_id"]
        kind, subtype = row.get("type"), row.get("subtype")
        relevant = kind == "system" and subtype in ("task_started", "task_progress", "task_notification", "task_updated")
        if not relevant and kind not in ("assistant", "user", "tool_progress"):
            return
        state = self.sessions.setdefault(session, {"tasks": {}, "spawns": {}, "connected": True})
        if len(self.sessions) > 32:
            del self.sessions[next(iter(self.sessions))]
        tasks, spawns = state["tasks"], state["spawns"]
        at = stamp(row.get("timestamp")) or time.time()
        key = row.get("task_id")
        parent_tool = row.get("parent_tool_use_id")
        if relevant:
            if not isinstance(key, str) or not key:
                return
            if subtype == "task_started" and row.get("task_type") not in (None, "local_agent"):
                return
            if key not in tasks and subtype != "task_started":
                return  # never manufacture a helper for a background shell task
            spawn_id = row.get("tool_use_id")
            previous = tasks.get(key, {})
            spawned = spawns.get(spawn_id, {})
            activity = Activity(previous)
            if subtype == "task_started":
                activity.transition("running", at, explicit=True)
                activity.data.update(name=plain(row.get("description"), 120),
                                     assignment=plain(row.get("prompt") or spawned.get("assignment")),
                                     parent_task=spawned.get("parent_task"), spawn_id=spawn_id)
                if isinstance(spawn_id, str):
                    spawns.setdefault(spawn_id, {})["task_id"] = key
            elif subtype == "task_progress":
                activity.transition("running", at, explicit=True)
                if plain(row.get("summary")):
                    activity.data["summary"] = plain(row["summary"])
                activity.data["reported_tool"] = plain(row.get("last_tool_name"), 120)
            else:
                patch = row.get("patch", {}) if subtype == "task_updated" else row
                status = {"completed": "completed", "failed": "failed", "stopped": "stopped",
                          "killed": "stopped", "running": "running"}.get(patch.get("status"))
                if status:
                    activity.transition(status, at, explicit=True)
                if plain(patch.get("summary") or patch.get("error")):
                    activity.data["result"] = plain(patch.get("summary") or patch.get("error"))
            usage = row.get("usage") or {}
            for src, dst in (("total_tokens", "total_tokens"), ("tool_uses", "reported_tool_count"), ("duration_ms", "duration_ms")):
                if number(usage.get(src)) is not None:
                    activity.data[dst] = usage[src]
            tasks[key] = activity.snapshot()
            if subtype == "task_started" and isinstance(spawn_id, str):
                waiting = [r for r in self.pending if r.get("session_id") == session and r.get("parent_tool_use_id") == spawn_id]
                self.pending = [r for r in self.pending if r not in waiting]
                for pending in waiting:
                    self.record(pending)
        elif kind in ("assistant", "user"):
            message = row.get("message") or {}
            blocks = message.get("content")
            if not isinstance(blocks, list):
                return
            child_id = spawns.get(parent_tool, {}).get("task_id")
            if parent_tool and not child_id:
                self.pending.append(row)
                self.pending = self.pending[-32:]
                return
            changed = False
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") in ("Agent", "Task"):
                    inputs = block.get("input") or {}
                    if isinstance(block.get("id"), str):
                        spawns.setdefault(block["id"], {}).update(assignment=plain(inputs.get("prompt")), parent_task=child_id)
                        known = spawns[block["id"]].get("task_id")
                        if known in tasks:
                            tasks[known].update(assignment=plain(inputs.get("prompt")), parent_task=child_id)
                        changed = True
            if child_id in tasks:
                activity = Activity(tasks[child_id])
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if kind == "assistant" and block.get("type") == "tool_use":
                        activity.start(block.get("id"), block.get("name"), block.get("input"), at)
                    elif kind == "user" and block.get("type") == "tool_result":
                        activity.finish(block.get("tool_use_id"), block.get("content"), at, block.get("is_error") is True)
                    elif kind == "assistant" and block.get("type") == "text":
                        activity.data["latest_message"] = plain(block.get("text"))
                activity.data["model"] = plain(message.get("model")) or activity.data.get("model")
                tasks[child_id] = activity.snapshot()
                changed = True
            if not changed:
                return
        elif kind == "tool_progress":
            key = key or spawns.get(parent_tool, {}).get("task_id")
            if key not in tasks:
                return
            activity = Activity(tasks[key])
            tool_id = row.get("tool_use_id")
            elapsed = number(row.get("elapsed_time_seconds"))
            activity.start(tool_id, row.get("tool_name"), "", at - (elapsed or 0))
            activity.transition("running", at, explicit=True)
            tasks[key] = activity.snapshot()
        while len(tasks) > 256:
            del tasks[next(iter(tasks))]
        while len(spawns) > 512:
            del spawns[next(iter(spawns))]
        self.persist(session)

    def close(self):
        for session, state in self.sessions.items():
            state["connected"] = False
            try:
                self.persist(session)
            except (OSError, sqlite3.Error):
                pass

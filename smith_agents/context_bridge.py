"""Capture Claude's reported context capacities while preserving status output.

Only the standard library is imported: status-line calls must not start the UI.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def snapshot_path(directory, session_id, task_id=""):
    key = json.dumps([session_id, task_id.removeprefix("agent-")])
    return Path(directory) / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".context-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def capture(payload, directory, kind):
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    if kind == "subagents":
        from .claude_activity import capture_status
        import sqlite3
        try:
            capture_status(payload, directory)
        except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
            pass
    if kind == "main":
        window = payload.get("context_window") or {}
        model = payload.get("model") or {}
        rows = [("", window.get("context_window_size"), model.get("id"))]
    else:
        rows = [(task.get("id"), task.get("contextWindowSize"), task.get("model"))
                for task in payload.get("tasks", []) if isinstance(task, dict)]
    for task_id, capacity, model in rows:
        if not isinstance(task_id, str):
            continue
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            continue
        atomic_json(snapshot_path(directory, session_id, task_id), {
            "session_id": session_id, "task_id": task_id.removeprefix("agent-"),
            "capacity": capacity, "model": model if isinstance(model, str) else None,
            "updated_at": time.time(),
        })


def _api_model(model):
    # Claude's status line retains the window selector; API transcripts omit it.
    # Capacity still comes from the session's feed, never from this suffix.
    return model[:-4] if model.lower().endswith("[1m]") else model


def read_capacity(directory, agent, model=None):
    session_id = (agent.get("root_parent") or agent.get("parent")) if agent.get("sub") else agent.get("id")
    task_id = agent.get("id", "") if agent.get("sub") else ""
    if not session_id:
        return None
    try:
        data = json.loads(snapshot_path(directory, session_id, task_id).read_text(encoding="utf-8"))
        if data.get("session_id") != session_id or data.get("task_id") != task_id.removeprefix("agent-"):
            return None
        if model and data.get("model") and _api_model(model) != _api_model(data["model"]):
            return None
        capacity = data.get("capacity")
        return capacity if isinstance(capacity, int) and not isinstance(capacity, bool) and capacity > 0 else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def main(directory, kind):
    raw = sys.stdin.buffer.read()
    try:
        capture(json.loads(raw), directory, kind)
    except (OSError, ValueError, TypeError, AttributeError):
        pass  # A cache failure must not break the user's terminal status line.
    try:
        config = json.loads((Path(directory) / "bridge.json").read_text(encoding="utf-8"))
        command = config.get("commands", {}).get(kind)
    except (OSError, ValueError, AttributeError):
        command = None
    if command:
        # Execute only the status-line command explicitly saved by the installer.
        # Pass Claude's original input unchanged, and inherit stdout/stderr.
        return subprocess.run(command, shell=True, input=raw).returncode
    if kind == "main":
        print("Claude")
    # No subagent overrides: Claude keeps its default row rendering.
    return 0

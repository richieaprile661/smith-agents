"""Optional, observational Codex hooks. Never return an approval decision.

This file can run directly, without importing Pillow or a native UI backend.
"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

EVENTS = ("SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
          "PermissionRequest", "PostToolUse", "Stop", "Interrupt",
          "SubagentStart", "SubagentStop")


def home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".widget-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def capture(payload, root, owner, now=None):
    """Serialize concurrent callbacks and retain approvals until that tool ends."""
    if not isinstance(payload, dict) or payload.get("hook_event_name") not in EVENTS:
        return
    session = payload.get("session_id")
    if not isinstance(session, str) or not session or not owner:
        return
    event = payload["hook_event_name"]
    if event.startswith("Subagent"):
        # These hooks carry the parent's session_id. Do not flip its state or
        # consume its context by treating a child completion as a parent stop.
        return
    now = time.time() if now is None else now
    directory = Path(root) / "widget-events"
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "events.sqlite", timeout=.2)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, updated REAL, data TEXT)")
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute("SELECT data FROM sessions WHERE id=?", (session,)).fetchone()
        row = json.loads(previous[0]) if previous else {}
        if row.get("updated", 0) > now:
            return
        turn = payload.get("turn_id")
        if (row.get("owner") != owner or event == "UserPromptSubmit"
                or turn and row.get("turn") and turn != row["turn"]):
            row = {}
        pending = row.setdefault("pending", {})
        inputs = payload.get("tool_input")
        key = hashlib.sha256(json.dumps([payload.get("tool_name"), inputs], sort_keys=True).encode()).hexdigest()
        if event == "PermissionRequest":
            encoded_inputs = json.dumps(inputs, ensure_ascii=False)
            shown_inputs = (inputs if isinstance(inputs, dict) else {"detail": inputs})
            if len(encoded_inputs) > 8192:
                shown_inputs = {"preview": encoded_inputs[:8192] + "…"}
            pending[key] = {"tool_name": str(payload.get("tool_name") or "Tool")[:120],
                            "input": shown_inputs}
            while len(pending) > 32:
                pending.pop(next(iter(pending)))
        elif event == "PostToolUse":
            pending.pop(key, None)
        elif event in ("Stop", "Interrupt", "SessionEnd", "SessionStart"):
            pending.clear()
        state = {"Stop": "done", "Interrupt": "done", "SessionEnd": "closed",
                 "SessionStart": "done"}.get(event, "working")
        if event == "SessionStart" and payload.get("source") == "compact":
            state = "working"
        row.update(id=session, owner=owner, updated=now, turn=turn or row.get("turn"),
                   state="needs" if pending else state, cwd=payload.get("cwd") or row.get("cwd", ""),
                   transcript=payload.get("transcript_path") or row.get("transcript"),
                   model=payload.get("model") or row.get("model"))
        connection.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, ?)",
                           (session, now, json.dumps(row)))
        connection.execute("DELETE FROM sessions WHERE updated < ?", (now - 7 * 86400,))


def snapshots(root):
    path = Path(root) / "widget-events/events.sqlite"
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.05)) as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT data FROM sessions ORDER BY updated DESC LIMIT 512")]
    except (sqlite3.Error, OSError, ValueError):
        return []


def owner_process():
    import psutil
    try:
        for process in psutil.Process().parents():
            if Path(process.exe()).name.lower() in ("codex", "codex.exe"):
                return {"pid": process.pid, "created": process.create_time(),
                        "server": "app-server" in process.cmdline()}
    except psutil.Error:
        pass
    return None


def install(root=None):
    """Merge our handlers, keeping every existing hook and a first-install backup."""
    root = Path(root or home()).expanduser().resolve()
    path = root / "hooks.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError("Codex hooks.json has an unsupported structure; it was left unchanged.")
    data.setdefault("hooks", {})
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--codex-hook"]
    else:
        command = [sys.executable, str(Path(__file__).resolve()), "capture"]
    command += [str(root)]
    encoded = subprocess.list2cmdline(command) if sys.platform == "win32" else shlex.join(command)
    for event in EVENTS:
        groups = data["hooks"].setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError("Codex hook entries must be lists; no settings were changed.")
        kept = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("Codex hook entries have an unsupported structure; no settings were changed.")
            handlers = [handler for handler in group["hooks"]
                        if not (isinstance(handler, dict) and handler.get("statusMessage") == "Widget session status")]
            if handlers:
                kept.append(dict(group, hooks=handlers))
        kept.append({"hooks": [{"type": "command", "command": encoded,
                                 "timeout": 1, "statusMessage": "Widget session status"}]})
        data["hooks"][event] = kept
    backup = root / "hooks.json.before-widget"
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    atomic_json(path, data)
    return path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "install":
        print(install(argv[1] if len(argv) > 1 else None))
        return 0
    # A broken observer must not alter or prevent the agent's normal operation.
    try:
        root = Path(argv[1]) if len(argv) > 1 else home()
        # GUI/frozen launchers can set sys.stdin to None; Codex still supplies
        # the hook's stdin pipe as descriptor 0.
        raw = bytearray()
        while len(raw) < 1024 * 1024:
            chunk = os.read(0, min(65536, 1024 * 1024 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        payload = json.loads(raw)
        capture(payload, root, owner_process())
    except (OSError, ValueError, TypeError, sqlite3.Error, ImportError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

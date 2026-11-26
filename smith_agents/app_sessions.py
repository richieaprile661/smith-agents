"""Sessions left open in the Claude desktop app, from the records it keeps.

Read only, and little of it: a session's id, title, folder, when it was last
used and whether it is archived. Nothing of a conversation is in these files or
read from them. They are the app's own and undocumented, so a record that is
not what is expected is passed over rather than guessed at.
"""
import glob
import json
import os
from pathlib import Path
import re
import sys

DAY = 86400
STALE_DAYS = 3      # Open, not working, and untouched this long: worth closing.
LIST = 10
MAX_FILE = 4 * 1024 * 1024
_ID = re.compile(r"local_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_WORKTREE = re.compile(r"[\\/]\.claude[\\/]worktrees[\\/]")
_WORKTREE_TAIL = re.compile(r"[\\/]\.claude[\\/]worktrees[\\/][^\\/]+[\\/]*$")


def roots():
    """Where the app may keep its session records on this computer."""
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Claude" / "claude-code-sessions"]
    if sys.platform == "win32":
        found = []
        if os.environ.get("APPDATA"):
            found.append(Path(os.environ["APPDATA"]) / "Claude" / "claude-code-sessions")
        if os.environ.get("LOCALAPPDATA"):   # The Store build keeps its own copy of Roaming.
            found += [Path(path) for path in sorted(glob.glob(os.path.join(
                glob.escape(os.environ["LOCALAPPDATA"]), "Packages", "Claude_*", "LocalCache",
                "Roaming", "Claude", "claude-code-sessions")))]
        return found
    return []


def _seconds(value):
    """An epoch time the app wrote in milliseconds, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return value / 1000.0


def _row(data):
    if not isinstance(data, dict):
        return None
    session = data.get("sessionId")
    last = _seconds(data.get("lastActivityAt")) or _seconds(data.get("createdAt"))
    if not isinstance(session, str) or not _ID.fullmatch(session) or last is None:
        return None
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else ""
    origin = data.get("originCwd") if isinstance(data.get("originCwd"), str) else ""
    title = " ".join(data["title"].split())[:160] if isinstance(data.get("title"), str) else ""
    # A work folder of the session's own is one the app names or keeps where it
    # keeps them. A session that only moved to a subfolder has none.
    named = data.get("worktreePath")
    return {"id": session, "title": title or "Untitled session",
            "cwd": _WORKTREE_TAIL.sub("", origin or cwd),
            "worktree": bool(_WORKTREE.search(cwd) or (isinstance(named, str) and named)),
            "last": last, "archived": data.get("isArchived") is True}


class Reader:
    """Reads each record once, then again only when its file changes."""

    def __init__(self):
        self.files = {}     # path -> (stamp, row or None)

    def read(self, base=None):
        """Every session the app has a record of, or None when it keeps none here."""
        folders = [Path(base)] if base is not None else roots()
        folder = next((path for path in folders if path.is_dir()), None)
        if folder is None:
            return None
        seen, rows = set(), []
        try:
            paths = list(folder.glob("*/*/local_*.json"))
        except OSError:
            return None
        for path in paths:
            seen.add(path)
            try:
                status = path.stat()
            except OSError:
                continue
            stamp = (status.st_size, status.st_mtime_ns)
            known = self.files.get(path)
            if known is None or known[0] != stamp:
                row = None
                if status.st_size <= MAX_FILE:
                    try:
                        row = _row(json.loads(path.read_bytes()))
                    except (OSError, ValueError):
                        row = None
                known = self.files[path] = (stamp, row)
            if known[1]:
                rows.append(known[1])
        for path in [path for path in self.files if path not in seen]:
            del self.files[path]
        return rows


def working_ids(agents):
    """The app sessions the widget can see working right now."""
    return {agent.get("host_session") for agent in agents
            if agent.get("entrypoint") == "claude-desktop" and not agent.get("sub")
            and agent.get("state") == "working" and agent.get("host_session")}


def summary(rows, working, now):
    """Open sessions counted by how long they have sat, and the ones to close."""
    if rows is None:
        return None
    opened = [row for row in rows if not row["archived"]]
    idle = [row for row in opened if row["id"] not in working]
    age = lambda row: max(0.0, now - row["last"]) / DAY
    stale = sorted((row for row in idle if age(row) >= STALE_DAYS), key=lambda row: row["last"])
    return {
        "total": len(opened), "working": len(opened) - len(idle),
        "today": sum(age(row) < 1 for row in idle),
        "recent": sum(1 <= age(row) < STALE_DAYS for row in idle),
        "stale_count": len(stale),
        "stale": [{"id": row["id"], "title": row["title"], "days": int(age(row)),
                   "project": os.path.basename(row["cwd"].rstrip("\\/")), "worktree": row["worktree"],
                   # Enough for the platform to show the session by the app's own link.
                   "host_session": row["id"], "entrypoint": "claude-desktop"}
                  for row in stale[:LIST]],
    }


_reader = Reader()


def read(agents, now):
    return summary(_reader.read(), working_ids(agents), now)

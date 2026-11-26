"""Observe local DeepSeek Harness desktop sessions without importing the app.

A session lock that the running app holds open establishes ownership; a saved
log alone never makes a session live. The log is a best-effort local format,
not a DeepSeek API. No credentials are read, and reasoning never leaves here.
"""
import json
import os
from pathlib import Path
import re
import time

# Replies and tool calls make up nearly all of a log, and only the newest ones
# matter. Everything else that is read is rare enough to follow from the start.
TAIL = 400
MAX_LOG = 256 * 1024 * 1024
_TYPE = re.compile(rb'"type"\s*:\s*"([^"]{1,64})"')
_TIME = re.compile(rb'"time"\s*:\s*(\d{1,16})')
_RARE = frozenset((b"turn/start", b"turn/end", b"approval/asked", b"approval/decided",
                   b"user/message", b"session/title", b"request/context",
                   b"model/selection", b"compaction/end"))
_BULK = frozenset((b"assistant/message", b"tool/call"))
_NAMES = ("deepseek harness", "deepseek harness.exe")


def home():
    return Path.home() / ".dsh"


def can_read_logs():
    """Session logs are zstd, which Python has had built in since 3.14."""
    try:
        from compression import zstd  # noqa: F401
    except ImportError:
        return False
    return True


def _decompress(raw):
    try:
        from compression import zstd
    except ImportError:
        return None
    try:
        return zstd.decompress(raw)
    except (zstd.ZstdError, EOFError, ValueError):
        return None  # Caught mid-write; the next scan reads it whole.


def _text(value, limit=2000):
    return " ".join(value[:limit * 2].split())[:limit] if isinstance(value, str) else ""


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _said(content):
    """Only public text blocks; reasoning and tool arguments stay out."""
    if not isinstance(content, list):
        return ""
    return _text(" ".join(block["text"] for block in content[:64]
                          if isinstance(block, dict) and block.get("type") == "text"
                          and isinstance(block.get("text"), str)))


def _record(line):
    try:
        record = json.loads(line)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def parse(lines):
    """The facts of one main session's log, or None if it is not one."""
    lines = list(lines)
    head = _record(lines[0]) if lines else None
    if not head or head.get("type") != "session":
        return None
    session = head.get("id")
    if not isinstance(session, str) or not session.strip():
        return None
    if head.get("parentSession") or head.get("delegationDepth"):
        return None  # A helper belongs to its parent session, which has its own row.
    facts = dict(session=session, cwd=head["cwd"] if isinstance(head.get("cwd"), str) else "",
                 since=(_count(head.get("createdAt")) or 0) / 1000.0, title=None, model=None,
                 tokens=None, capacity=None, request=None, message=None, tool=None,
                 open_turn=False, failed=False, pending={}, tail=[], at=None)
    bulk_from = len(lines) - TAIL
    for index, line in enumerate(lines[1:], 1):
        kind = _TYPE.search(line[:96])
        if not kind or not (kind[1] in _RARE or (kind[1] in _BULK and index >= bulk_from)):
            continue
        record = _record(line)
        data = record.get("data") if record else None
        if not isinstance(data, dict):
            continue
        kind = record.get("type")
        if kind == "turn/start":
            facts.update(open_turn=True, failed=False, pending={})
        elif kind == "turn/end":
            reason = data.get("reason")
            facts.update(open_turn=False, pending={},
                         failed=isinstance(reason, dict) and reason.get("kind") == "error")
        elif kind == "approval/asked":
            if isinstance(data.get("id"), str):
                facts["pending"][data["id"]] = _text(data.get("toolName"), 160) or "Tool"
        elif kind == "approval/decided":
            facts["pending"].pop(data.get("id"), None)
        elif kind == "user/message":
            facts["request"] = _said(data.get("content")) or facts["request"]
        elif kind == "session/title":
            facts["title"] = _text(data.get("title"), 160) or facts["title"]
        elif kind in ("request/context", "model/selection"):
            facts["model"] = _text(data.get("model"), 160) or facts["model"]
            if kind == "request/context":
                facts["capacity"] = _count(data.get("contextWindow")) or facts["capacity"]
        elif kind == "compaction/end":
            facts["tokens"] = None
        elif kind == "assistant/message":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            source = message.get("source") if isinstance(message.get("source"), dict) else {}
            facts["model"] = _text(source.get("model"), 160) or facts["model"]
            text = _said(message.get("content"))
            if text:
                facts["message"] = text
                facts["tail"].append(("text", text[:160]))
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            counts = [_count(usage.get(key)) for key in
                      ("inputTokens", "cacheReadTokens", "cacheWriteTokens")]
            if counts[0] is not None:
                facts["tokens"] = sum(count or 0 for count in counts)
        elif kind == "tool/call":
            name = _text(data.get("name"), 160)
            if name:
                facts["tool"] = name
                facts["tail"].append(("cmd", name))
    for line in reversed(lines[-32:]):
        stamp = _TIME.search(line[:160])
        if stamp:
            facts["at"] = int(stamp[1]) / 1000.0
            break
    facts["tail"] = facts["tail"][-8:]
    return facts


def _unknown(session):
    return dict(session=session, cwd="", since=0, title=None, model=None, tokens=None,
                capacity=None, request=None, message=None, tool=None, open_turn=False,
                failed=False, pending={}, tail=[], at=None, unknown=True)


def _row(facts, now):
    cwd = facts["cwd"]
    path = cwd.replace("\\", "/").rstrip("/")
    is_home = bool(cwd) and os.path.normcase(os.path.normpath(cwd)) == os.path.normcase(str(Path.home()))
    project = path.rsplit("/", 1)[-1] if path and not is_home else ""
    idle = max(0, now - facts["at"]) if facts["at"] else None
    row = dict(provider="deepseek", id="deepseek:" + facts["session"], session_id=facts["session"],
               name=project or facts["title"] or "DeepSeek session", session_name=facts["title"],
               cwd=cwd, since=facts["since"] or None, entrypoint="deepseek-desktop",
               model=facts["model"], sub=False, state="done", status_detail=None, idle=idle,
               active=False, stale=idle is not None and idle > 1800, can_terminate=False,
               context_tokens=facts["tokens"], context_capacity=facts["capacity"],
               permissions=[], tail=list(facts["tail"]), last_request=facts["request"],
               latest_message=facts["message"], last_tool=facts["tool"])
    if facts.get("unknown"):
        row.update(state="needs", status_detail="Activity unknown")
    elif facts["pending"]:
        row.update(state="needs", permissions=[
            {"actionable": False, "request": {"tool_name": tool, "input": {}}}
            for tool in facts["pending"].values()])
    elif facts["open_turn"]:
        # A long tool run is quiet too; this is a prompt to look, not an approval.
        recent = idle is not None and idle < 90
        row.update(state="working" if recent else "needs", active=recent,
                   status_detail=None if recent else "Quiet · check DeepSeek")
    elif facts["failed"]:
        row.update(state="needs", status_detail="Turn failed · check DeepSeek")
    return row


def summarize(lines, now):
    facts = parse(lines)
    return _row(facts, now) if facts else None


def processes():
    """Running DeepSeek Harness processes and the session locks they hold."""
    import psutil
    from .codex_sessions import open_files
    result = []
    for process in psutil.process_iter(["pid", "name"]):
        if (process.info.get("name") or "").lower() not in _NAMES:
            continue
        try:
            if Path(process.exe()).name.lower() not in _NAMES:
                continue
            files = [path for path in open_files(process)
                     if path.replace("\\", "/").endswith("/session.lock")]
            if files:
                result.append({"pid": process.pid, "created": process.create_time(), "files": files})
        except (psutil.Error, OSError):
            continue
    return result


class Scanner:
    def __init__(self, process_source=processes):
        self.process_source = process_source
        self.logs = {}
        self.previous = {}

    def _facts(self, folder):
        """Reread a log only when it changes; a turn can run to tens of MB."""
        try:
            log = max(folder.glob("session.v*.jsonl.zstd"), default=None)
            status = log.stat() if log else None
        except OSError:
            log = status = None
        known = self.logs.get(folder)
        if status is None or status.st_size > MAX_LOG:
            return known[1] if known else _unknown(folder.name)
        stamp = (str(log), status.st_size, status.st_mtime_ns)
        if known and known[0] == stamp:
            return known[1]
        try:
            data = _decompress(log.read_bytes())
        except OSError:
            data = None
        if data is None:
            # Keep the last good reading; the stamp stays stale so it is retried.
            return known[1] if known else _unknown(folder.name)
        facts = parse(data.splitlines())
        self.logs[folder] = (stamp, facts)
        return facts

    def scan(self, now=None):
        now = time.time() if now is None else now
        owners = {}
        for process in self.process_source():
            for raw in process["files"]:
                path = Path(raw)
                if path.name == "session.lock" and path.parent.parent.parent.name == "sessions":
                    owners.setdefault(path.parent, []).append(process)
        rows = {}
        for folder, candidates in owners.items():
            if len(candidates) != 1:
                continue  # Ambiguous ownership cannot identify the session's app.
            facts = self._facts(folder)
            if not facts:
                continue
            row = _row(facts, now)
            row.update(pid=candidates[0]["pid"], process_created=candidates[0]["created"])
            rows[row["id"]] = row
        for folder in [folder for folder in self.logs if folder not in owners]:
            del self.logs[folder]
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

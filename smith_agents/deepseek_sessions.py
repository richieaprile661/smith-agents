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

from .codex_sessions import count as _count
from .hermes_sessions import _text, keep_closed, project_name

try:  # Session logs are zstd, which Python has had built in since 3.14.
    from compression import zstd
except ImportError:
    zstd = None

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
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def home():
    return Path.home() / ".dsh"


def can_read_logs():
    return zstd is not None


def _frames(raw):
    """Decompress every whole frame at the start of `raw`: (chunks, bytes used).

    The app writes one small frame per record, thousands to a log. Taking them
    one at a time is linear; handing the lot to zstd.decompress copies the rest
    of the buffer after every frame."""
    view, used, chunks = memoryview(raw), 0, []
    while used < len(view):
        try:
            size = zstd.get_frame_size(view[used:])
            chunks.append(zstd.decompress(view[used:used + size]))
        except (zstd.ZstdError, ValueError):
            break  # A frame still being written, or not a log at all.
        used += size
    return chunks, used


class Log:
    """Follows one append-only log, handing back only the lines added since."""

    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.stamp = None
        self.rest = b""

    def read(self):
        """(new lines, restarted), or None while nothing can be read.

        `restarted` means the lines begin at the log's first record, so
        whatever was folded from earlier reads is to be discarded."""
        if zstd is None:
            return None
        try:
            status = self.path.stat()
            stamp = (status.st_size, status.st_mtime_ns)
            if stamp == self.stamp:
                return [], False
            if status.st_size <= self.offset:
                self.offset = 0  # Replaced or truncated: not the log that was followed.
            if status.st_size - self.offset > MAX_LOG:
                return None
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                raw = stream.read(status.st_size - self.offset)
                if self.offset and raw[:4] != _ZSTD_MAGIC[:len(raw[:4])]:
                    self.offset = 0  # Not a frame boundary any more: replaced.
                    stream.seek(0)
                    raw = stream.read(status.st_size)
        except OSError:
            return None
        chunks, used = _frames(raw)
        restarted = self.offset == 0
        if restarted and not used:
            return None  # Unreadable for now; the next change is tried from the start.
        if restarted:
            self.rest = b""
        lines = (self.rest + b"".join(chunks)).split(b"\n")
        self.rest = lines.pop()
        self.offset += used
        # A frame caught mid-write leaves the stamp stale, so it is tried again.
        self.stamp = stamp if used == len(raw) else None
        return lines, restarted


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


def header(lines):
    """A log's first record, if it opens a session."""
    head = _record(lines[0]) if lines else None
    if not head or head.get("type") != "session":
        return None
    return head if isinstance(head.get("id"), str) and head["id"].strip() else None


def event(line, wanted):
    """(type, record, data) for a record of a wanted type, read only if it is one."""
    kind = _TYPE.search(line[:96])
    if not kind or kind[1] not in wanted:
        return None
    record = _record(line)
    data = record.get("data") if record else None
    return (record.get("type"), record, data) if isinstance(data, dict) else None


def reply(data):
    """A reply's message source and usage, as dicts whatever was written."""
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    source = message.get("source") if isinstance(message.get("source"), dict) else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return message, source, usage


def _facts(session, cwd="", since=0, **extra):
    return dict(session=session, cwd=cwd, since=since, title=None, model=None, tokens=None,
                capacity=None, request=None, message=None, tool=None, open_turn=False,
                failed=False, pending={}, tail=[], at=None, **extra)


def _apply(facts, line, wanted=_RARE | _BULK):
    """Fold one record into a session's facts."""
    stamp = _TIME.search(line[:160])
    if stamp:
        facts["at"] = int(stamp[1]) / 1000.0
    found = event(line, wanted)
    if not found:
        return
    kind, _, data = found
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
        message, source, usage = reply(data)
        facts["model"] = _text(source.get("model"), 160) or facts["model"]
        text = _said(message.get("content"))
        if text:
            facts["message"] = text
            facts["tail"].append(("text", text[:160]))
        counts = [_count(usage.get(key)) for key in
                  ("inputTokens", "cacheReadTokens", "cacheWriteTokens")]
        if counts[0] is not None:
            facts["tokens"] = sum(count or 0 for count in counts)
    elif kind == "tool/call":
        name = _text(data.get("name"), 160)
        if name:
            facts["tool"] = name
            facts["tail"].append(("cmd", name))
    del facts["tail"][:-8]


def parse(lines):
    """The facts of one main session's log, or None if it is not one."""
    head = header(lines)
    if not head or head.get("parentSession") or head.get("delegationDepth"):
        return None  # A helper belongs to its parent session, which has its own row.
    facts = _facts(head["id"], head["cwd"] if isinstance(head.get("cwd"), str) else "",
                   (_count(head.get("createdAt")) or 0) / 1000.0)
    bulk_from = len(lines) - TAIL
    for index in range(1, len(lines)):
        _apply(facts, lines[index], _RARE | _BULK if index >= bulk_from else _RARE)
    return facts


def _row(facts, now):
    idle = max(0, now - facts["at"]) if facts["at"] else None
    row = dict(provider="deepseek", id="deepseek:" + facts["session"], session_id=facts["session"],
               name=project_name(facts["cwd"]) or facts["title"] or "DeepSeek session",
               session_name=facts["title"], cwd=facts["cwd"], since=facts["since"] or None,
               entrypoint="deepseek-desktop", model=facts["model"], sub=False, state="done",
               status_detail=None, idle=idle, active=False,
               stale=idle is not None and idle > 1800, can_terminate=False,
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
    facts = parse(list(lines))
    return _row(facts, now) if facts else None


def processes():
    """Running DeepSeek Harness processes and the session locks they hold."""
    if not (home() / "sessions").is_dir():
        return []  # Never used here: nothing to look for among the processes.
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


def newest_log(folder):
    try:
        return max(folder.glob("session.v*.jsonl.zstd"), default=None)
    except OSError:
        return None


class Scanner:
    def __init__(self, process_source=processes):
        self.process_source = process_source
        self.logs = {}      # session folder -> [Log, facts]
        self.previous = {}

    def _facts(self, folder):
        """A session's facts, folding in only what its log gained since last time."""
        path = newest_log(folder)
        known = self.logs.get(folder)
        if path is None:
            return known[1] if known else _facts(folder.name, unknown=True)
        if not known or known[0].path != path:
            known = self.logs[folder] = [Log(path), _facts(folder.name, unknown=True)]
        read = known[0].read()
        if read is None:
            return known[1]  # Keep the last good reading until the log reads again.
        lines, restarted = read
        if restarted:
            known[1] = parse(lines)
        elif known[1]:
            for line in lines:
                _apply(known[1], line)
        return known[1]

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
        self.previous = keep_closed(self.previous, rows, now)
        return list(self.previous.values())


_scanner = Scanner()


def list_agents():
    return _scanner.scan()

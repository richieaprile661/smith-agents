"""Saved DeepSeek Harness usage: token totals from session logs.

These are counts the app wrote on this computer, never an account quota, a
balance, or a price. No credentials are read and nothing is requested.
"""
from pathlib import Path
import time

from . import hermes_usage
from .deepseek_sessions import MAX_LOG, _TYPE, _count, _decompress, _record, _text, can_read_logs, home

SESSION_LIMIT = 100
MODEL_LIMIT = 100
_COUNTED = frozenset((b"assistant/message", b"tool/call", b"user/message", b"session/title"))
_PARTS = (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens"),
          ("cacheReadTokens", "cache_read_tokens"), ("cacheWriteTokens", "cache_write_tokens"))


class Unavailable(Exception):
    pass


def _blank(**identity):
    return dict(identity, input_tokens=0, output_tokens=0, cache_read_tokens=0,
                cache_write_tokens=0, tokens=0, reasoning_tokens=None, api_call_count=0,
                tool_call_count=0, message_count=0, estimated_cost_usd=None)


def totals_and_replies(lines):
    """One log's totals and each reply's (time, tokens), or None without a header."""
    lines = list(lines)
    head = _record(lines[0]) if lines else None
    if not head or head.get("type") != "session":
        return None
    identity = head.get("id")
    if not isinstance(identity, str) or not identity.strip():
        return None
    session = _blank(id=identity, title="", model="",
                     cwd=_text(head.get("cwd"), 250), started_at=(_count(head.get("createdAt")) or 0) / 1000.0)
    models, replies = {}, []
    for line in lines[1:]:
        kind = _TYPE.search(line[:96])
        if not kind or kind[1] not in _COUNTED:
            continue
        record = _record(line)
        data = record.get("data") if record else None
        if not isinstance(data, dict):
            continue
        kind = record.get("type")
        if kind == "session/title":
            session["title"] = _text(data.get("title"), 250) or session["title"]
        elif kind == "user/message":
            session["message_count"] += 1
        elif kind == "tool/call":
            session["tool_call_count"] += 1
        elif kind == "assistant/message":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            source = message.get("source") if isinstance(message.get("source"), dict) else {}
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            name = _text(source.get("model"), 250) or "Unknown model"
            parts = {ours: _count(usage.get(theirs)) or 0 for theirs, ours in _PARTS}
            # Cache reads and writes are tokens of their own here, not a share of input.
            total = _count(usage.get("totalTokens"))
            total = sum(parts.values()) if total is None else total
            model = models.setdefault(name, _blank(
                model=name, billing_provider=_text(source.get("provider"), 250), task=""))
            for target in (session, model):
                for key, value in parts.items():
                    target[key] += value
                target["tokens"] += total
                target["api_call_count"] += 1
                target["message_count"] += 1
            session["model"] = name
            replies.append(((_count(record.get("time")) or 0) / 1000.0, total))
    session["models"] = [models[name] for name in sorted(models)][:MODEL_LIMIT]
    session["models_limited"] = len(models) > MODEL_LIMIT
    return session, replies


def _with_today(parsed, today_start):
    session, replies = parsed
    return dict(session, today_tokens=sum(tokens for at, tokens in replies if at >= today_start))


def totals(lines, today_start):
    parsed = totals_and_replies(lines)
    return _with_today(parsed, today_start) if parsed else None


class Reader:
    """Reads each saved log once, and again only after it changes."""

    def __init__(self):
        self.logs = {}

    def _parsed(self, log):
        try:
            status = log.stat()
        except OSError:
            return None
        stamp = (status.st_size, status.st_mtime_ns)
        known = self.logs.get(log)
        if known and known[0] == stamp:
            return known[1]
        parsed = None
        if status.st_size <= MAX_LOG:
            try:
                data = _decompress(log.read_bytes())
            except OSError:
                data = None
            if data is None:
                # Caught mid-write: keep the last good totals and try again next time.
                return known[1] if known else None
            parsed = totals_and_replies(data.splitlines())
        self.logs[log] = (stamp, parsed)
        return parsed

    def fetch(self, root=None, today_start=None):
        base = (Path(root) if root is not None else home()) / "sessions"
        if not base.is_dir():
            raise Unavailable("No saved DeepSeek sessions")
        if not can_read_logs():
            raise Unavailable("DeepSeek usage needs Python 3.14")
        if today_start is None:
            today_start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        try:
            logs = set(base.glob("*/*/session.v*.jsonl.zstd"))
        except OSError as exc:
            raise Unavailable("DeepSeek logs unavailable · retrying") from exc
        sessions = [_with_today(parsed, today_start)
                    for parsed in (self._parsed(log) for log in logs) if parsed]
        for log in [log for log in self.logs if log not in logs]:
            del self.logs[log]
        if not sessions:
            raise Unavailable("No saved DeepSeek sessions")
        sessions.sort(key=lambda session: (session["started_at"], session["id"]), reverse=True)
        return {"sessions": sessions[:SESSION_LIMIT], "limited": len(sessions) > SESSION_LIMIT,
                "updated": time.time(),
                "today_tokens": sum(session["today_tokens"] for session in sessions)}


_reader = Reader()


def fetch(root=None, today_start=None):
    return _reader.fetch(root, today_start)


def select(data, identity=None, live_ids=(), model_index=0):
    """The same paging as the other saved-usage provider, marked as DeepSeek."""
    return dict(hermes_usage.select(data, identity, live_ids, model_index), provider="deepseek")


def demo_data():
    model = dict(_blank(model="deepseek-v4-pro", billing_provider="deepseek-official", task=""),
                 input_tokens=412000, output_tokens=38000, cache_read_tokens=1960000,
                 cache_write_tokens=0, tokens=2410000, api_call_count=64, message_count=64)
    session = dict(model, id="demo-deepseek", title="Check the storefront", cwd="/demo/storefront",
                   started_at=time.time() - 1800, tool_call_count=58, message_count=70,
                   today_tokens=2410000, models=[model], models_limited=False)
    return {"sessions": [session], "limited": False, "updated": time.time(), "today_tokens": 2410000}

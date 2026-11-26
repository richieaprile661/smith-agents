"""Saved DeepSeek Harness usage: token totals from session logs.

These are counts the app wrote on this computer, never an account quota, a
balance, or a price. No credentials are read and nothing is requested.
"""
from pathlib import Path
import time

from . import hermes_usage
from .codex_sessions import count as _count
from .deepseek_sessions import Log, can_read_logs, event, header, home, reply
from .hermes_sessions import _text

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


class Tally:
    """One log's running totals, added to as its records arrive."""

    def __init__(self, head):
        self.session = _blank(id=head["id"], title="", model="", cwd=_text(head.get("cwd"), 250),
                              started_at=(_count(head.get("createdAt")) or 0) / 1000.0)
        self.models = {}
        self.replies = []       # (time, tokens), to say how much of it was today

    def add(self, line):
        found = event(line, _COUNTED)
        if not found:
            return
        kind, record, data = found
        session = self.session
        if kind == "session/title":
            session["title"] = _text(data.get("title"), 250) or session["title"]
        elif kind == "user/message":
            session["message_count"] += 1
        elif kind == "tool/call":
            session["tool_call_count"] += 1
        elif kind == "assistant/message":
            _, source, usage = reply(data)
            name = _text(source.get("model"), 250) or "Unknown model"
            parts = {ours: _count(usage.get(theirs)) or 0 for theirs, ours in _PARTS}
            # Cache reads and writes are tokens of their own here, not a share of input.
            total = _count(usage.get("totalTokens"))
            total = sum(parts.values()) if total is None else total
            model = self.models.setdefault(name, _blank(
                model=name, billing_provider=_text(source.get("provider"), 250), task=""))
            for target in (session, model):
                for key, value in parts.items():
                    target[key] += value
                target["tokens"] += total
                target["api_call_count"] += 1
                target["message_count"] += 1
            session["model"] = name
            self.replies.append(((_count(record.get("time")) or 0) / 1000.0, total))

    def totals(self, since):
        names = sorted(self.models)
        return dict(self.session, models=[self.models[name] for name in names[:MODEL_LIMIT]],
                    models_limited=len(names) > MODEL_LIMIT,
                    today_tokens=sum(tokens for at, tokens in self.replies if at >= since))


def tally(lines):
    """A whole log's tally, or None without a session header."""
    head = header(lines)
    if not head:
        return None
    result = Tally(head)
    for index in range(1, len(lines)):
        result.add(lines[index])
    return result


def totals(lines, today_start):
    counted = tally(list(lines))
    return counted.totals(today_start) if counted else None


class Reader:
    """Counts each saved log once, then only the records it gains."""

    def __init__(self):
        self.logs = {}      # log path -> [Log, Tally or None]

    def _tally(self, path):
        known = self.logs.get(path)
        if known is None:
            known = self.logs[path] = [Log(path), None]
        read = known[0].read()
        if read is not None:    # Unreadable for now keeps the last good tally.
            lines, restarted = read
            if restarted:
                known[1] = tally(lines)
            elif known[1]:
                for line in lines:
                    known[1].add(line)
        return known[1]

    def fetch(self, root=None, today_start=None):
        base = (Path(root) if root is not None else home()) / "sessions"
        if not base.is_dir():
            raise Unavailable("No saved DeepSeek sessions")
        if not can_read_logs():
            raise Unavailable("DeepSeek usage needs Python 3.14")
        since = hermes_usage.today_start() if today_start is None else today_start
        try:
            logs = set(base.glob("*/*/session.v*.jsonl.zstd"))
        except OSError as exc:
            raise Unavailable("DeepSeek logs unavailable · retrying") from exc
        sessions = [counted.totals(since) for counted in map(self._tally, logs) if counted]
        for path in [path for path in self.logs if path not in logs]:
            del self.logs[path]
        if not sessions:
            raise Unavailable("No saved DeepSeek sessions")
        sessions.sort(key=lambda session: (session["started_at"], session["id"]), reverse=True)
        return {"sessions": sessions[:SESSION_LIMIT], "limited": len(sessions) > SESSION_LIMIT,
                "updated": time.time(),
                "today_tokens": sum(session["today_tokens"] for session in sessions)}


_reader = Reader()


def fetch(root=None, today_start=None):
    return _reader.fetch(root, today_start)


def demo_data():
    model = dict(_blank(model="deepseek-v4-pro", billing_provider="deepseek-official", task=""),
                 input_tokens=412000, output_tokens=38000, cache_read_tokens=1960000,
                 cache_write_tokens=0, tokens=2410000, api_call_count=64, message_count=64)
    session = dict(model, id="demo-deepseek", title="Check the storefront", cwd="/demo/storefront",
                   started_at=time.time() - 1800, tool_call_count=58, message_count=70,
                   today_tokens=2410000, models=[model], models_limited=False)
    return {"sessions": [session], "limited": False, "updated": time.time(), "today_tokens": 2410000}

"""Bounded, provider-neutral tool activity. No UI, credentials, or model calls."""
from collections import OrderedDict
from datetime import datetime
import json
import math


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def stamp(value):
    if number(value) is not None:
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError):
        return 0


def plain(value, limit=2000):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def output(value, limit=6000):
    if isinstance(value, list):
        value = "\n".join(str(b.get("text", "")) for b in value if isinstance(b, dict) and b.get("type", "text") == "text")
    elif isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False)
    return value[-limit:] if isinstance(value, str) else ""


def tool_label(name, inputs):
    if isinstance(inputs, str):
        try:
            inputs = json.loads(inputs)
        except ValueError:
            pass
    if isinstance(inputs, dict):
        inputs = next((inputs[k] for k in ("cmd", "command", "file_path", "path", "pattern", "description")
                       if isinstance(inputs.get(k), str)), "")
    return (plain(name, 120) + " " + plain(inputs, 1800)).strip()


class Activity:
    """Tool identities, lifecycle, and cumulative usage, separate from context."""
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.data.setdefault("tools", {})
        self.data.setdefault("tool_count", 0)

    def transition(self, status, at, explicit=False):
        if at < self.data.get("state_at", 0):
            return
        self.data.update(status=status, state_at=at)
        if explicit:
            self.data["explicit"] = True
        if status == "running":
            self.data.setdefault("started_at", at)
            self.data.pop("ended_at", None)
            self.data.pop("result", None)
        else:
            if status in ("completed", "failed", "stopped"):
                self.data["ended_at"] = at
            for tool in self.data["tools"].values():
                if tool.get("status") == "running":
                    tool["status"] = "unknown"

    def start(self, key, name, inputs, at):
        if not isinstance(key, str) or not key:
            return
        tools = self.data["tools"]
        if key in tools:
            return  # replayed assistant messages must not restart a finished tool
        label = tool_label(name, inputs)
        tools[key] = {"label": label, "started_at": at, "updated_at": at, "status": "running"}
        self.data["tool_count"] += 1
        self.data["last_tool"] = label
        self.data["last_tool_at"] = at
        self.transition("running", at)
        # Retain recent identities for replay deduplication, never evict a live
        # call in favor of a completed one. Excess concurrency stays bounded.
        while len(tools) > 256:
            old = next((k for k, v in tools.items() if v["status"] != "running"), next(iter(tools)))
            del tools[old]

    def finish(self, key, result, at, failed=False):
        tool = self.data["tools"].get(key)
        if not tool or at < tool["started_at"]:
            return
        if at < tool.get("updated_at", 0):
            return
        tool.update(status="failed" if failed else "completed", ended_at=at, updated_at=at)
        self.observe_output(key, result, at)

    def observe_output(self, key, result, at):
        tool = self.data["tools"].get(key)
        if not tool or at < tool.get("started_at", 0):
            return
        if at >= self.data.get("output_at", 0):
            self.data.update(last_output=output(result), output_tool=tool["label"], output_at=at)

    def snapshot(self):
        return dict(self.data, tools={k: dict(v) for k, v in self.data["tools"].items()})


def current_tools(data):
    if data.get("status") != "running" or data.get("disconnected"):
        return []
    return [t for t in data.get("tools", {}).values() if t.get("status") == "running"]


def visible_helpers(rows, now):
    """Show current work plus up to three results per root for two minutes.

    A resumed conversation can contain years of helper transcripts. Process
    ownership of its parent does not make those historical helpers current.
    Keep ancestors of visible nested work so its hierarchy stays intact.
    """
    indexed = {row["id"]: row for row in rows}
    keep = {row["id"] for row in rows if not row.get("sub")}
    finished = {}
    for row in rows:
        if not row.get("sub"):
            continue
        root, ancestors = row, set()
        while root.get("sub") and root["id"] not in ancestors:
            ancestors.add(root["id"])
            root = indexed.get(root.get("parent"), {})
        if not root or root.get("sub") or root.get("state") == "closed":
            continue
        data = row.get("activity") or {}
        observed = max(data.get("state_at") or 0, data.get("output_at") or 0,
                       data.get("last_tool_at") or 0)
        process_start = root.get("process_created") or root.get("started_at") or 0
        if observed < process_start:
            continue
        if data.get("status") in ("completed", "failed", "stopped"):
            ended = data.get("ended_at") or data.get("state_at") or 0
            if now - ended < 120:
                finished.setdefault(root["id"], []).append((ended, row["id"], ancestors))
        else:
            keep.update(ancestors)
    for group in finished.values():
        for _, _, ancestors in sorted(group, key=lambda item: (item[0], item[1]), reverse=True)[:3]:
            keep.update(ancestors)
    return [row for row in rows if row["id"] in keep]


def merge_activity(history, observed):
    """Merge independent observations without resurrecting a completed call."""
    merged = dict(history)
    for key in ("name", "assignment", "model", "parent_task", "total_tokens", "reported_tool_count", "duration_ms", "summary", "reported_tool"):
        if observed.get(key) is not None and observed[key] != "":
            merged[key] = observed[key]
    if observed.get("state_at", 0) >= history.get("state_at", 0):
        for key in ("status", "state_at", "started_at", "ended_at", "explicit", "disconnected", "result", "latest_message"):
            if key in observed:
                merged[key] = observed[key]
        if observed.get("status") == "running":
            merged.pop("ended_at", None)
            merged.pop("result", None)
    tools = {k: dict(v) for k, v in history.get("tools", {}).items()}
    for key, tool in observed.get("tools", {}).items():
        if tool.get("updated_at", 0) >= tools.get(key, {}).get("updated_at", 0):
            tools[key] = dict(tool)
    merged["tools"] = dict(list(tools.items())[-256:])
    merged["tool_count"] = max(history.get("tool_count", 0), observed.get("tool_count", 0))
    for at_key, fields in (("output_at", ("last_output", "output_tool")), ("last_tool_at", ("last_tool",))):
        if observed.get(at_key, 0) >= history.get(at_key, 0):
            for key in (at_key, *fields):
                if key in observed:
                    merged[key] = observed[key]
    return merged


def apply_activity(row, data, now):
    row["activity"] = data
    row["assigned_task"] = data.get("assignment") or row.get("last_request")
    if data.get("latest_message") or data.get("result"):
        row["latest_message"] = data.get("result") or data["latest_message"]
    status = data.get("status")
    if row.get("state") == "closed":
        return row
    if row.get("permissions"):
        row["state"] = "needs"
    elif status:
        row["state"] = {"running": "working", "completed": "done", "failed": "done", "stopped": "done"}.get(status, "needs")
    row["active"] = row.get("state") == "working"
    row["status_detail"] = {"failed": "Failed", "stopped": "Stopped", "unknown": "Activity unknown"}.get(status)
    if data.get("loading"):
        row["status_detail"] = "Loading activity…"
    if data.get("disconnected") and status not in ("completed", "failed", "stopped"):
        row.update(state="needs", active=False, status_detail="Activity unknown · feed lost")
    return row


class JsonlReader:
    """Incremental complete-line reading, with a per-scan budget and reset."""
    MAX_LINE = 2 * 1024 * 1024
    BUDGET = 4 * 1024 * 1024

    def __init__(self):
        self.offset = 0
        self.identity = None

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
                    while raw and not raw.endswith(b"\n"):
                        raw = stream.readline(self.MAX_LINE + 1)
                    if not raw:
                        stream.seek(start)
                        break
                else:
                    try:
                        self.record(json.loads(raw))
                    except (ValueError, TypeError, AttributeError, KeyError):
                        pass
                self.offset = stream.tell()
        return self.offset >= stat.st_size, stat.st_mtime


class ClaudeTranscript(JsonlReader):
    def __init__(self):
        super().__init__()
        self.activity = Activity()
        self.assignment = self.message = self.model = None

    def record(self, row):
        if not isinstance(row, dict) or row.get("isMeta") or row.get("isApiErrorMessage"):
            return
        msg = row.get("message")
        if not isinstance(msg, dict) or msg.get("model") == "<synthetic>":
            return
        at = stamp(row.get("timestamp"))
        role = row.get("type")
        blocks = msg.get("content")
        if isinstance(blocks, str):
            blocks = [{"type": "text", "text": blocks}]
        if not isinstance(blocks, list):
            return
        texts = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                texts.append(plain(block.get("text")))
            elif role == "assistant" and kind == "tool_use":
                self.activity.start(block.get("id"), block.get("name"), block.get("input"), at)
            elif role == "user" and kind == "tool_result":
                self.activity.finish(block.get("tool_use_id"), block.get("content"), at, block.get("is_error") is True)
        text = " ".join(texts).strip()
        if role == "user" and text and not text.startswith("<") and not row.get("isCompactSummary"):
            if not self.assignment:
                self.assignment = text
            self.activity.transition("running", at)
        if role == "assistant":
            self.model = msg.get("model") or self.model
            if text:
                self.message = text
            if msg.get("stop_reason") == "end_turn":
                self.activity.transition("completed", at)


_claude_readers = OrderedDict()


def claude_transcript(path):
    reader = _claude_readers.pop(str(path), None) or ClaudeTranscript()
    caught_up, modified = reader.read(path)
    _claude_readers[str(path)] = reader
    while len(_claude_readers) > 256:
        _claude_readers.popitem(last=False)
    data = reader.activity.snapshot()
    data.update(assignment=reader.assignment, latest_message=reader.message, model=reader.model)
    if not caught_up:
        data.update(status="unknown", loading=True)
    return data, modified

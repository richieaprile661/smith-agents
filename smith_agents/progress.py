"""A Claude session's step list and turn times, from its own transcript.

Only the records that carry them are parsed: a prompt, the end of a reply, and
the calls that keep a step list. Tool output, which is nearly all of a
transcript, is passed over unread. No text of the conversation is kept.
"""
from collections import OrderedDict
from pathlib import Path
import re

from .activity import JsonlReader, plain, stamp

PLAN_TOOL = "mcp__ccd_turn__update_plan"    # The Claude desktop app's task list.
WRAP_TOOL = "mcp__ccd_turn__wrap_up"        # Its end of task: every step is done.
TODO_TOOL = "TodoWrite"                     # Claude Code's own list, sent whole.
STATUSES = ("pending", "in_progress", "completed")
MAX_STEPS = 60
# Typed at the prompt, but not a request that starts a turn.
_LOCAL = ("<local-command", "<bash-input", "<bash-stdout", "<bash-stderr")
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def _step(item):
    if not isinstance(item, dict) or not isinstance(item.get("content"), str):
        return None
    content = plain(item["content"], 200)
    status = item.get("status")
    return {"content": content, "status": status if status in STATUSES else "pending"} if content else None


def _set(steps, index, status):
    return [dict(step, status=status) if at == index else step for at, step in enumerate(steps)]


def _first(steps, status):
    return next((at for at, step in enumerate(steps) if step["status"] == status), None)


def _index(value, steps):
    """A 1-based step number as a position, or None for anything else."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= len(steps):
        return None
    return value - 1


def apply_plan(steps, inputs):
    """The list as one call of the desktop app's plan tool leaves it: a whole
    new list, the next step, or steps named by their numbers."""
    if not isinstance(inputs, dict):
        return steps
    if "items" in inputs:
        items = inputs["items"]
        if not isinstance(items, list):
            return steps
        return [step for step in map(_step, items[:MAX_STEPS]) if step]
    if inputs.get("advance") is True:
        steps = _set(steps, _first(steps, "in_progress"), "completed")
        steps = _set(steps, _first(steps, "pending"), "in_progress")
    if isinstance(inputs.get("done"), list):
        for number in inputs["done"]:
            steps = _set(steps, _index(number, steps), "completed")
    start = _index(inputs.get("start"), steps)
    if start is not None:
        steps = [dict(step, status="completed") if step["status"] == "in_progress" else step
                 for step in steps]
        steps = _set(steps, start, "in_progress")
    return steps


class Progress(JsonlReader):
    # Passing over a line costs little, so a long transcript is caught up fast.
    BUDGET = 16 * 1024 * 1024

    def __init__(self):
        super().__init__()
        self.steps = []
        self.turn_started = self.turn_ended = None
        self.seen = OrderedDict()   # Calls already applied: a replay adds nothing.

    def wanted(self, raw):
        if b'"tool_use"' in raw and (b"update_plan" in raw or b"wrap_up" in raw or b"TodoWrite" in raw):
            return True
        if b'"end_turn"' in raw:
            return True
        return b'"user"' in raw and b'"tool_result"' not in raw

    def record(self, row):
        if (not isinstance(row, dict) or row.get("isMeta") or row.get("isSidechain")
                or row.get("isApiErrorMessage") or row.get("isCompactSummary")):
            return
        message = row.get("message")
        if not isinstance(message, dict) or message.get("model") == "<synthetic>":
            return
        at = stamp(row.get("timestamp"))
        blocks = message.get("content")
        if isinstance(blocks, str):
            blocks = [{"type": "text", "text": blocks}]
        if not isinstance(blocks, list):
            return
        blocks = [block for block in blocks if isinstance(block, dict)]
        if row.get("type") == "assistant":
            for block in blocks:
                if block.get("type") == "tool_use":
                    self._call(block.get("id"), block.get("name"), block.get("input"))
            if message.get("stop_reason") == "end_turn" and at:
                self.turn_ended = at
        elif row.get("type") == "user":
            if any(block.get("type") == "tool_result" for block in blocks):
                return
            text = " ".join(block["text"] for block in blocks
                            if block.get("type") == "text" and isinstance(block.get("text"), str))
            text = _REMINDER.sub("", text).strip()
            if not text or not at or text.startswith(_LOCAL):
                return
            if text.startswith("[Request interrupted"):
                self.turn_ended = at
                return
            self.turn_started = at
            # A finished list belongs to the work before: new work starts clean.
            if self.steps and all(step["status"] == "completed" for step in self.steps):
                self.steps = []

    def _call(self, key, name, inputs):
        if name not in (PLAN_TOOL, WRAP_TOOL, TODO_TOOL):
            return
        if isinstance(key, str) and key:
            if key in self.seen:
                return
            self.seen[key] = None
            while len(self.seen) > 512:
                self.seen.popitem(last=False)
        if name == WRAP_TOOL:
            self.steps = [dict(step, status="completed") for step in self.steps]
        elif name == TODO_TOOL:
            if isinstance(inputs, dict) and isinstance(inputs.get("todos"), list):
                self.steps = apply_plan(self.steps, {"items": inputs["todos"]})
        else:
            self.steps = apply_plan(self.steps, inputs)

    def snapshot(self):
        return {"steps": [dict(step) for step in self.steps],
                "turn_started": self.turn_started, "turn_ended": self.turn_ended}


_readers = OrderedDict()


def read(path):
    """A session's progress, or None until its transcript is read through:
    a list from half way down an old conversation is not the list now."""
    key = str(path)
    reader = _readers.pop(key, None) or Progress()
    try:
        caught_up, _ = reader.read(Path(path))
    except OSError:
        return None
    _readers[key] = reader
    while len(_readers) > 64:
        _readers.popitem(last=False)
    return reader.snapshot() if caught_up else None

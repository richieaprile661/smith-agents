"""Deterministic example readings; no account or local transcript access."""
from datetime import datetime, timedelta, timezone
import time


def demo_hermes_agent():
    now = time.time()
    return dict(id="hermes:demo", session_id="demo", provider="hermes", pid=0,
                name="Research notes", cwd="~/Projects/research", entrypoint="hermes-cli",
                state="done", status_detail="Last reply saved", idle=12, since=now - 600,
                model="Hermes model", last_request="Summarize the project notes.",
                latest_message="The notes are organized and the summary is ready.",
                last_tool="read_file", tail=[], permissions=[], sub=False,
                context_tokens=None, context_capacity=None, can_terminate=False,
                active=False, stale=False)


def demo_payload():
    now = datetime.now(timezone.utc)
    return {"limits": [
        {"kind": "session", "percent": 42, "resets_at": (now + timedelta(hours=2)).isoformat()},
        {"kind": "weekly_all", "percent": 68, "resets_at": (now + timedelta(days=3)).isoformat()},
        {"kind": "weekly_scoped", "percent": 23, "resets_at": (now + timedelta(days=3)).isoformat(),
         "scope": {"model": {"display_name": "Opus"}}},
    ]}


def demo_stats():
    return {"sessions": 12, "turns": 184, "tokens": 1472000, "tools": 73,
            "per_turn": 8000, "projects": 3, "days": 14,
            "spark": [3, 7, 2, 14, 8, 11, 20, 4, 15, 24, 18, 11, 25, 22]}

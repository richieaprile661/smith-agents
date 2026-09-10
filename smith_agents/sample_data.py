"""Deterministic example readings; no account or local transcript access."""
from datetime import datetime, timedelta, timezone


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

"""Read Codex account usage through its official app-server protocol.

Codex owns authentication. This client requests account readings only: it never
starts a conversation, runs a model, answers approvals, or reads credential files.
"""
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time


class Unavailable(Exception):
    pass


def executable():
    """Prefer a running native client, including the extension's bundled CLI."""
    import psutil
    for process in psutil.process_iter(["name"]):
        if (process.info.get("name") or "").lower() not in ("codex", "codex.exe"):
            continue
        try:
            path = Path(process.exe())
            if path.name.lower() in ("codex", "codex.exe"):
                return str(path)
        except psutil.Error:
            continue
    found = shutil.which("codex")
    if found and Path(found).suffix.lower() not in (".cmd", ".bat", ".ps1"):
        return found
    if found and sys.platform == "win32":
        # npm's .cmd shim needs a shell; use its packaged native executable
        # directly, preserving argument boundaries and avoiding a console flash.
        npm = Path(found).parent / "node_modules" / "@openai"
        for path in sorted(npm.glob("codex*/**/codex.exe")):
            if path.is_file():
                return str(path)
    if sys.platform == "darwin":
        for path in (Path.home()/".local/bin/codex", Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex")):
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
    for directory in (".vscode", ".vscode-insiders", ".cursor"):
        root = Path.home() / directory / "extensions"
        candidates = sorted(root.glob("openai.chatgpt-*/bin/*/codex*"), reverse=True)
        for path in candidates:
            if path.name == ("codex.exe" if sys.platform == "win32" else "codex") and path.is_file():
                return str(path)
    raise Unavailable("Open Codex or install the Codex CLI to read usage.")


class AccountClient:
    def __init__(self, command=None, timeout=20):
        self.timeout = timeout
        self.events = queue.Queue(maxsize=32)
        self.sequence = 0
        self.child = subprocess.Popen(command or [executable(), "app-server"], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        self.reader = threading.Thread(target=self._read, daemon=True, name="codex-usage-reader")
        self.reader.start()

    def _read(self):
        try:
            while True:
                raw = self.child.stdout.readline(2 * 1024 * 1024 + 1)
                if not raw or len(raw) > 2 * 1024 * 1024:
                    break
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(message, dict) and "id" in message:
                    try:
                        self.events.put_nowait(message)
                    except queue.Full:
                        break
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.events.put_nowait(None)
            except queue.Full:
                pass

    def send(self, message):
        try:
            self.child.stdin.write(json.dumps(message).encode() + b"\n")
            self.child.stdin.flush()
        except (OSError, ValueError):
            raise Unavailable("Codex usage connection closed.") from None

    def request(self, method, params=None):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params or {}})
        end = time.monotonic() + self.timeout
        while True:
            try:
                message = self.events.get(timeout=max(0, end - time.monotonic()))
            except queue.Empty:
                raise Unavailable("Codex usage request timed out.") from None
            if message is None:
                raise Unavailable("Codex usage connection closed.")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                detail = error.get("message", "") if isinstance(error, dict) else ""
                if any(word in detail.lower() for word in ("auth", "login", "sign in", "not logged")):
                    raise Unavailable("Sign in to Codex to read account usage.")
                if "method" in detail.lower() or "experimental" in detail.lower():
                    raise Unavailable("Update Codex to read these account statistics.")
                raise Unavailable("Codex could not return this account reading.")
            result = message.get("result")
            if not isinstance(result, dict):
                raise Unavailable("Codex returned an unrecognized account reading.")
            return result

    def close(self):
        try:
            self.child.stdin.close()
            self.child.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait(timeout=2)
        finally:
            self.reader.join(timeout=1)
            self.child.stdout.close()


def fetch():
    client = AccountClient()
    try:
        client.request("initialize", {"clientInfo": {"name": "smith_agents",
                         "title": "Agent Usage Widget", "version": "1.1.0"}})
        client.send({"method": "initialized", "params": {}})
        results = {}
        for key, method in (("limits", "account/rateLimits/read"), ("activity", "account/usage/read")):
            try:
                results[key] = client.request(method)
            except Unavailable as error:
                results[key + "_error"] = str(error)
        return results
    finally:
        client.close()


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def duration(minutes):
    if not minutes:
        return "Limit"
    if minutes % 10080 == 0:
        return "%dw" % (minutes // 10080)
    if minutes % 1440 == 0:
        return "%dd" % (minutes // 1440)
    if minutes % 60 == 0:
        return "%dh" % (minutes // 60)
    return "%dm" % minutes


def build_metrics(payload):
    buckets = payload.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        bucket = payload.get("rateLimits")
        buckets = {"codex": bucket} if isinstance(bucket, dict) else {}
    metrics = []
    for bucket_id in sorted(buckets, key=lambda key: (key != "codex", str(key))):
        bucket = buckets[bucket_id]
        if not isinstance(bucket, dict):
            continue
        for key in ("primary", "secondary"):
            window = bucket.get(key)
            if not isinstance(window, dict) or number(window.get("usedPercent")) is None:
                continue
            minutes = number(window.get("windowDurationMins"))
            label = duration(minutes)
            reset = number(window.get("resetsAt"))
            try:
                reset = datetime.fromtimestamp(reset, timezone.utc).isoformat() if reset else None
            except (OSError, ValueError, OverflowError):
                reset = None
            name = str(bucket.get("limitName") or bucket_id)
            metrics.append({"key": "codex:" + str(bucket_id) + ":" + key, "provider": "codex",
                            "label": label, "header_label": label if bucket_id == "codex" else name.rsplit("-", 1)[-1],
                            "pct": min(100, window["usedPercent"]),
                            "severity": "normal", "resets_at": reset,
                            "detail": ("Codex" if name == "codex" else name) + " · " + label, "active": True})
    return metrics


def build_stats(payload, days=14, today=None):
    """Account statistics retain Codex's definitions and null/unknown values."""
    summary = payload.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    buckets = payload.get("dailyUsageBuckets")
    values = {}
    if isinstance(buckets, list):
        for bucket in buckets:
            if not isinstance(bucket, dict) or number(bucket.get("tokens")) is None:
                continue
            try:
                date = datetime.strptime(bucket["startDate"], "%Y-%m-%d").date()
            except (KeyError, TypeError, ValueError):
                continue
            if start <= date <= today:
                values[date] = bucket["tokens"]
    known = isinstance(buckets, list)
    spark = [values.get(start + timedelta(days=i), 0) for i in range(days)] if known else []
    return {"provider": "codex", "days": days, "spark": spark,
            "spark_known": known, "tokens": sum(spark) if known else None,
            "lifetime_tokens": number(summary.get("lifetimeTokens")),
            "peak_daily_tokens": number(summary.get("peakDailyTokens")),
            "longest_turn_seconds": number(summary.get("longestRunningTurnSec")),
            "current_streak_days": number(summary.get("currentStreakDays")),
            "longest_streak_days": number(summary.get("longestStreakDays"))}

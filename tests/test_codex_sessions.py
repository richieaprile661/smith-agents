"""Codex CLI/extension compatibility and isolation, using synthetic records."""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from smith_agents import codex_sessions as codex, codex_hooks as hooks
from test_core import core
from smith_agents import tucked


def record(kind, payload, second=0):
    return {"timestamp": "2026-09-10T10:00:%02dZ" % second, "type": kind, "payload": payload}


def event(kind, second=0, **kwargs):
    return record("event_msg", dict(type=kind, **kwargs), second)


def transcript(path, session="one", source="cli", parent=None, done=False):
    meta = dict(id=session, cwd=str(path.parent), source=source, timestamp="2026-09-10T10:00:00Z")
    if parent:
        meta["parent_thread_id"] = parent
    records = [record("session_meta", meta), event("task_started", 1, turn_id="turn-1"),
               record("turn_context", {"model": "gpt-test"}),
               event("token_count", 2, info={"last_token_usage": {"input_tokens": 1200, "cached_input_tokens": 1000},
                     "total_token_usage": {"input_tokens": 900000}, "model_context_window": 200000})]
    if done:
        records.append(event("task_complete", 3, turn_id="turn-1"))
    path.write_text("".join(json.dumps(r)+"\n" for r in records), encoding="utf-8")
    return records


class TranscriptTests(unittest.TestCase):
    def test_cli_and_extension_record_formats_preserve_current_state_and_input_context(self):
        for source in ("cli", "vscode"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/"rollout-one.jsonl"
                transcript(path, source=source, done=True)
                reader = codex.Transcript()
                self.assertTrue(reader.read(path)[0])
                self.assertEqual((reader.state, reader.tokens, reader.capacity), ("done", 1200, 200000))
                with path.open("a") as f:
                    f.write(json.dumps(event("task_started", 4, turn_id="turn-2"))+"\n")
                    f.write(json.dumps(event("task_complete", 5, turn_id="turn-1"))+"\n")
                reader.read(path)
                self.assertEqual(reader.state, "working")

    def test_partial_writes_truncation_and_unknown_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"rollout-one.jsonl"
            transcript(path)
            reader = codex.Transcript(); reader.read(path)
            complete = json.dumps(event("task_complete", 3, turn_id="turn-1"))
            with path.open("a") as f: f.write(complete[:20])
            self.assertFalse(reader.read(path)[0]); self.assertEqual(reader.state, "working")
            with path.open("a") as f: f.write(complete[20:]+"\n[]\nnull\n{bad}\n")
            reader.read(path); self.assertEqual(reader.state, "done")
            path.write_text(json.dumps(record("session_meta", {"id": "replacement"}))+"\n")
            reader.read(path)
            self.assertEqual(reader.meta["id"], "replacement")
            self.assertIsNone(reader.tokens)

    def test_pagination_public_messages_and_tools_exclude_reasoning_and_system_context(self):
        reader = codex.Transcript()
        for role in ("developer", "system"):
            reader.item({"type": "message", "role": role, "content": [{"type": "input_text", "text": "internal"}]})
        reader.item({"type": "reasoning", "summary": [{"text": "private"}]})
        reader.record(event("item_completed", item={"type": "UserMessage", "content": [{"type": "text", "text": "Fix the test"}]}))
        reader.record(event("item_completed", item={"type": "AgentMessage", "content": [{"type": "text", "text": "Fixed it"}]}))
        reader.item({"type": "function_call", "name": "exec_command", "arguments": '{"cmd":"pytest -q"}'})
        self.assertEqual((reader.request, reader.message, reader.tool), ("Fix the test", "Fixed it", "exec_command pytest -q"))
        self.assertNotIn("private", str(reader.tail)); self.assertNotIn("internal", str(reader.tail))
        reader.tokens = 1200
        reader.record(record("compacted", {}))
        self.assertIsNone(reader.tokens)

    def test_invalid_context_is_unknown_and_cache_is_never_added_twice(self):
        for value in (True, -1, "12", None, 1.5):
            reader = codex.Transcript()
            reader.record(event("token_count", info={"last_token_usage": {"input_tokens": value}, "model_context_window": value}))
            self.assertIsNone(reader.tokens); self.assertIsNone(reader.capacity)


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home_patch = patch.object(codex, "home", return_value=self.root)
        self.home_patch.start(); self.addCleanup(self.home_patch.stop)

    def process(self, pid, paths, server=False, created=100):
        return dict(pid=pid, created=created, server=server, files=[str(p) for p in paths])

    def test_four_extension_threads_in_one_project_stay_independent(self):
        paths = [self.root/("rollout-%d.jsonl" % n) for n in range(4)]
        for n, path in enumerate(paths): transcript(path, session=str(n), source="vscode", done=n == 0)
        process = self.process(10, paths, True)
        scanner = codex.Scanner(lambda: [process])
        rows = scanner.scan(1000)
        self.assertEqual(len({r["id"] for r in rows}), 4)
        self.assertEqual(sum(r["state"] == "working" for r in rows), 3)
        self.assertTrue(all(not r["can_terminate"] for r in rows))
        process["files"].remove(str(paths[2]))
        rows = scanner.scan(1001)
        self.assertEqual([r["id"] for r in rows if r["state"] == "closed"], ["codex:2"])
        self.assertEqual(len([r for r in rows if r["state"] != "closed"]), 3)
        self.assertEqual(len(scanner.scan(1122)), 3)

    def test_historical_files_and_ambiguous_ownership_are_not_live_sessions(self):
        path = self.root/"rollout-one.jsonl"; transcript(path)
        self.assertEqual(codex.Scanner(lambda: []).scan(), [])
        both = [self.process(10, [path]), self.process(11, [path])]
        self.assertEqual(codex.Scanner(lambda: both).scan(), [])

    def test_helpers_attach_only_to_their_actual_parent(self):
        parent, child = self.root/"rollout-one.jsonl", self.root/"rollout-child.jsonl"
        transcript(parent); transcript(child, "child", {"subagent": {}}, parent="one")
        scanner = codex.Scanner(lambda: [self.process(10, [parent, child])])
        rows = scanner.scan()
        helper = next(r for r in rows if r["sub"])
        self.assertEqual(helper["parent"], "codex:one")
        self.assertEqual(codex.Scanner(lambda: [self.process(10, [child])]).scan(), [])

    def test_lock_index_is_read_only_and_schema_change_falls_back(self):
        folder = self.root/"sessions/2026/09/10"; folder.mkdir(parents=True)
        path = folder/"rollout-t-one.jsonl"; transcript(path)
        db = self.root/"state_5.sqlite"
        with closing(sqlite3.connect(db)) as c, c:
            c.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT)")
            c.execute("INSERT INTO threads VALUES (?, ?)", ("one", str(path)))
        before = db.read_bytes()
        self.assertEqual(codex.locked_rollouts(self.root, {"one"}), {"one": path})
        self.assertEqual(db.read_bytes(), before)
        db.unlink()
        self.assertEqual(codex.locked_rollouts(self.root, {"one"}), {"one": path})

    def test_hooks_supply_extension_detection_when_open_file_enumeration_is_unavailable(self):
        path = self.root/"rollout-one.jsonl"; transcript(path, source="vscode")
        process = self.process(10, [], True)
        owner = {k:process[k] for k in ("pid", "created", "server")}
        hooks.capture(dict(session_id="one", transcript_path=str(path), hook_event_name="PermissionRequest",
                           turn_id="turn-1", tool_name="Bash", tool_input={"command": "pytest"}), self.root, owner)
        scanner = codex.Scanner(lambda: [process]); rows = scanner.scan()
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]["state"], "needs")
        self.assertFalse(rows[0]["permissions"][0]["actionable"])
        process["created"] = 101
        self.assertEqual(codex.Scanner(lambda: [process]).scan(), [])


class HookTests(unittest.TestCase):
    def test_parallel_tools_do_not_clear_another_approval_and_children_do_not_finish_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            owner = dict(pid=10, created=100, server=True)
            payload = dict(session_id="one", turn_id="t", tool_name="Bash", tool_input={"command": "first"})
            def send(kind, second, **kwargs):
                hooks.capture(dict(payload, hook_event_name=kind, **kwargs), directory, owner, second)
            send("PermissionRequest", 1)
            send("PostToolUse", 2, tool_input={"command": "different"})
            send("SubagentStop", 3, agent_id="child")
            self.assertEqual(hooks.snapshots(Path(directory))[0]["state"], "needs")
            send("PostToolUse", 4)
            self.assertEqual(hooks.snapshots(Path(directory))[0]["state"], "working")
            send("Stop", 5)
            self.assertEqual(hooks.snapshots(Path(directory))[0]["state"], "done")
            send("PermissionRequest", 4)
            self.assertEqual(hooks.snapshots(Path(directory))[0]["state"], "done")

    def test_install_preserves_user_hooks_and_is_idempotent_without_trusting_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"hooks.json"
            original = {"description": "mine", "hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "existing"}]}]}}
            path.write_text(json.dumps(original))
            hooks.install(directory); first = path.read_bytes(); hooks.install(directory)
            self.assertEqual(path.read_bytes(), first)
            data = json.loads(first)
            self.assertEqual(data["hooks"]["Stop"][0], original["hooks"]["Stop"][0])
            self.assertEqual(json.loads((Path(directory)/"hooks.json.before-widget").read_text()), original)
            self.assertEqual(set(p.name for p in Path(directory).iterdir()), {"hooks.json", "hooks.json.before-widget"})


class ProviderIntegrationTests(unittest.TestCase):
    def test_no_claude_credentials_or_transcripts_needed_for_codex(self):
        row = dict(id="codex:one", provider="codex", state="done", cwd="", context_tokens=12,
                   context_capacity=100, model="gpt-test", entrypoint="codex-vscode")
        with patch.object(core, "_agent_transcript") as claude, patch.object(core, "git_branch", return_value=""):
            core.decorate_agents([row]); claude.assert_not_called()
        self.assertEqual(row["context_tokens"], 12)
        self.assertIn("Codex", core.agent_model_source(row))

    def test_codex_controls_cannot_terminate_shared_host_and_approval_is_provider_specific(self):
        row = dict(core.demo_agents()[1], id="codex:one", provider="codex", can_terminate=False,
                   entrypoint="codex-vscode", permissions=[{"actionable": False, "request": {"tool_name": "Bash", "input": {}}}])
        with patch.object(core.platform, "terminate_agent") as kill:
            self.assertFalse(core.terminate_agent(row)); kill.assert_not_called()
        self.assertEqual(core.agent_status(row), "Answer in Codex")
        self.assertEqual(core.agent_window_action(row), ("open", "Open window"))
        image, boxes = core.render_console([], None, {}, [row], 0, open_id=row["id"])
        self.assertNotIn("kill", [box[0] for box in boxes])
        self.assertNotIn("permission", [box[0] for box in boxes])
        for side in tucked.EDGES:
            image, boxes, layout = tucked.render([row], [], None, 0, side=side, selected=row["id"])
            self.assertTrue(any(b[0] == "peek-agent" for b in boxes))
            self.assertNotIn("kill", [b[0] for b in boxes])


if __name__ == "__main__":
    unittest.main()

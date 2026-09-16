"""Provider event attribution, recovery, and the real widget activity panels."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import psutil

from smith_agents.activity import Activity, ClaudeTranscript, current_tools, apply_activity, merge_activity, visible_helpers
from smith_agents.claude_activity import TaskStream, capture_status, read
from smith_agents import codex_hooks, codex_sessions
from test_core import core
from test_codex_sessions import transcript, record, event


def claude(role, blocks, at, **message):
    return {"type": role, "timestamp": at, "message": dict(content=blocks, **message)}


class ToolActivityTests(unittest.TestCase):
    def test_visible_helpers_exclude_previous_run_and_bound_recent_results_for_both_providers(self):
        for provider in ("claude", "codex"):
            parent = dict(id="parent", provider=provider, started_at=100, state="working")
            def child(key, status, at):
                return dict(id=key, sub=True, parent="parent", activity=dict(status=status, state_at=at))
            rows = [parent, child("previous-run", "completed", 99), child("old-unknown", "unknown", 99),
                    child("long-running", "running", 101), child("permission", "running", 101),
                    child("expired", "completed", 180)]
            rows += [child("result-"+str(n), "completed", 290+n) for n in range(8)]
            rows[4]["permissions"] = [{"request": {}}]
            shown = visible_helpers(rows, 310)
            self.assertEqual([r["id"] for r in shown],
                             ["parent", "long-running", "permission", "result-5", "result-6", "result-7"])
            self.assertEqual([r["id"] for r in visible_helpers(rows, 500)],
                             ["parent", "long-running", "permission"])

    def test_visible_nested_work_keeps_its_ancestor_and_rejects_orphans_and_cycles(self):
        rows = [dict(id="root", state="working", started_at=100),
                dict(id="parent", sub=True, parent="root", activity=dict(status="completed", state_at=110)),
                dict(id="child", sub=True, parent="parent", activity=dict(status="running", state_at=200)),
                dict(id="orphan", sub=True, parent="missing", activity=dict(status="running", state_at=200)),
                dict(id="cycle", sub=True, parent="cycle", activity=dict(status="running", state_at=200))]
        self.assertEqual([r["id"] for r in visible_helpers(rows, 500)], ["root", "parent", "child"])

    def test_merge_does_not_resurrect_finished_call_or_previous_result(self):
        a = Activity()
        a.start("tool", "Read", "file", 10)
        earlier = a.snapshot()
        a.finish("tool", "read", 11)
        self.assertFalse(current_tools(merge_activity(a.snapshot(), earlier)))
        a.transition("completed", 12)
        a.data["result"] = "Previous turn"
        merged = merge_activity(a.snapshot(), {"status": "running", "state_at": 13})
        self.assertNotIn("result", merged)
        self.assertNotIn("ended_at", merged)

    def test_parallel_results_and_duplicate_messages_match_only_their_call(self):
        a = Activity()
        a.start("first", "Bash", {"command": "first"}, 10)
        a.start("second", "Read", {"file_path": "second"}, 11)
        a.finish("unrelated", "not ours", 12)
        self.assertNotIn("last_output", a.data)
        a.finish("first", "first output", 13)
        a.start("first", "Bash", {"command": "first"}, 10)
        self.assertEqual(a.data["tool_count"], 2)
        self.assertEqual([t["label"] for t in current_tools(a.data)], ["Read second"])
        a.finish("second", "second output", 14, failed=True)
        self.assertFalse(current_tools(a.data))
        self.assertEqual(a.data["output_tool"], "Read second")
        self.assertEqual(a.data["last_output"], "second output")

    def test_ended_disconnected_and_stale_events_do_not_claim_current_work(self):
        a = Activity()
        a.start("cmd", "Bash", "sleep 300", 10)
        self.assertTrue(current_tools(a.data))
        a.data["disconnected"] = True
        self.assertFalse(current_tools(a.data))
        a.data.pop("disconnected")
        a.transition("completed", 500, explicit=True)
        a.transition("running", 100)
        self.assertFalse(current_tools(a.data))
        self.assertEqual(a.data["status"], "completed")

    def test_output_and_history_are_bounded(self):
        a = Activity()
        for n in range(600):
            a.start(str(n), "Read", "file", n)
            a.finish(str(n), "x" * 10000, n)
        self.assertLessEqual(len(a.data["tools"]), 256)
        self.assertLessEqual(len(a.data["last_output"]), 6000)
        self.assertEqual(a.data["tool_count"], 600)


class ClaudeActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stream = TaskStream(self.root)

    def send(self, subtype, task="child", at=100, **extra):
        self.stream.record(dict(type="system", subtype=subtype, task_id=task,
                                session_id="parent", timestamp=at, **extra))

    def test_child_messages_before_task_started_are_correlated_including_nested_spawn(self):
        self.stream.record(dict(claude("assistant", [{"type": "tool_use", "id": "nested-spawn", "name": "Agent",
                                                       "input": {"prompt": "Check nested work"}}], 101),
                                session_id="parent", parent_tool_use_id="spawn"))
        self.send("task_started", tool_use_id="spawn", task_type="local_agent")
        self.send("task_started", task="nested", at=102, tool_use_id="nested-spawn", task_type="local_agent")
        tasks = read(self.root, {"id": "parent"})
        self.assertEqual(tasks["nested"]["parent_task"], "child")
        self.assertEqual(tasks["nested"]["assignment"], "Check nested work")
        self.assertEqual(tasks["child"]["tool_count"], 1)

    def test_old_observer_close_cannot_disconnect_new_owner(self):
        self.stream.owner = {"pid": 1, "started": 1}
        self.send("task_started", at=100, tool_use_id="spawn")
        newer = TaskStream(self.root, {"pid": 2, "started": 2})
        newer.record(dict(type="system", subtype="task_started", task_id="child", session_id="parent",
                          timestamp=200, tool_use_id="spawn"))
        self.stream.close()
        with patch("smith_agents.claude_activity.psutil.Process") as process:
            process.side_effect = lambda pid: SimpleNamespace(create_time=lambda: pid, is_running=lambda: True)
            tasks = read(self.root, {"id": "parent"})
        self.assertFalse(tasks["child"]["disconnected"])

    def test_windows_parent_identity_does_not_hide_current_helpers(self):
        from test_session_windows import win
        from smith_agents.activity import visible_helpers
        started = 1_800_000_000
        filetime = (started + 11644473600) * 10000000
        session = dict(sessionId="parent", pid=123, procStart=filetime, cwd=str(self.root))
        sessions = self.root / "sessions"
        sessions.mkdir()
        (sessions / "parent.json").write_text(json.dumps(session))
        capture_status({"session_id": "parent", "tasks": [{"id": "child", "status": "running"}]},
                       self.root / "widget-context", started + 10)
        with patch.object(core, "platform", win), patch.object(win, "pid_alive", return_value=True), \
             patch.object(core, "SESSIONS_DIR", str(sessions)), \
             patch.object(core, "CLAUDE_DIR", str(self.root)), \
             patch.object(core, "PROJECTS_DIR", str(self.root)), \
             patch("time.time", return_value=started + 15):
            rows = visible_helpers(core._list_claude_agents(), started + 15)
        self.assertEqual([r["id"] for r in rows], ["parent", "agent-child"])
        self.assertEqual(rows[0]["started_at"], filetime)
        self.assertEqual(rows[0]["process_created"], started)
        self.assertEqual(rows[1]["state"], "working")

    def test_stream_owner_checks_unix_time_and_rejects_reused_or_dead_pid(self):
        self.stream.owner = {"pid": 123, "started": 100.25}
        self.send("task_started", at=101, tool_use_id="spawn")
        for actual, running, expected in ((100.25, True, "running"), (200, True, "unknown"),
                                          (100.25, False, "unknown")):
            with patch("smith_agents.claude_activity.psutil.Process") as process:
                process.return_value.create_time.return_value = actual
                process.return_value.is_running.return_value = running
                self.assertEqual(read(self.root, {"id": "parent"})["child"]["status"], expected)
        with patch("smith_agents.claude_activity.psutil.Process", side_effect=psutil.NoSuchProcess(123)):
            self.assertEqual(read(self.root, {"id": "parent"})["child"]["status"], "unknown")

    def test_empty_status_snapshot_clears_helpers_but_missing_metadata_does_not(self):
        capture_status({"session_id": "parent", "tasks": [{"id": "child", "status": "running"}]}, self.root, 110)
        capture_status({"session_id": "parent"}, self.root, 115)
        self.assertIn("child", read(self.root, {"id": "parent"}, 116))
        capture_status({"session_id": "parent", "tasks": []}, self.root, 120)
        self.assertEqual(read(self.root, {"id": "parent"}, 121), {})
        self.assertEqual(read(self.root, {"id": "parent"}, 200), {})

    def test_recent_transcript_is_not_crowded_out_by_256_old_helpers(self):
        parent = dict(id="parent", cwd=str(self.root), pid=123, state="working", started_at=100)
        folder = self.root / core._encode_path(str(self.root)) / "parent" / "subagents"
        folder.mkdir(parents=True)
        for index in range(257):
            path = folder / ("agent-%03d.jsonl" % index)
            at = 200 if index == 256 else 10
            path.write_text(json.dumps(claude("user", "Assigned work", at)) + "\n")
            os.utime(path, (at, at))
        # Force the new helper to be last regardless of filesystem order.
        with os.scandir(folder) as scan:
            entries = sorted(scan, key=lambda entry: entry.name)
        with patch.object(core, "PROJECTS_DIR", str(self.root)), \
             patch.object(core, "CLAUDE_DIR", str(self.root)), \
             patch.object(core.os, "scandir", return_value=iter(entries)), \
             patch("time.time", return_value=210):
            rows = core.list_subagents(parent)
        from smith_agents.activity import visible_helpers
        shown = visible_helpers([parent, *rows], 210)
        self.assertEqual([r["id"] for r in shown], ["parent", "agent-256"])
        self.assertLessEqual(len(rows), 256)


    def test_task_stream_uses_child_messages_and_retains_completion(self):
        self.stream.record(dict(claude("assistant", [{"type": "tool_use", "id": "spawn", "name": "Agent",
                                                       "input": {"prompt": "Test lifecycle"}}], 90), session_id="parent"))
        self.send("task_started", tool_use_id="spawn", task_type="local_agent", description="Write tests")
        self.stream.record(dict(claude("assistant", [{"type": "tool_use", "id": "call", "name": "Bash",
                                                       "input": {"command": "pytest"}}], 101, model="child-model"),
                                session_id="parent", parent_tool_use_id="spawn"))
        self.send("task_progress", at=400, usage={"total_tokens": 14000, "tool_uses": 3, "duration_ms": 300000})
        data = read(self.root, {"id": "parent"})["child"]
        self.assertEqual(data["assignment"], "Test lifecycle")
        self.assertEqual(data["status"], "running")
        self.assertEqual(current_tools(data)[0]["label"], "Bash pytest")
        self.stream.record(dict(claude("user", [{"type": "tool_result", "tool_use_id": "call", "content": "12 passed"}], 401),
                                session_id="parent", parent_tool_use_id="spawn"))
        self.send("task_notification", at=402, status="completed", summary="Done")
        data = read(self.root, {"id": "parent"})["child"]
        self.assertEqual(data["last_output"], "12 passed")
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["total_tokens"], 14000)
        self.assertNotIn("context_tokens", data)
        self.assertFalse(current_tools(data))

    def test_parent_and_other_session_cannot_supply_child_output(self):
        self.send("task_started", tool_use_id="spawn", task_type="local_agent")
        for session, parent in (("other", "spawn"), ("parent", None), ("parent", "different")):
            self.stream.record(dict(claude("assistant", [{"type": "text", "text": "wrong message"}], 101),
                                    session_id=session, parent_tool_use_id=parent))
        self.assertNotIn("latest_message", read(self.root, {"id": "parent"})["child"])
        self.send("task_started", task="bash", task_type="local_bash")
        self.send("task_progress", task="bash", usage={})
        self.assertNotIn("bash", read(self.root, {"id": "parent"}))

    def test_disconnect_and_process_resume_do_not_reuse_running_state(self):
        self.send("task_started", task_type="local_agent")
        self.stream.close()
        data = read(self.root, {"id": "parent"})["child"]
        self.assertEqual(data["status"], "unknown")
        self.assertTrue(data["disconnected"])
        self.assertEqual(read(self.root, {"id": "parent", "started_at": time.time()+10}), {})

    def test_statusline_retains_description_and_reports_stale_feed(self):
        capture_status({"session_id": "parent", "tasks": [{"id": "child", "status": "running",
                        "description": "Review changes", "model": "haiku", "tokenCount": 123, "startTime": 100000}]}, self.root, 110)
        data = read(self.root, {"id": "parent"}, 115)["child"]
        self.assertEqual(data["name"], "Review changes")
        self.assertNotIn("total_tokens", data)
        self.assertEqual(data["started_at"], 100)
        self.assertEqual(read(self.root, {"id": "parent"}, 150)["child"]["status"], "unknown")

    def test_statusline_refresh_does_not_renew_finished_helper_lifetime(self):
        payload = {"session_id": "parent", "tasks": [{"id": "child", "status": "completed", "startTime": 100000}]}
        capture_status(payload, self.root, 110)
        capture_status(payload, self.root, 300)
        data = read(self.root, {"id": "parent"}, 301)["child"]
        self.assertEqual(data["ended_at"], 110)
        self.assertEqual(data["state_at"], 110)
        payload["tasks"][0]["status"] = "running"
        capture_status(payload, self.root, 310)
        payload["tasks"][0]["status"] = "completed"
        capture_status(payload, self.root, 320)
        self.assertEqual(read(self.root, {"id": "parent"}, 321)["child"]["ended_at"], 320)

    def test_transcript_partial_line_results_and_reset(self):
        path = self.root/"agent-one.jsonl"
        lines = [claude("user", "Test the changes", 10),
                 claude("assistant", [{"type": "thinking", "thinking": "private"},
                                      {"type": "tool_use", "id": "t", "name": "Read", "input": {"file_path": "file.py"}}], 11)]
        path.write_text("".join(json.dumps(r)+"\n" for r in lines))
        reader = ClaudeTranscript(); self.assertTrue(reader.read(path)[0])
        tail = json.dumps(claude("user", [{"type": "tool_result", "tool_use_id": "t", "content": "file output"}], 12))
        with path.open("a") as f: f.write(tail[:20])
        self.assertFalse(reader.read(path)[0])
        with path.open("a") as f: f.write(tail[20:]+"\n")
        reader.read(path)
        self.assertFalse(current_tools(reader.activity.data))
        self.assertNotIn("private", str(reader.activity.data))
        self.assertEqual(reader.assignment, "Test the changes")
        path.write_text(json.dumps(claude("user", "Replacement", 20))+"\n")
        reader.read(path)
        self.assertEqual(reader.assignment, "Replacement")
        self.assertNotIn("last_output", reader.activity.data)

    def test_quiet_and_completed_helpers_remain_in_list(self):
        parent = dict(id="parent", cwd=str(self.root), pid=123, state="working", started_at=0)
        folder = self.root/core._encode_path(str(self.root))/"parent"/"subagents"
        folder.mkdir(parents=True)
        for key, done in (("running", False), ("done", True)):
            rows = [claude("user", "Assigned work", 10), claude("assistant", [{"type": "text", "text": "Working"}], 11,
                                                                           stop_reason="end_turn" if done else None)]
            (folder/("agent-"+key+".jsonl")).write_text("".join(json.dumps(r)+"\n" for r in rows))
        with patch.object(core, "PROJECTS_DIR", str(self.root)), patch.object(core, "CLAUDE_DIR", str(self.root)), \
             patch("smith_agents.core.time.time", return_value=time.time()+300):
            found = core.list_subagents(parent)
        self.assertEqual(len(found), 2)
        states = {r["id"]: r["activity"]["status"] for r in found}
        self.assertEqual(states, {"agent-running": "unknown", "agent-done": "completed"})


class CodexActivityTests(unittest.TestCase):
    def test_yielded_command_stays_running_until_matching_poll_finishes(self):
        reader = codex_sessions.Transcript()
        reader.item(dict(type="function_call", call_id="exec", name="exec_command", arguments='{"cmd":"sleep 300"}'), 1)
        reader.item(dict(type="function_call_output", call_id="exec", output="Process running with session ID 42\nOutput: working"), 2)
        self.assertEqual(current_tools(reader.activity.data)[0]["label"], "exec_command sleep 300")
        reader.item(dict(type="function_call", call_id="poll", name="write_stdin", arguments='{"session_id":42}'), 3)
        reader.item(dict(type="function_call_output", call_id="poll", output='{"session_id":42,"output":"still working"}'), 4)
        self.assertEqual(len(current_tools(reader.activity.data)), 1)
        reader.item(dict(type="function_call", call_id="end", name="write_stdin", arguments='{"session_id":42}'), 5)
        reader.item(dict(type="function_call_output", call_id="end", output='{"exit_code":0,"output":"done"}'), 6)
        self.assertFalse(current_tools(reader.activity.data))
        self.assertEqual(reader.activity.data["output_tool"], "exec_command sleep 300")

    def test_rollout_outputs_match_call_ids_and_end_current_tool(self):
        reader = codex_sessions.Transcript()
        reader.record(event("task_started", 1))
        reader.record(record("response_item", {"type": "function_call", "call_id": "t", "name": "exec_command",
                                                "arguments": '{"cmd":"pytest"}'}, 2))
        reader.record(record("response_item", {"type": "function_call_output", "call_id": "different", "output": "wrong"}, 3))
        self.assertNotIn("last_output", reader.activity.data)
        reader.record(record("response_item", {"type": "function_call_output", "call_id": "t", "output": "passed"}, 4))
        self.assertEqual(reader.activity.data["last_output"], "passed")
        self.assertFalse(current_tools(reader.activity.data))

    def test_hooks_track_parallel_tools_and_children_without_parent_completion(self):
        with tempfile.TemporaryDirectory() as d:
            owner = dict(pid=1, created=2, server=False)
            def send(event, at, **kw):
                codex_hooks.capture(dict(session_id="parent", hook_event_name=event, **kw), d, owner, at)
            send("PreToolUse", 10, tool_use_id="a", tool_name="Bash", tool_input={"command": "sleep 300"})
            send("PreToolUse", 11, tool_use_id="b", tool_name="Read", tool_input={"path": "file"})
            send("PostToolUse", 12, tool_use_id="b", tool_response="read file")
            send("SubagentStart", 13, agent_id="child", agent_type="reviewer")
            send("SubagentStop", 400, agent_id="child", last_assistant_message="Reviewed")
            parent = codex_hooks.snapshots(Path(d))[0]
            self.assertEqual(parent["state"], "working")
            self.assertEqual(current_tools(parent["activity"])[0]["label"], "Bash sleep 300")
            child = codex_hooks.child_snapshots(Path(d))[0]
            self.assertEqual((child["parent"], child["status"], child["result"]), ("parent", "completed", "Reviewed"))
            send("PostToolUse", 401, tool_use_id="a", tool_response={"exit_code": 0, "output": "done"})
            self.assertFalse(current_tools(codex_hooks.snapshots(Path(d))[0]["activity"]))

    def test_completed_child_survives_closed_handle_but_not_parent_loss(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); parent, child = root/"rollout-p.jsonl", root/"rollout-c.jsonl"
            transcript(parent, "p"); transcript(child, "c", parent="p", done=True)
            process = dict(pid=123, created=100, server=True, files=[str(parent),str(child)])
            scanner = codex_sessions.Scanner(lambda:[process])
            with patch.object(codex_sessions, "home", return_value=root):
                self.assertEqual(len(scanner.scan()), 2)
                process["files"] = [str(parent)]
                rows = scanner.scan(time.time()+400)
                self.assertEqual(len(rows), 2)
                self.assertEqual(next(r for r in rows if r["sub"])["activity"]["status"], "completed")
                process["files"] = []
                self.assertFalse(any(r["sub"] for r in scanner.scan(time.time()+401)))

    def test_lifecycle_only_child_and_pid_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); parent = root/"rollout-p.jsonl"; transcript(parent, "p")
            process = dict(pid=123, created=100, server=True, files=[str(parent)])
            owner = {k:process[k] for k in ("pid","created","server")}
            codex_hooks.capture(dict(session_id="p", hook_event_name="SubagentStart", agent_id="c", agent_type="tester"), root, owner)
            with patch.object(codex_sessions, "home", return_value=root):
                rows = codex_sessions.Scanner(lambda:[process]).scan()
                child = next(r for r in rows if r["sub"])
                self.assertEqual(child["name"], "tester")
                self.assertIsNone(child["context_tokens"])
                process["created"] = 101
                self.assertFalse(any(r["sub"] for r in codex_sessions.Scanner(lambda:[process]).scan()))


class ActivityPanelTests(unittest.TestCase):
    def row(self):
        activity = Activity()
        activity.start("t", "Bash", {"command": "pytest -q tests/test_subagent_activity.py"}, time.time()-180)
        activity.data.update(assignment="Test lifecycle behavior", total_tokens=12800)
        row = dict(core.demo_agents()[1], sub=True, id="child", parent="parent", permissions=[], context_tokens=None,
                   context_capacity=None, last_request="task")
        return apply_activity(row, activity.snapshot(), time.time())

    def test_current_tool_and_cumulative_usage_are_separate_from_context(self):
        row = self.row()
        self.assertTrue(core.agent_activity(row).startswith("Now · Bash pytest"))
        self.assertIn("in tool", core.agent_activity_stamp(row))
        self.assertIn("12.8k total tokens", core.agent_work_counts(row))
        self.assertIsNone(row["context_tokens"])
        row["activity"].update(status="unknown", disconnected=True)
        self.assertTrue(core.agent_activity(row).startswith("Last observed"))

    def test_rich_rows_and_drawers_fit_with_long_commands_and_permissions(self):
        from PIL import ImageDraw
        for approved in (False, True):
            row = self.row()
            if approved:
                row["permissions"] = [{"actionable":True,"request":{"tool_name":"Bash","input":{"command":"pytest "*50}}}]
            row["activity"].update(last_output="line\n"*20, output_tool="Bash "+"x"*2000)
            height=core.agent_row_height(row)
            image=core.console_base((core.CONSOLE_W,height)); pen=ImageDraw.Draw(image)
            boxes=core.render_row(pen,image,row,0,time.time(),False,False)
            for kind,x0,y0,x1,y1,_ in boxes:
                self.assertLessEqual(y1,height,kind)
            blocks,_,_=core.agent_drawer_layout(row)
            labels=[b[0] for b in blocks]
            self.assertIn("Assigned task",labels)
            self.assertIn("Tool output",labels)
            self.assertLessEqual(len(next(b[1] for b in blocks if b[0]=="Tool output")),12)


if __name__ == "__main__":
    unittest.main()

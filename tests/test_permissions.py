import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from smith_agents.permissions import PermissionRelay, answer_permission, endpoint_path, pending_permissions
from test_core import core


def request(request_id="request-1", tool="Bash", agent=None):
    body = {"subtype": "can_use_tool", "tool_name": tool, "input": {"command": "echo example"},
            "tool_use_id": "tool-1"}
    if agent:
        body["agent_id"] = agent
    return {"type": "control_request", "request_id": request_id, "request": body}


@unittest.skipUnless(sys.platform in ("darwin", "win32"), "Native permission relay")
class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.child = []
        self.client = []
        self.relay = PermissionRelay(self.temp.name, self.child.append, self.client.append)
        self.relay.observe({"type": "system", "subtype": "init", "session_id": "session-1"})
        self.agent = {"id": "session-1", "state": "needs"}

    def tearDown(self):
        self.relay.close()
        self.temp.cleanup()

    def test_wrong_token_cannot_list_or_answer_requests(self):
        self.relay.observe(request())
        item = pending_permissions(self.temp.name, self.agent)[0]
        for action in ("list", "allow", "deny"):
            answer = self.relay.handle(dict(item, token="wrong", action=action))
            self.assertFalse(answer["ok"])
        self.assertFalse(self.child)
        self.assertEqual(len(pending_permissions(self.temp.name, self.agent)), 1)

    def test_allow_exact_input_once_and_suppress_late_vscode_reply(self):
        self.relay.observe(request())
        item = pending_permissions(self.temp.name, self.agent)[0]
        answer = answer_permission(self.temp.name, item, "allow")
        self.assertTrue(answer["ok"])
        response = json.loads(self.child[0])["response"]
        self.assertEqual(response["response"], {"behavior": "allow", "updatedInput": {"command": "echo example"}, "toolUseID": "tool-1"})
        self.assertNotIn("updatedPermissions", response["response"])
        self.assertEqual(json.loads(self.client[0]), {"type": "control_cancel_request", "request_id": "request-1"})
        self.relay.forward_input(json.dumps({"type": "control_response", "response": {"request_id": "request-1"}}).encode())
        self.assertEqual(len(self.child), 1)
        self.assertFalse(answer_permission(self.temp.name, item, "allow")["ok"])

    def test_vscode_response_wins_and_cannot_be_overridden(self):
        self.relay.observe(request())
        item = pending_permissions(self.temp.name, self.agent)[0]
        raw = b'{"type":"control_response","response":{"request_id":"request-1","response":{"behavior":"deny"}}}\n'
        self.relay.forward_input(raw)
        self.assertEqual(self.child, [raw])
        self.assertFalse(answer_permission(self.temp.name, item, "allow")["ok"])
        self.assertFalse(pending_permissions(self.temp.name, self.agent))

    def test_response_before_observation_and_cancellation_do_not_leave_stale_prompt(self):
        self.relay.forward_input(b'{"type":"control_response","response":{"request_id":"request-1"}}\n')
        self.relay.observe(request())
        self.assertFalse(pending_permissions(self.temp.name, self.agent))
        self.relay.observe(request("request-2"))
        item = pending_permissions(self.temp.name, self.agent)[0]
        self.relay.observe({"type": "control_cancel_request", "request_id": "request-2"})
        self.assertFalse(answer_permission(self.temp.name, item, "allow")["ok"])

    def test_changed_request_and_wrong_session_cannot_be_approved(self):
        self.relay.observe(request())
        item = pending_permissions(self.temp.name, self.agent)[0]
        changed = request()
        changed["request"]["input"] = {"command": "different command"}
        self.relay.observe(changed)
        self.assertFalse(answer_permission(self.temp.name, item, "allow")["ok"])
        self.assertFalse(self.child)
        self.assertFalse(pending_permissions(self.temp.name, {"id": "other", "state": "needs"}))

    def test_subagent_and_question_requests_are_not_parent_tool_approvals(self):
        self.relay.observe(request(agent="child"))
        self.assertFalse(pending_permissions(self.temp.name, self.agent))
        child = {"id": "agent-child", "parent": "session-1", "sub": True, "state": "needs"}
        self.assertEqual(len(pending_permissions(self.temp.name, child)), 1)
        self.relay.observe(request("question", tool="AskUserQuestion"))
        item = pending_permissions(self.temp.name, self.agent)[0]
        self.assertFalse(item["actionable"])
        self.assertFalse(answer_permission(self.temp.name, item, "allow")["ok"])

    def test_deny_and_endpoint_contains_no_prompt_content(self):
        self.relay.observe(request())
        endpoint = endpoint_path(self.temp.name, "session-1")
        if os.name == "posix":
            self.assertEqual(endpoint.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("echo example", endpoint.read_text())
        item = pending_permissions(self.temp.name, self.agent)[0]
        self.assertTrue(answer_permission(self.temp.name, item, "deny")["ok"])
        self.assertEqual(json.loads(self.child[0])["response"]["response"]["behavior"], "deny")

    def test_renderer_only_offers_allow_for_real_actionable_request(self):
        row = core.demo_agents()[0]
        _, boxes = core.render_console([], {}, {}, [row], time.time())
        self.assertNotIn("permission", [box[0] for box in boxes])
        self.relay.observe(request())
        row["permissions"] = pending_permissions(self.temp.name, self.agent)
        _, boxes = core.render_console([], {}, {}, [row], time.time(), open_id=row["id"])
        self.assertIn("permission", [box[0] for box in boxes])
        self.assertIn("permission-deny", [box[0] for box in boxes])
        self.assertEqual(core.agent_action(row), ("Allow Bash?", "echo example"))

    def test_collapsed_panel_allow_deny_and_cancel_use_the_exact_request(self):
        from smith_agents import app
        for index, (kind, decision) in enumerate((('permission', None), ('permission', 'allow'),
                                                  ('permission-deny', 'deny'))):
            self.relay.observe(request(request_id='panel-' + str(index)))
            row = dict(core.demo_agents()[0], permissions=pending_permissions(self.temp.name, self.agent))
            widget = app.SmithAgentsWidget.__new__(app.SmithAgentsWidget)
            widget.agent_open = None
            widget.agents = [row]
            _, boxes = core.render_console([], None, {}, [row], time.time())
            widget.agent_rows = boxes
            # Choose the explicit bottom action, not the Approval needed link.
            box = [box for box in boxes if box[0] == kind][-1]
            row_box = next(box for box in boxes if box[0] == 'row')
            self.assertGreaterEqual(box[2], row_box[2])
            self.assertLessEqual(box[4], row_box[4])
            before = len(self.child)
            with patch.object(app.platform, 'review_permission', return_value=decision) as review, \
                 patch.object(app, 'answer_permission', side_effect=lambda directory, item, choice:
                              answer_permission(self.temp.name, item, choice)) as answer, \
                 patch.object(app, 'terminate_agent') as terminate, patch.object(widget, '_repaint'):
                self.assertTrue(widget._on_console_click(SimpleNamespace(
                    x=(box[1]+box[3])/2, y=(box[2]+box[4])/2)))
                self.assertIsNone(widget.agent_open)
                terminate.assert_not_called()
                if kind == 'permission':
                    review.assert_called_once_with(row, row['permissions'][0])
                else:
                    review.assert_not_called()
                if decision:
                    answer.assert_called_once()
                    self.assertEqual(len(self.child), before + 1)
                    sent = json.loads(self.child[-1])['response']
                    self.assertEqual(sent['request_id'], 'panel-' + str(index))
                    self.assertEqual(sent['response']['behavior'], decision)
                    self.assertFalse(pending_permissions(self.temp.name, self.agent))
                else:
                    answer.assert_not_called()
                    self.assertEqual(len(self.child), before)
                    self.assertTrue(pending_permissions(self.temp.name, self.agent))
                    self.relay.observe({'type': 'control_cancel_request', 'request_id': 'panel-' + str(index)})

    def test_yellow_panel_shows_actions_only_for_supported_live_requests(self):
        row = core.demo_agents()[0]
        for tool, actionable in ((None, False), ('AskUserQuestion', False), ('Bash', True)):
            if tool:
                self.relay.observe(request(request_id=tool, tool=tool))
            row['permissions'] = pending_permissions(self.temp.name, self.agent)
            for open_id in (None, row['id']):
                _, boxes = core.render_console([], None, {}, [row], time.time(), open_id=open_id)
                for kind in ('permission', 'permission-deny'):
                    self.assertEqual(any(box[0] == kind for box in boxes), actionable)
            if tool:
                self.relay.observe({'type': 'control_cancel_request', 'request_id': tool})
        row['permissions'] = pending_permissions(self.temp.name, self.agent)
        _, boxes = core.render_console([], None, {}, [row], time.time())
        self.assertFalse(any(box[0] in ('permission', 'permission-deny') for box in boxes))

    def test_multiline_permission_renders_without_changing_approved_input(self):
        command = "python - <<'PY'\nprint('example')\nPY"
        data = request()
        data["request"]["input"]["command"] = command
        self.relay.observe(data)
        row = core.demo_agents()[0]
        row["permissions"] = pending_permissions(self.temp.name, self.agent)
        for open_id in (None, row["id"]):
            _, boxes = core.render_console([], {}, {}, [row], time.time(), open_id=open_id)
            self.assertIn("permission", [box[0] for box in boxes])
        preview = core.elide(command, core.MONO("book", 9), 10000)
        self.assertNotIn("\n", preview)
        self.assertIn("↵", preview)
        item = row["permissions"][0]
        self.assertEqual(item["request"]["input"]["command"], command)
        self.assertTrue(answer_permission(self.temp.name, item, "allow")["ok"])
        self.assertEqual(json.loads(self.child[0])["response"]["response"]["updatedInput"]["command"], command)

    def test_wrapper_receives_ui_approval_and_cleans_up_endpoint(self):
        script = Path(self.temp.name) / "child.py"
        script.write_text("import json,sys\n"
                          "print(json.dumps({'type':'system','subtype':'init','session_id':'live-test','model':'claude-test'}),flush=True)\n"
                          "print(" + repr(json.dumps(request())) + ",flush=True)\n"
                          "answer=json.loads(sys.stdin.readline())\n"
                          "assert answer['response']['response']['behavior']=='allow'\n"
                          "print('approved',flush=True)\n")
        proc = subprocess.Popen([sys.executable, "-m", "smith_agents", "--vscode-context-bridge", sys.executable, str(script)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=dict(os.environ, CLAUDE_CONFIG_DIR=self.temp.name))
        directory = Path(self.temp.name) / "widget-context"
        try:
            deadline = time.monotonic() + 5
            items = []
            while not items and time.monotonic() < deadline:
                items = pending_permissions(directory, {"id": "live-test", "state": "working"})
                time.sleep(.02)
            self.assertTrue(items)
            self.assertTrue(answer_permission(directory, items[0], "allow")["ok"])
            stdout, stderr = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertIn(b'approved', stdout)
            self.assertIn(b'control_cancel_request', stdout)
            self.assertFalse(endpoint_path(directory, "live-test").exists())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()

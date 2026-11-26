"""Claude desktop (Code tab) sessions: transcripts, state, names, and Go to session."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_core import core

SESSION = "6eff17ae-ef20-4d23-8cd7-e6fc909f4ccc"
HOST_SESSION = "local_0b1c2d3e-4f50-4a61-8b72-9c8d7e6f5a40"


def list_sessions(directory, transcript=None):
    """List the session files in a directory as live processes."""
    with patch.object(core, "SESSIONS_DIR", str(directory)), \
         patch.object(core.platform, "session_process_start", return_value=0), \
         patch.object(core, "_pid_alive", return_value=True), \
         patch.object(core, "_agent_transcript", return_value=transcript), \
         patch.object(core, "list_subagents", return_value=[]):
        return core._list_claude_agents()


class TranscriptPathTests(unittest.TestCase):
    def test_transcript_is_found_for_folders_with_dots_and_underscores(self):
        # Claude Code names the project folder with every character that is not
        # a letter, a digit, or a dash turned into a dash.
        for cwd, folder in (
                ("/Users/me/Projects/my_work", "-Users-me-Projects-my-work"),
                ("/Users/me/Projects/shop/.claude/worktrees/fix-1b2c",
                 "-Users-me-Projects-shop--claude-worktrees-fix-1b2c"),
                ("/Users/me/site v2.1", "-Users-me-site-v2-1"),
                ("C:\\Users\\me\\notes", "C--Users-me-notes")):
            with self.subTest(cwd=cwd), tempfile.TemporaryDirectory() as directory:
                transcript = Path(directory) / folder / (SESSION + ".jsonl")
                transcript.parent.mkdir()
                transcript.write_text("{}\n")
                with patch.object(core, "PROJECTS_DIR", directory):
                    self.assertEqual(core._agent_transcript(cwd, SESSION), str(transcript))


class SessionStateTests(unittest.TestCase):
    def state(self, record, last_stop=None, quiet_for=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "1.json").write_text(json.dumps(dict(
                sessionId=SESSION, pid=123, cwd="/projects/shop", **record)))
            transcript = None
            if last_stop:
                transcript = str(root / "transcript.jsonl")
                Path(transcript).write_text(json.dumps(
                    {"message": {"stop_reason": last_stop}}, separators=(",", ":")) + "\n")
            with patch.object(core.time, "time", return_value=(
                    Path(transcript).stat().st_mtime if transcript else 0) + quiet_for):
                return list_sessions(root, transcript)[0]["state"]

    def test_idle_session_is_ready_even_without_a_transcript(self):
        self.assertEqual(self.state({"status": "idle"}), "done")

    def test_idle_session_is_ready_after_an_interrupted_turn(self):
        self.assertEqual(self.state({"status": "idle"}, last_stop="tool_use", quiet_for=600), "done")

    def test_busy_session_is_working_before_its_first_reply_of_a_new_turn(self):
        self.assertEqual(self.state({"status": "busy"}, last_stop="end_turn"), "working")

    def test_waiting_session_needs_you(self):
        self.assertEqual(self.state({"status": "waiting"}, last_stop="tool_use"), "needs")

    def test_session_without_a_status_still_reads_its_transcript(self):
        self.assertEqual(self.state({}, last_stop="end_turn"), "done")
        self.assertEqual(self.state({}, last_stop="tool_use"), "working")
        self.assertEqual(self.state({}, last_stop="tool_use", quiet_for=600), "needs")


class SessionNameTests(unittest.TestCase):
    def test_worktree_session_is_named_after_its_project(self):
        for cwd, expected in (
                ("/Users/me/Projects/shop/.claude/worktrees/dev-run-8beb8f", "shop"),
                ("/Users/me/Projects/shop/.claude/worktrees/dev-run-8beb8f/", "shop"),
                ("C:\\Users\\me\\shop\\.claude\\worktrees\\dev-run-8beb8f", "shop"),
                # A folder inside the worktree is still that folder.
                ("/Users/me/Projects/shop/.claude/worktrees/dev-run-8beb8f/api", "api"),
                ("/Users/me/Projects/shop", "shop")):
            with self.subTest(cwd=cwd):
                self.assertEqual(core.project_title(cwd), expected)

    def test_desktop_session_shows_its_title_under_the_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "1.json").write_text(json.dumps(dict(
                sessionId=SESSION, pid=123, cwd="/projects/shop", name="Fix checkout",
                entrypoint="claude-desktop")))
            (root / "2.json").write_text(json.dumps(dict(
                sessionId="terminal", pid=124, cwd="/projects/blog", name="blog-12",
                entrypoint="cli")))
            rows = {row["id"]: row for row in core.label_shared_sessions(list_sessions(root))}
        self.assertEqual(rows[SESSION]["name"], "shop")
        self.assertEqual(rows[SESSION]["session_label"], "Fix checkout")
        self.assertNotIn("session_label", rows["terminal"])

    def test_two_worktrees_of_one_project_are_told_apart_by_session_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number, tree in enumerate(("a-111111", "b-222222")):
                (root / (tree + ".json")).write_text(json.dumps(dict(
                    sessionId=tree, pid=200 + number, name="Task " + tree[0],
                    cwd="/projects/shop/.claude/worktrees/" + tree, entrypoint="cli")))
            rows = core.label_shared_sessions(list_sessions(root))
        self.assertEqual({row["name"] for row in rows}, {"shop"})
        self.assertEqual({row["session_label"] for row in rows}, {"Task a", "Task b"})


class DesktopLabelTests(unittest.TestCase):
    def test_desktop_session_is_not_called_a_terminal(self):
        row = {"entrypoint": "claude-desktop", "model": "claude-opus-4-6"}
        self.assertEqual(core.agent_window_action(row), ("open", "Open session"))
        self.assertEqual(core.agent_window_action(dict(row, _window_open=True)), ("hide", "Hide window"))
        self.assertEqual(core.agent_model_source(row), "Opus 4.6 · Desktop")

    def test_terminal_and_editor_labels_are_unchanged(self):
        self.assertEqual(core.agent_window_action({"entrypoint": "cli"}), ("open", "Open terminal"))
        self.assertEqual(core.agent_window_action({"entrypoint": "claude-vscode"}), ("open", "Open chat"))


class DesktopSessionIdentityTests(unittest.TestCase):
    def test_session_row_carries_the_desktop_apps_own_session_id(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "1.json").write_text(json.dumps(dict(
                sessionId=SESSION, pid=123, cwd="/projects/shop",
                entrypoint="claude-desktop", hostSessionId=HOST_SESSION)))
            self.assertEqual(list_sessions(directory)[0]["host_session"], HOST_SESSION)


@unittest.skipUnless(sys.platform == "darwin", "macOS backend")
class DesktopGoToSessionTests(unittest.TestCase):
    def setUp(self):
        from smith_agents import platform_darwin
        self.mac = platform_darwin
        self.agent = {"id": SESSION, "pid": 123, "entrypoint": "claude-desktop",
                      "cwd": "/projects/shop", "host_session": HOST_SESSION}

    def test_link_targets_one_desktop_session(self):
        self.assertEqual(self.mac.claude_desktop_session_url(self.agent),
                         "claude://claude.ai/epitaxy/" + HOST_SESSION)

    def test_link_is_refused_for_anything_but_a_local_session_id(self):
        for value in (None, "", SESSION, "local_" + SESSION + "?prompt=do-stuff",
                      "local_../" + SESSION, "remote_" + SESSION, 7):
            with self.subTest(value=value):
                self.assertIsNone(self.mac.claude_desktop_session_url(
                    dict(self.agent, host_session=value)))

    def open_session(self, agent, window=None, trusted=False):
        import AppKit
        workspace = Mock()
        with patch.object(self.mac, "pid_alive", return_value=True), \
             patch.object(self.mac, "_agent_host_application", return_value=Mock()), \
             patch.object(self.mac, "_match_agent_window", return_value=window), \
             patch.object(self.mac.macos_windows, "trusted", return_value=trusted), \
             patch.object(self.mac.macos_windows, "request_access") as request_access, \
             patch.object(AppKit, "NSWorkspace", Mock(sharedWorkspace=Mock(return_value=workspace))), \
             patch("psutil.Process") as process:
            process.return_value.parents.return_value = []
            self.mac._AGENT_WINDOWS.discard(agent)
            result = self.mac.raise_agent_window(agent)
        opened = [str(call.args[0]) for call in
                  workspace.openURL_configuration_completionHandler_.call_args_list]
        return result, opened, request_access

    def test_go_to_session_opens_that_session_without_accessibility(self):
        # The Claude window's title never names the project, so no window
        # matches; the session link alone must be enough.
        result, opened, request_access = self.open_session(self.agent)
        self.assertTrue(result)
        self.assertEqual(opened, ["claude://claude.ai/epitaxy/" + HOST_SESSION])
        request_access.assert_not_called()

    def test_desktop_session_without_an_app_session_id_opens_nothing_else(self):
        agent = dict(self.agent, host_session=None)
        with self.assertRaises(PermissionError):
            self.open_session(agent)
        result, opened, _ = self.open_session(agent, trusted=True)
        self.assertFalse(result)
        self.assertEqual(opened, [])


if __name__ == "__main__":
    unittest.main()

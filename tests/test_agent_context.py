import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_core import core


def response(tokens, **usage):
    return {"type": "assistant", "message": {"model": "claude-test", "usage": {
        "input_tokens": tokens, **usage}}}


class AgentContextTests(unittest.TestCase):
    def test_latest_request_includes_cache_without_summing_turns_or_output(self):
        records = [response(90000), response(200, cache_creation_input_tokens=3000,
                   cache_read_input_tokens=45000, output_tokens=800,
                   cache_creation={"ephemeral_1h_input_tokens": 3000})]
        records.append({"type": "user", "message": {"content": "tool result"}})
        self.assertEqual(core.agent_context_tokens(records), 48200)

    def test_compaction_resets_old_reading_until_new_request(self):
        boundary = {"type": "system", "subtype": "compact_boundary"}
        records = [response(180000), boundary]
        self.assertIsNone(core.agent_context_tokens(records))
        boundary["compactMetadata"] = {"postTokens": 24000}
        self.assertEqual(core.agent_context_tokens(records), 24000)
        records.append(response(27000))
        self.assertEqual(core.agent_context_tokens(records), 27000)

    def test_missing_invalid_and_synthetic_usage_is_not_a_zero_reading(self):
        for value in (None, -1, "100", True, 1.5):
            self.assertIsNone(core.agent_context_tokens([response(value)]))
        synthetic = response(0)
        synthetic["message"]["model"] = "<synthetic>"
        error = response(0)
        error["isApiErrorMessage"] = True
        self.assertEqual(core.agent_context_tokens([response(42), synthetic, error]), 42)
        self.assertIsNone(core.agent_context_tokens([]))
        self.assertEqual(core.agent_context_tokens([response(0)]), 0)

    def test_each_agent_uses_its_own_transcript_and_tolerates_partial_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            parent_path = Path(directory) / "parent.jsonl"
            sub_path = Path(directory) / "sub.jsonl"
            parent_path.write_text(json.dumps(response(12000)) + '\n{"type":')
            sub_path.write_text(json.dumps(response(3400)) + '\n')
            agents = [{"id": "parent", "cwd": directory, "state": "working"},
                      {"id": "sub", "cwd": directory, "state": "working", "sub": True,
                       "transcript": str(sub_path)}]
            with patch.object(core, "_agent_transcript", return_value=str(parent_path)), \
                 patch.object(core, "git_branch", return_value=""):
                core.decorate_agents(agents)
            self.assertEqual([agent["context_tokens"] for agent in agents], [12000, 3400])
            parent_path.write_text(json.dumps(response(6000)) + '\n')
            with patch.object(core, "_agent_transcript", return_value=str(parent_path)), \
                 patch.object(core, "git_branch", return_value=""):
                core.decorate_agents(agents)
            self.assertEqual(agents[0]["context_tokens"], 6000)

    def test_details_distinguish_real_messages_from_tools_and_harness_text(self):
        records = [
            {"type": "user", "message": {"content": "Review the invoice changes."}},
            {"type": "assistant", "message": {"model": "claude-test", "content": [
                {"type": "text", "text": "Checking the tests."},
                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "tool output"}]}},
            {"type": "user", "isMeta": True, "message": {"content": "internal telemetry"}},
            {"type": "user", "isCompactSummary": True, "message": {"content": "compaction summary"}},
            {"type": "user", "message": {"content": "<task-notification>Agent done</task-notification>"}},
            {"type": "assistant", "isApiErrorMessage": True, "message": {"content": "API error"}},
            {"type": "assistant", "message": {"model": "<synthetic>", "content": "synthetic text"}},
        ]
        self.assertEqual(core.agent_details(records), {"last_request": "Review the invoice changes.",
                         "latest_message": "Checking the tests.", "last_tool": "Bash pytest -q"})
        records.append({"type": "user", "message": {"content": "<system-reminder>metadata</system-reminder> Fix the failure."}})
        self.assertEqual(core.agent_details(records)["last_request"], "Fix the failure.")
        self.assertEqual(core.agent_details([]), {"last_request": None, "latest_message": None, "last_tool": None})

    def test_git_branch_preserves_namespace_in_normal_and_worktree_repos(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gitdir = root / "metadata"
            gitdir.mkdir()
            (gitdir / "HEAD").write_text("ref: refs/heads/codex/richer-agent-panels\n")
            for name, worktree in (("normal", False), ("worktree", True)):
                folder = root / name
                folder.mkdir()
                if worktree:
                    (folder / ".git").write_text("gitdir: ../metadata\n")
                else:
                    (folder / ".git").mkdir()
                    (folder / ".git" / "HEAD").write_text((gitdir / "HEAD").read_text())
                self.assertEqual(core.git_branch(str(folder)), "codex/richer-agent-panels")

    def test_model_names_are_readable_and_custom_models_preserved(self):
        for raw, expected in (("claude-opus-4-6", "Opus 4.6"),
                              ("claude-sonnet-4-20250514", "Sonnet 4"),
                              ("claude-haiku-4-5-20251001", "Haiku 4.5"),
                              ("custom-model", "custom-model")):
            self.assertEqual(core.agent_model_source({"model": raw, "entrypoint": "claude-vscode"}), expected + " · VS Code")

    def test_project_titles_preserve_real_folder_suffixes_on_both_platforms(self):
        for path, expected in (("/Users/me/Projects/qilobot", "qilobot"),
                               ("C:\\Users\\me\\Projects\\qilobot\\", "qilobot"),
                               ("/projects/qilobot-30", "qilobot-30"),
                               ("/", "/"), ("", "Named session")):
            with self.subTest(path=path):
                self.assertEqual(core.project_title(path, "Named session"), expected)

    def test_claude_session_label_only_appears_for_shared_live_project(self):
        from smith_agents import tucked
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = str(root / "qilobot")
            first = dict(sessionId="first", pid=123, name="qilobot-30", cwd=project)
            (root / "first.json").write_text(json.dumps(first))
            with patch.object(core, "SESSIONS_DIR", directory), \
                 patch.object(core.platform, "session_process_start", return_value=0), \
                 patch.object(core, "_pid_alive", return_value=True), \
                 patch.object(core, "_agent_transcript", return_value=None), \
                 patch.object(core, "list_subagents", return_value=[]):
                rows = core.label_shared_sessions(core._list_claude_agents())
                self.assertEqual(rows[0]["name"], "qilobot")
                self.assertEqual(rows[0]["session_name"], "qilobot-30")
                self.assertNotIn("session_label", rows[0])
                self.assertEqual(tucked._name_lines(rows[0]), ["qilobot"])
                (root / "second.json").write_text(json.dumps(dict(first, sessionId="second", name="qilobot-31")))
                rows = core.label_shared_sessions(core._list_claude_agents())
                self.assertEqual({a["session_label"] for a in rows}, {"qilobot-30", "qilobot-31"})
                for row in rows:
                    self.assertEqual(row["name"], "qilobot")
                    self.assertEqual(tucked._name_lines(row), ["qilobot", row["session_label"]])
                rows[1]["state"] = "closed"
                rows.append(dict(rows[0], id="helper", name="Review tests", sub=True))
                core.label_shared_sessions(rows)
                self.assertTrue(all("session_label" not in row for row in rows))
                self.assertEqual(rows[-1]["name"], "Review tests")


if __name__ == "__main__":
    unittest.main()

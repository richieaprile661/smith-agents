"""ChatGPT (Codex desktop) and DeepSeek Harness sessions, using synthetic records."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from smith_agents import codex_sessions as codex, deepseek_sessions as deepseek
from test_core import core

T0 = 1_795_600_000
THREAD = "0a1b2c3d-4e5f-7a6b-8c9d-0e1f2a3b4c5d"
SESSION = "session-1f2e3d4c-5b6a-4978-8a9b-0c1d2e3f4a5b"


def event(kind, second, **data):
    return json.dumps({"type": kind, "seq": second, "time": (T0 + second) * 1000, "data": data},
                      separators=(",", ":")).encode()


def header(session=SESSION, cwd="/projects/shop", **extra):
    return json.dumps({"type": "session", "version": 4, "id": session, "createdAt": T0 * 1000,
                       "cwd": cwd, "delegationDepth": 0, "agentPreset": "standard", **extra},
                      separators=(",", ":")).encode()


def said(second, text, model="deepseek-v4-pro", tool=None, **usage):
    content = [{"type": "reasoning", "text": "PRIVATE REASONING"}, {"type": "text", "text": text}]
    if tool:
        content.append({"type": "tool-call", "name": tool})
    return event("assistant/message", second, turn=1, step=1, usage=usage, message={
        "role": "assistant", "content": content,
        "source": {"kind": "model", "provider": "deepseek-official", "model": model}})


def asked(second, text):
    return event("user/message", second, role="user", content=[{"type": "text", "text": text}])


def finished_turn():
    return [header(), asked(1, "Fix the checkout total"),
            event("session/title", 2, title="Checkout total"),
            event("request/context", 3, provider="deepseek-official", model="deepseek-v4-pro",
                  contextWindow=1000000),
            event("turn/start", 4, turn=1),
            event("tool/call", 5, turn=1, step=1, callId="c1", name="bash", arguments="{}"),
            said(6, "The total is fixed.", inputTokens=6000, outputTokens=90,
                 cacheReadTokens=700, cacheWriteTokens=50, totalTokens=6840),
            event("turn/end", 7, turn=1, reason={"kind": "completed"})]


class DeepSeekLogTests(unittest.TestCase):
    def test_finished_turn_is_ready_with_its_project_title_model_and_context(self):
        row = deepseek.summarize(finished_turn(), T0 + 60)
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["session_id"], SESSION)
        self.assertEqual((row["name"], row["session_name"], row["cwd"]),
                         ("shop", "Checkout total", "/projects/shop"))
        self.assertEqual(row["model"], "deepseek-v4-pro")
        # The request's context is fresh input plus both cache counts, never output.
        self.assertEqual((row["context_tokens"], row["context_capacity"]), (6750, 1000000))
        self.assertEqual((row["last_request"], row["latest_message"], row["last_tool"]),
                         ("Fix the checkout total", "The total is fixed.", "bash"))
        self.assertEqual((row["since"], row["idle"]), (T0, 53))

    def test_reasoning_never_reaches_the_row(self):
        self.assertNotIn("PRIVATE REASONING", json.dumps(deepseek.summarize(finished_turn(), T0 + 60)))

    def test_open_turn_is_working_then_quiet(self):
        lines = finished_turn() + [asked(20, "Now the tax"), event("turn/start", 21, turn=2)]
        self.assertEqual(deepseek.summarize(lines, T0 + 30)["state"], "working")
        quiet = deepseek.summarize(lines, T0 + 600)
        self.assertEqual((quiet["state"], core.agent_status(dict(quiet, provider="deepseek"))),
                         ("needs", "Quiet · check DeepSeek"))

    def test_unanswered_approval_needs_you_until_it_is_decided(self):
        lines = finished_turn() + [event("turn/start", 21, turn=2), event(
            "approval/asked", 22, id="a1", toolName="bash", callId="c2", reason="rm -rf build")]
        row = deepseek.summarize(lines, T0 + 23)
        self.assertEqual(row["state"], "needs")
        self.assertEqual(row["permissions"], [{"actionable": False, "request": {"tool_name": "bash", "input": {}}}])
        self.assertEqual(core.agent_status(dict(row, provider="deepseek")), "Answer in DeepSeek")
        lines.append(event("approval/decided", 24, id="a1", outcome="allowed-once"))
        row = deepseek.summarize(lines, T0 + 25)
        self.assertEqual((row["state"], row["permissions"]), ("working", []))

    def test_failed_turn_needs_you(self):
        lines = finished_turn()[:-1] + [event("turn/end", 7, turn=1, reason={
            "kind": "error", "error": {"message": "Out of credit", "code": "QUOTA", "status": 402}})]
        row = deepseek.summarize(lines, T0 + 60)
        self.assertEqual((row["state"], row["status_detail"]), ("needs", "Turn failed · check DeepSeek"))

    def test_stopped_turn_is_ready(self):
        lines = finished_turn()[:-1] + [event("turn/end", 7, turn=1, reason={
            "kind": "aborted", "reason": {"kind": "user"}})]
        self.assertEqual(deepseek.summarize(lines, T0 + 60)["state"], "done")

    def test_compaction_clears_the_context_reading_until_the_next_reply(self):
        lines = finished_turn() + [event("compaction/end", 30, compactionId="k")]
        self.assertIsNone(deepseek.summarize(lines, T0 + 60)["context_tokens"])
        lines.append(said(31, "Continuing.", inputTokens=900))
        self.assertEqual(deepseek.summarize(lines, T0 + 60)["context_tokens"], 900)

    def test_a_later_model_choice_replaces_the_earlier_one(self):
        lines = finished_turn() + [event("model/selection", 30, provider="deepseek-official",
                                         model="deepseek-flash", reasoningEffort="high")]
        self.assertEqual(deepseek.summarize(lines, T0 + 60)["model"], "deepseek-flash")

    def test_request_and_state_survive_a_very_long_turn(self):
        lines = [header(), asked(1, "Refactor everything"), event("turn/start", 2, turn=1)]
        for step in range(3, 3003):
            lines.append(said(step, "Step %d" % step, inputTokens=step))
        row = deepseek.summarize(lines, T0 + 3010)
        self.assertEqual((row["state"], row["last_request"], row["latest_message"], row["context_tokens"]),
                         ("working", "Refactor everything", "Step 3002", 3002))

    def test_damaged_lines_are_skipped_and_a_log_without_a_header_is_refused(self):
        lines = finished_turn()
        lines[4:4] = [b'{"type":"turn/start","seq":', b"not json", b""]
        self.assertEqual(deepseek.summarize(lines, T0 + 60)["state"], "done")
        self.assertIsNone(deepseek.summarize(lines[1:], T0 + 60))
        self.assertIsNone(deepseek.summarize([], T0 + 60))

    def test_no_process_sweep_where_deepseek_was_never_used(self):
        with tempfile.TemporaryDirectory() as empty, \
             patch.object(deepseek, "home", return_value=Path(empty)), \
             patch("psutil.process_iter") as sweep:
            self.assertEqual(deepseek.processes(), [])
            sweep.assert_not_called()

    def test_helper_session_is_refused(self):
        lines = [header(session="9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d", parentSession=SESSION,
                        delegationDepth=1)] + finished_turn()[1:]
        self.assertIsNone(deepseek.summarize(lines, T0 + 60))


@unittest.skipUnless(deepseek.can_read_logs(), "needs zstd from Python 3.14")
class DeepSeekScannerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "sessions" / "--projects-shop--"

    def session(self, name, lines):
        from compression import zstd
        folder = self.root / name
        folder.mkdir(parents=True)
        (folder / "session.v4.jsonl.zstd").write_bytes(zstd.compress(b"\n".join(lines) + b"\n"))
        (folder / "session.lock").write_bytes(b"")
        return str(folder / "session.lock")

    def frames(self, lines):
        """The app's own layout: every record is a zstd frame of its own."""
        from compression import zstd
        return b"".join(zstd.compress(line + b"\n") for line in lines)

    def following(self):
        held = self.session(SESSION, finished_turn())
        log = Path(held).with_name("session.v4.jsonl.zstd")
        log.write_bytes(self.frames(finished_turn()))
        scanner = deepseek.Scanner(lambda: [{"pid": 10, "created": 5.0, "files": [held]}])
        return scanner, log

    def test_log_written_one_frame_per_record_reads_the_same(self):
        scanner, _ = self.following()
        row = scanner.scan(T0 + 60)[0]
        self.assertEqual((row["state"], row["session_name"], row["context_tokens"], row["idle"]),
                         ("done", "Checkout total", 6750, 53))

    def test_appended_records_are_folded_in_without_rereading_the_log(self):
        scanner, log = self.following()
        scanner.scan(T0 + 60)
        with log.open("ab") as stream:
            stream.write(self.frames([asked(70, "And the tax"), event("turn/start", 71, turn=2),
                                      said(72, "Working on it", inputTokens=9000)]))
        with patch.object(deepseek, "parse", wraps=deepseek.parse) as parse:
            row = scanner.scan(T0 + 73)[0]
            parse.assert_not_called()
        self.assertEqual((row["state"], row["last_request"], row["latest_message"],
                          row["context_tokens"], row["idle"], row["session_name"]),
                         ("working", "And the tax", "Working on it", 9000, 1, "Checkout total"))

    def test_record_still_being_written_waits_for_the_next_scan(self):
        scanner, log = self.following()
        scanner.scan(T0 + 60)
        whole = self.frames([asked(70, "And the tax"), event("turn/start", 71, turn=2)])
        partial = self.frames([said(72, "Working on it", inputTokens=9000)])
        with log.open("ab") as stream:
            stream.write(whole + partial[:-5])
        with patch.object(deepseek, "parse", wraps=deepseek.parse) as parse:
            row = scanner.scan(T0 + 73)[0]
            self.assertEqual((row["state"], row["latest_message"]), ("working", "The total is fixed."))
            with log.open("ab") as stream:
                stream.write(partial[-5:])
            self.assertEqual(scanner.scan(T0 + 74)[0]["latest_message"], "Working on it")
            parse.assert_not_called()

    def test_only_a_session_whose_lock_the_app_holds_is_live(self):
        held = self.session(SESSION, finished_turn())
        self.session("session-old", [header(session="session-old")] + finished_turn()[1:])
        self.assertEqual(deepseek.Scanner(lambda: []).scan(T0 + 60), [])
        process = {"pid": 10, "created": 5.0, "files": [held]}
        rows = deepseek.Scanner(lambda: [process]).scan(T0 + 60)
        self.assertEqual([(r["id"], r["provider"], r["pid"], r["process_created"], r["state"],
                           r["entrypoint"], r["can_terminate"]) for r in rows],
                         [("deepseek:" + SESSION, "deepseek", 10, 5.0, "done", "deepseek-desktop", False)])

    def test_a_lock_held_by_two_processes_is_not_a_live_session(self):
        held = self.session(SESSION, finished_turn())
        both = [{"pid": 10, "created": 5.0, "files": [held]}, {"pid": 11, "created": 6.0, "files": [held]}]
        self.assertEqual(deepseek.Scanner(lambda: both).scan(T0 + 60), [])

    def test_session_is_reread_when_its_log_changes(self):
        from compression import zstd
        held = self.session(SESSION, finished_turn())
        scanner = deepseek.Scanner(lambda: [{"pid": 10, "created": 5.0, "files": [held]}])
        self.assertEqual(scanner.scan(T0 + 60)[0]["state"], "done")
        more = finished_turn() + [asked(70, "And the tax"), event("turn/start", 71, turn=2)]
        Path(held).with_name("session.v4.jsonl.zstd").write_bytes(zstd.compress(b"\n".join(more) + b"\n"))
        row = scanner.scan(T0 + 72)[0]
        self.assertEqual((row["state"], row["last_request"]), ("working", "And the tax"))

    def test_half_written_log_keeps_the_last_good_reading(self):
        held = self.session(SESSION, finished_turn())
        scanner = deepseek.Scanner(lambda: [{"pid": 10, "created": 5.0, "files": [held]}])
        scanner.scan(T0 + 60)
        log = Path(held).with_name("session.v4.jsonl.zstd")
        log.write_bytes(log.read_bytes()[:-9])
        row = scanner.scan(T0 + 61)[0]
        self.assertEqual((row["state"], row["session_name"]), ("done", "Checkout total"))

    def test_unreadable_log_is_still_a_live_session_with_unknown_activity(self):
        held = self.session(SESSION, finished_turn())
        Path(held).with_name("session.v4.jsonl.zstd").write_bytes(b"not zstd")
        row = deepseek.Scanner(lambda: [{"pid": 10, "created": 5.0, "files": [held]}]).scan(T0 + 60)[0]
        self.assertEqual((row["id"], row["state"], row["status_detail"], row["name"]),
                         ("deepseek:" + SESSION, "needs", "Activity unknown", "DeepSeek session"))

    def test_closed_session_leaves_a_closed_row_for_two_minutes(self):
        held = self.session(SESSION, finished_turn())
        process = {"pid": 10, "created": 5.0, "files": [held]}
        scanner = deepseek.Scanner(lambda: [process])
        scanner.scan(T0 + 60)
        process["files"] = []
        self.assertEqual([r["state"] for r in scanner.scan(T0 + 61)], ["closed"])
        self.assertEqual(scanner.scan(T0 + 200), [])

    def test_helper_session_gets_no_row_of_its_own(self):
        helper = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d"
        held = self.session(helper, [header(session=helper, parentSession=SESSION,
                                            delegationDepth=1)] + finished_turn()[1:])
        self.assertEqual(deepseek.Scanner(lambda: [{"pid": 10, "created": 5.0, "files": [held]}]).scan(T0 + 60), [])


class DeepSeekInWidgetTests(unittest.TestCase):
    def row(self):
        return dict(deepseek.summarize(finished_turn(), T0 + 60), provider="deepseek", pid=10,
                    id="deepseek:" + SESSION, process_created=5.0, entrypoint="deepseek-desktop",
                    can_terminate=False, sub=False)

    def test_deepseek_sessions_join_the_other_providers(self):
        with patch.object(core, "_list_claude_agents", return_value=[]), \
             patch.object(codex, "list_agents", return_value=[]), \
             patch("smith_agents.hermes_sessions.list_agents", return_value=[]), \
             patch.object(deepseek, "list_agents", return_value=[self.row()]), \
             patch.object(core.platform, "process_started", return_value=5.0):
            rows = core._list_agents()
        self.assertEqual([(r["provider"], r["name"], r["started_at"]) for r in rows],
                         [("deepseek", "shop", 5.0)])

    def test_row_reads_as_deepseek_and_keeps_its_own_readings(self):
        row = self.row()
        with patch.object(core, "_agent_transcript") as claude, patch.object(core, "git_branch", return_value=""):
            core.decorate_agents([row])
            claude.assert_not_called()
        self.assertEqual(row["context_tokens"], 6750)
        self.assertEqual(core.provider_name(row), "DeepSeek")
        self.assertEqual(core.agent_model_source(row), "DeepSeek · deepseek-v4-pro · Desktop")
        self.assertEqual(core.agent_status(row), "Ready")

    def test_deepseek_row_carries_its_own_provider_badge(self):
        from PIL import Image
        chip = Image.new("RGBA", (core.px(40), core.px(40)))
        self.assertTrue(core.draw_provider_badge(chip, self.row(), core.px(3), core.px(2)))
        badge = chip.getchannel("A").getbbox()
        # The mark fills its 16px corner cell and stays inside it.
        self.assertGreaterEqual(badge[2] - badge[0], core.px(12))
        self.assertLessEqual(badge[2], core.px(3 + 16))
        self.assertLessEqual(badge[3], core.px(2 + 16))
        # It is the provider's own mark, not another provider's.
        other = Image.new("RGBA", chip.size)
        core.draw_provider_badge(other, {"provider": "codex"}, core.px(3), core.px(2))
        self.assertNotEqual(chip.tobytes(), other.tobytes())

    def test_deepseek_session_is_managed_in_its_own_app(self):
        row = dict(core.demo_agents()[1], **self.row())
        with patch.object(core.platform, "terminate_agent") as kill:
            self.assertFalse(core.terminate_agent(row))
            kill.assert_not_called()
        # The app has no link to one session; only its window can be opened.
        self.assertEqual(core.agent_window_action(row), ("open", "Open window"))
        _, boxes = core.render_console([], None, {}, [row], 0, open_id=row["id"])
        self.assertNotIn("kill", [box[0] for box in boxes])


class CodexDesktopTests(unittest.TestCase):
    def thread(self, root, originator, source):
        path = Path(root) / ("rollout-2026-09-10T10-00-00-" + THREAD + ".jsonl")
        meta = dict(id=THREAD, cwd="/projects/shop", source=source, originator=originator,
                    timestamp="2026-09-10T10:00:00Z")
        path.write_text(json.dumps({"timestamp": meta["timestamp"], "type": "session_meta",
                                    "payload": meta}) + "\n", encoding="utf-8")
        process = {"pid": 10, "created": 5.0, "server": True, "files": [str(path)], "files_known": True}
        return codex.Scanner(lambda: [process]).scan(T0)[0]

    def test_thread_started_in_the_chatgpt_app_is_a_desktop_session(self):
        with tempfile.TemporaryDirectory() as root:
            row = self.thread(root, "Codex Desktop", "vscode")
        self.assertEqual(row["entrypoint"], "codex-desktop")
        self.assertTrue(core.agent_model_source(row).endswith(" · Desktop"))
        self.assertEqual(core.agent_window_action(row), ("open", "Open session"))
        self.assertEqual(core.agent_window_action(dict(row, _window_open=True)), ("hide", "Hide window"))

    def test_extension_and_terminal_threads_are_unchanged(self):
        for originator, source, expected in (("codex_vscode", "vscode", "codex-vscode"),
                                             ("codex_cli_rs", "cli", "codex-cli")):
            with self.subTest(originator=originator), tempfile.TemporaryDirectory() as root:
                row = self.thread(root, originator, source)
                self.assertEqual(row["entrypoint"], expected)
                self.assertEqual(core.agent_window_action(row), ("open", "Open window"))


@unittest.skipUnless(sys.platform == "darwin", "macOS backend")
class DesktopAppGoToTests(unittest.TestCase):
    def setUp(self):
        from smith_agents import platform_darwin
        self.mac = platform_darwin

    def open_session(self, agent):
        import AppKit
        workspace = Mock()
        with patch.object(self.mac, "pid_alive", return_value=True), \
             patch.object(self.mac, "_agent_host_application", return_value=Mock()), \
             patch.object(self.mac, "_match_agent_window", return_value=None), \
             patch.object(self.mac.macos_windows, "trusted", return_value=False), \
             patch.object(self.mac.macos_windows, "request_access") as request_access, \
             patch.object(AppKit, "NSWorkspace", Mock(sharedWorkspace=Mock(return_value=workspace))), \
             patch("psutil.Process") as process:
            process.return_value.parents.return_value = []
            process.return_value.create_time.return_value = 5.0
            self.mac._AGENT_WINDOWS.discard(agent)
            result = self.mac.raise_agent_window(agent)
        request_access.assert_not_called()
        return result, [str(call.args[0]) for call in
                        workspace.openURL_configuration_completionHandler_.call_args_list]

    def test_chatgpt_thread_opens_by_its_own_link(self):
        agent = {"provider": "codex", "id": "codex:" + THREAD, "session_id": THREAD, "pid": 10,
                 "process_created": 5.0, "entrypoint": "codex-desktop", "cwd": "/projects/shop"}
        self.assertEqual(self.open_session(agent), (True, ["codex://threads/" + THREAD]))

    def test_chatgpt_link_is_refused_for_anything_but_a_thread_id(self):
        for value in (None, "", "one", THREAD + "?ephemeral=1", "../" + THREAD, 7):
            with self.subTest(value=value):
                self.assertIsNone(self.mac.codex_desktop_thread_url({"session_id": value}))

    def test_deepseek_session_brings_its_app_forward(self):
        agent = {"provider": "deepseek", "id": "deepseek:" + SESSION, "session_id": SESSION,
                 "pid": 10, "process_created": 5.0, "entrypoint": "deepseek-desktop", "cwd": "/projects/shop"}
        self.assertEqual(self.open_session(agent), (True, ["dsh://open"]))

    def test_a_reused_process_id_opens_nothing(self):
        agent = {"provider": "deepseek", "id": "deepseek:" + SESSION, "session_id": SESSION,
                 "pid": 10, "process_created": 4.0, "entrypoint": "deepseek-desktop"}
        self.assertEqual(self.open_session(agent), (False, []))


if __name__ == "__main__":
    unittest.main()

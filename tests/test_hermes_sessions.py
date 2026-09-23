"""Hermes compatibility uses synthetic registry, process, and SQLite fixtures."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from smith_agents import hermes_sessions as hermes
from test_core import core
from smith_agents import tucked
from smith_agents.app import SmithAgentsWidget


class HermesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        home = patch.object(hermes, "home", return_value=self.root)
        home.start()
        self.addCleanup(home.stop)
        self.db = self.root / "state.db"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript("""
                CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, model TEXT,
                    cwd TEXT, started_at REAL, input_tokens INTEGER, system_prompt TEXT);
                CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
                    content TEXT, timestamp REAL, tool_calls TEXT, tool_name TEXT,
                    finish_reason TEXT, active INTEGER DEFAULT 1, display_kind TEXT,
                    _compressed_summary INTEGER DEFAULT 0, reasoning_content TEXT);
            """)
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("one", "Review storefront", "hermes-model", str(self.root), 100, 999999, "private system"))
        self.entry = dict(session_id="one", pid=42, process_start_time=100,
                          started_at=120, surface="cli", metadata={"live_session_id": "one"})
        self.registry([self.entry])
        self.process = dict(pid=42, created=100, cwd=str(self.root))
        self.scanner = hermes.Scanner(lambda entry: self.process)

    def registry(self, entries):
        path = self.root / "runtime" / "active_sessions.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"entries": entries}), encoding="utf-8")

    def message(self, role, content, at=200, **fields):
        values = dict(session_id="one", role=role, content=content, timestamp=at, **fields)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO messages (" + ", ".join(values) + ") VALUES ("
                       + ", ".join("?" for _ in values) + ")", list(values.values()))

    def test_live_cards_read_public_history_without_changing_database(self):
        self.message("system", "secret system")
        self.message("assistant", "hidden reasoning", display_kind="hidden")
        self.message("user", "old rewound prompt", active=0)
        self.message("user", "compaction summary", _compressed_summary=1)
        self.message("user", "Review the storefront")
        self.message("assistant", "Checking", tool_calls=json.dumps([
            {"function": {"name": "terminal", "arguments": '{"command":"pytest"}'}}]))
        self.message("tool", "tests passed", tool_name="terminal")
        self.message("assistant", "All checks passed", 210, finish_reason="stop",
                     tool_calls="[]", reasoning_content="secret reasoning")
        before = self.db.read_bytes()
        row, = self.scanner.scan(215)
        self.assertEqual((row["provider"], row["id"], row["state"]), ("hermes", "hermes:one", "done"))
        self.assertEqual((row["last_request"], row["latest_message"], row["last_tool"]),
                         ("Review the storefront", "All checks passed", "terminal"))
        self.assertEqual(row["idle"], 5)
        self.assertIsNone(row["context_tokens"])
        self.assertIsNone(row["context_capacity"])
        self.assertFalse(row["can_terminate"])
        for private in ("secret", "compaction summary", "old rewound", "hidden reasoning"):
            self.assertNotIn(private, str(row))
        self.assertEqual(before, self.db.read_bytes())

    def test_saved_history_is_not_liveness_and_closed_cards_expire(self):
        self.assertEqual(len(self.scanner.scan(200)), 1)
        self.registry([])
        self.assertEqual(hermes.Scanner(lambda entry: self.process).scan(201), [])
        row, = self.scanner.scan(201)
        self.assertEqual(row["state"], "closed")
        self.assertEqual(self.scanner.scan(322), [])

    def test_project_folder_takes_priority_and_chat_title_distinguishes_shared_sessions(self):
        for cwd, expected in (("/work/storefront", "storefront"),
                              ("C:\\work\\storefront\\", "storefront"),
                              ("/work/My  Project", "My  Project")):
            with self.subTest(cwd=cwd):
                with closing(sqlite3.connect(self.db)) as db, db:
                    db.execute("UPDATE sessions SET cwd=?", (cwd,))
                row, = self.scanner.scan(200)
                self.assertEqual(row["name"], expected)
                self.assertEqual(row["cwd"], cwd)
                self.assertEqual(row["session_name"], "Review storefront")
                core.label_shared_sessions([row, dict(row, id="another")])
                self.assertEqual(row["session_label"], "Review storefront")

    def test_home_or_unavailable_folder_uses_chat_title_without_inventing_project(self):
        for cwd in (str(Path.home()), ""):
            with self.subTest(cwd=cwd):
                self.process["cwd"] = cwd
                with closing(sqlite3.connect(self.db)) as db, db:
                    db.execute("UPDATE sessions SET cwd=?", (cwd,))
                row, = self.scanner.scan(200)
                self.assertEqual(row["name"], "Review storefront")

    def test_ambiguous_ownership_and_dead_processes_are_excluded(self):
        self.registry([self.entry, dict(self.entry, pid=43)])
        self.assertEqual(self.scanner.scan(200), [])
        self.registry([self.entry])
        self.assertEqual(hermes.Scanner(lambda entry: None).scan(200), [])

    def test_independent_sessions_in_one_process_and_lease_replacement(self):
        self.registry([self.entry, dict(self.entry, session_id="two")])
        self.assertEqual({r["id"] for r in self.scanner.scan(200)}, {"hermes:one", "hermes:two"})
        self.registry([dict(self.entry, session_id="compressed")])
        rows = self.scanner.scan(201)
        self.assertEqual([r["id"] for r in rows if r["state"] != "closed"], ["hermes:compressed"])

    def test_recent_and_quiet_states_do_not_invent_approval_or_completion(self):
        self.message("user", "Start work")
        row, = self.scanner.scan(210)
        self.assertEqual((row["state"], row["status_detail"]), ("working", "Recent activity"))
        self.message("assistant", "Still working", 211)
        self.assertEqual(self.scanner.scan(212)[0]["state"], "working")
        row, = self.scanner.scan(400)
        self.assertEqual(row["status_detail"], "Quiet · check Hermes")
        self.assertEqual(row["permissions"], [])

    def test_missing_corrupt_and_changed_database_keep_live_card_unknown(self):
        for content in (None, b"corrupt database", b""):
            self.db.unlink(missing_ok=True)
            if content is not None:
                self.db.write_bytes(content)
            row, = self.scanner.scan(200)
            self.assertEqual(row["status_detail"], "Activity unknown")
            if content is None:
                self.assertFalse(self.db.exists())
        self.db.unlink()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("CREATE TABLE sessions (unknown TEXT)")
        self.assertEqual(self.scanner.scan(200)[0]["status_detail"], "Activity unknown")

    def test_registry_and_message_values_fail_soft(self):
        self.message("assistant", "A reply", tool_calls="{invalid")
        self.assertEqual(self.scanner.scan(200)[0]["latest_message"], "A reply")
        path = self.root / "runtime" / "active_sessions.json"
        for contents in ("{partial", "null", '{"entries": null}', '[null, {}]', "x" * (1024 * 1024 + 1)):
            path.write_text(contents)
            self.assertEqual(hermes.Scanner(lambda entry: self.process).scan(200), [])

    def test_creation_time_and_command_identity_are_required(self):
        process = Mock()
        process.create_time.return_value = 100
        process.exe.return_value = "/usr/bin/python3"
        process.cmdline.return_value = ["python3", "-m", "hermes_cli", "chat"]
        process.cwd.return_value = str(self.root)
        with patch("psutil.Process", return_value=process):
            self.assertEqual(hermes.owner(self.entry), self.process)
            process.create_time.return_value = 101
            self.assertIsNone(hermes.owner(self.entry))
            process.create_time.return_value = 100
            process.cmdline.return_value = ["python3", "unrelated.py", "hermes"]
            self.assertIsNone(hermes.owner(self.entry))
            process.cmdline.return_value = ["python3", "-m", "hermes_cli"]
            for value in (None, True, -1, float("nan")):
                self.assertIsNone(hermes.owner(dict(self.entry, process_start_time=value)))

    def test_bounded_message_tail(self):
        for index in range(50):
            self.message("user", str(index), at=200 + index)
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory = sqlite3.Row
            _, messages = hermes._details(db, "one", hermes._columns(db, "sessions"), hermes._columns(db, "messages"))
        self.assertEqual(len(messages), 32)
        self.assertEqual(messages[-1]["content"], "49")

    def test_multimodal_previews_exclude_nontext_blocks(self):
        self.message("user", json.dumps([
            {"type": "text", "text": "Describe this image"},
            {"type": "image_url", "image_url": {"url": "private image data"}},
            {"type": "reasoning", "text": "private reasoning"}]))
        row, = self.scanner.scan(210)
        self.assertEqual(row["last_request"], "Describe this image")
        self.assertNotIn("private", str(row))

    def test_old_schema_without_optional_columns_still_reads_messages(self):
        self.db.unlink()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript("""
                CREATE TABLE sessions (id TEXT PRIMARY KEY);
                CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT, timestamp REAL, content TEXT);
                INSERT INTO sessions VALUES ('one');
                INSERT INTO messages VALUES (1, 'one', 'user', 200, 'A request');
            """)
        row, = self.scanner.scan(210)
        self.assertEqual(row["last_request"], "A request")
        self.assertEqual(row["state"], "working")

    def test_provider_integration_preserves_rows_and_isolates_failure(self):
        row, = self.scanner.scan(200)
        with patch.object(core, "_list_claude_agents", return_value=[]), \
             patch("smith_agents.codex_sessions.list_agents", return_value=[]), \
             patch.object(hermes, "list_agents", return_value=[row]), \
             patch.object(core.platform, "process_started", return_value=100):
            rows = core.decorate_agents(core.list_agents())
        self.assertEqual(rows[0]["provider"], "hermes")
        self.assertEqual(rows[0]["model"], "hermes-model")
        self.assertFalse(core.terminate_agent(row))
        with patch.object(core, "_list_claude_agents", return_value=[dict(row, id="claude")]), \
             patch("smith_agents.codex_sessions.list_agents", side_effect=RuntimeError), \
             patch.object(hermes, "list_agents", side_effect=RuntimeError), patch.object(core, "log_line"):
            self.assertEqual(len(core.list_agents()), 1)

    def test_hermes_card_and_badge_render_in_console_and_all_edges(self):
        row, = self.scanner.scan(200)
        row["_details_expanded"] = True
        self.assertEqual(core.provider_name(row), "Hermes")
        self.assertIn("Hermes", core.agent_model_source(row))
        self.assertNotIn("Claude", core.agent_model_source(row))
        self.assertIsNotNone(core._provider_logo("hermes", 16).getbbox())
        _, boxes = core.render_console([], None, {}, [row], 200, open_id=row["id"])
        self.assertTrue(any(b[0] == "open" for b in boxes))
        self.assertFalse(any(b[0] == "kill" for b in boxes))
        for side in ("left", "right", "top", "bottom"):
            image, boxes, _ = tucked.render([row], [], None, 200, side=side)
            self.assertGreater(image.width, 0)

    def test_hermes_selector_never_reuses_another_accounts_readings(self):
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = dict(core.DEFAULTS)
        widget.lock = threading.Lock()
        widget._save_config = Mock()
        widget.set_usage_provider('hermes')
        self.assertEqual(widget.config['usage_provider'], 'hermes')
        metrics, spend, plan, active, error, updated, stats = widget._snapshot()
        self.assertEqual(metrics, [])
        self.assertEqual(spend['provider'], 'hermes')
        self.assertIsNone(stats['tokens'])
        self.assertEqual(stats['session'], {})

    def test_three_edge_logos_have_separate_click_targets(self):
        row, = self.scanner.scan(200)
        for side in tucked.EDGES:
            _, boxes, _ = tucked.render([row], [], None, 200, side=side, provider='hermes')
            lamps = [box for box in boxes if box[0].startswith('provider:')]
            self.assertEqual([b[0] for b in lamps], ['provider:claude', 'provider:codex', 'provider:hermes'])
            for index, box in enumerate(lamps):
                _, x0, y0, x1, y1, _ = box
                self.assertLess(x0, x1)
                self.assertLess(y0, y1)
                for other in lamps[index+1:]:
                    self.assertFalse(max(x0, other[1]) < min(x1, other[3])
                                     and max(y0, other[2]) < min(y1, other[4]))



class HermesHome(unittest.TestCase):
    def test_home_is_found_where_hermes_keeps_it_on_each_platform(self):
        env = {k: v for k, v in os.environ.items() if k != 'HERMES_HOME'}
        with patch.dict(os.environ, dict(env, LOCALAPPDATA='C:/Users/me/AppData/Local'), clear=True):
            with patch.object(hermes.sys, 'platform', 'win32'):
                self.assertEqual(hermes.home(), Path('C:/Users/me/AppData/Local') / 'hermes')
            with patch.object(hermes.sys, 'platform', 'darwin'):
                self.assertEqual(hermes.home(), Path.home() / '.hermes')
        with patch.dict(os.environ, {'HERMES_HOME': '/custom/hermes'}):
            with patch.object(hermes.sys, 'platform', 'win32'):
                self.assertEqual(hermes.home(), Path('/custom/hermes'))

    def test_the_dashboard_reads_the_same_hermes_database(self):
        from smith_agents.dashboard import history
        with patch.object(history, 'hermes_home', return_value=Path('/h')):
            self.assertEqual(history.hermes_database(), Path('/h/state.db'))
        self.assertEqual(history.hermes_database('/x'), Path('/x/state.db'))

if __name__ == "__main__":
    unittest.main()

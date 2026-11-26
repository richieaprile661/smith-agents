"""Sessions left open in the Claude desktop app, and the panel that lists them."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import ImageDraw

from smith_agents import app, app_sessions, core

DAY = 86400
NOW = 1_800_000_000


def session_id(number):
    return "local_%08x-4f50-4a61-8b72-9c8d7e6f5a40" % number


def record(number, title, days, cwd="/projects/shop", archived=False, **extra):
    return dict({"sessionId": session_id(number), "title": title, "cwd": cwd, "originCwd": cwd,
                 "lastActivityAt": int((NOW - days * DAY) * 1000), "createdAt": int((NOW - 30 * DAY) * 1000),
                 "isArchived": archived, "toolSurfaceSnapshot": {"big": "x" * 500}}, **extra)


def write(base, data, name=None):
    folder = Path(base) / "0b1c2d3e-0000-4000-8000-000000000001" / "0b1c2d3e-0000-4000-8000-000000000002"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name or data["sessionId"] + ".json")
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


class ReaderTests(unittest.TestCase):
    def test_reads_title_folder_and_last_use_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as base:
            write(base, record(1, "  Calendar   sync fix ", 9))
            write(base, record(2, "Checkout redesign", 6, cwd="/projects/shop/.claude/worktrees/fix-1b2c",
                               originCwd="/projects/shop"))
            write(base, record(3, "Old one", 40, archived=True))
            rows = sorted(app_sessions.Reader().read(base), key=lambda row: row["id"])
        self.assertEqual(rows[0], {"id": session_id(1), "title": "Calendar sync fix", "cwd": "/projects/shop",
                                   "worktree": False, "last": NOW - 9 * DAY, "archived": False})
        self.assertEqual((rows[1]["cwd"], rows[1]["worktree"]), ("/projects/shop", True))
        self.assertTrue(rows[2]["archived"])

    def test_a_worktree_is_named_by_the_app_or_its_folder_and_never_guessed(self):
        tree = "/projects/shop/.claude/worktrees/fix-1b2c"
        with tempfile.TemporaryDirectory() as base:
            write(base, record(1, "Started in its worktree", 1, cwd=tree))
            write(base, record(2, "Named by the app", 1, cwd="/work/trees/a", worktreePath="/work/trees/a"))
            # Moving to a subfolder is not a work folder of the session's own.
            write(base, record(3, "Moved folder", 1, cwd="/projects/shop/api", originCwd="/projects/shop"))
            rows = sorted(app_sessions.Reader().read(base), key=lambda row: row["id"])
        self.assertEqual([(row["cwd"], row["worktree"]) for row in rows],
                         [("/projects/shop", True), ("/work/trees/a", True), ("/projects/shop", False)])

    def test_skips_what_it_does_not_understand(self):
        with tempfile.TemporaryDirectory() as base:
            write(base, record(1, "Good", 1))
            write(base, "{not json", name=session_id(2) + ".json")
            write(base, dict(record(3, "Bad id", 1), sessionId="../../etc"), name=session_id(3) + ".json")
            write(base, dict(record(4, "No time", 1), lastActivityAt="soon", createdAt=None))
            write(base, "[1, 2]", name=session_id(5) + ".json")
            write(base, record(6, "Too big", 1, padding="x" * (app_sessions.MAX_FILE + 1)))
            write(base, record(7, "Not a session file", 1), name="notes.json")
            rows = app_sessions.Reader().read(base)
        self.assertEqual([row["title"] for row in rows], ["Good"])

    def test_no_folder_means_no_panel_and_unchanged_files_are_not_read_again(self):
        self.assertIsNone(app_sessions.Reader().read("/nonexistent/claude-code-sessions"))
        with tempfile.TemporaryDirectory() as base:
            path = write(base, record(1, "One", 1))
            write(base, record(2, "Two", 2))
            reader = app_sessions.Reader()
            with patch.object(app_sessions.json, "loads", wraps=json.loads) as loads:
                reader.read(base)
                self.assertEqual(loads.call_count, 2)
                reader.read(base)
                self.assertEqual(loads.call_count, 2)
                path.write_text(json.dumps(record(1, "One renamed", 0)), encoding="utf-8")
                os.utime(path, (NOW, NOW))
                rows = reader.read(base)
                self.assertEqual(loads.call_count, 3)
            self.assertIn("One renamed", [row["title"] for row in rows])
            path.unlink()
            self.assertEqual([row["title"] for row in reader.read(base)], ["Two"])


def rows(*items):
    return [{"id": session_id(number), "title": title, "cwd": cwd, "worktree": False,
             "last": NOW - days * DAY, "archived": archived}
            for number, title, days, cwd, archived in items]


class SummaryTests(unittest.TestCase):
    def test_open_sessions_are_counted_by_age_and_the_oldest_lead(self):
        found = rows((1, "Working", 0.01, "/projects/shop", False), (2, "Today", 0.5, "/projects/shop", False),
                     (3, "Two days", 2, "/projects/site", False), (4, "Four days", 4.2, "/projects/site", False),
                     (5, "Nine days", 9.9, "/projects/shop/", False), (6, "Archived", 50, "/projects/old", True),
                     (7, "Live but old", 12, "/projects/shop", False))
        summary = app_sessions.summary(found, {session_id(1), session_id(7)}, NOW)
        self.assertEqual({key: summary[key] for key in ("total", "working", "today", "recent", "stale_count")},
                         {"total": 6, "working": 2, "today": 1, "recent": 1, "stale_count": 2})
        self.assertEqual([(row["title"], row["days"], row["project"]) for row in summary["stale"]],
                         [("Nine days", 9, "shop"), ("Four days", 4, "site")])
        # A row is enough for the platform to open the session by its link.
        self.assertEqual((summary["stale"][0]["host_session"], summary["stale"][0]["entrypoint"]),
                         (session_id(5), "claude-desktop"))

    def test_a_long_list_is_cut_and_no_source_gives_no_summary(self):
        found = rows(*[(number, "Session %d" % number, 5 + number, "/projects/shop", False) for number in range(25)])
        summary = app_sessions.summary(found, set(), NOW)
        self.assertEqual((len(summary["stale"]), summary["stale_count"]), (app_sessions.LIST, 25))
        self.assertEqual(summary["stale"][0]["title"], "Session 24")
        self.assertIsNone(app_sessions.summary(None, set(), NOW))

    def test_working_sessions_come_from_the_widgets_own_rows(self):
        agents = [{"entrypoint": "claude-desktop", "host_session": session_id(1), "state": "working"},
                  {"entrypoint": "claude-desktop", "host_session": session_id(2), "state": "done"},
                  {"entrypoint": "cli", "host_session": session_id(3), "state": "working"},
                  {"entrypoint": "claude-desktop", "host_session": session_id(4), "state": "working", "sub": True}]
        self.assertEqual(app_sessions.working_ids(agents), {session_id(1)})


def summary(stale=3, extra=0):
    listed = [{"id": session_id(number), "title": "Session %d with a long title that will not fit the row" % number,
               "project": "shop", "days": 9 - number, "worktree": number == 1,
               "host_session": session_id(number), "entrypoint": "claude-desktop"} for number in range(stale)]
    return {"total": 14, "working": 2, "today": 5, "recent": 4, "stale": listed, "stale_count": stale + extra}


class PanelTests(unittest.TestCase):
    def draw(self, idle, open_):
        chip = core.console_base((core.CONSOLE_W, core.idle_panel_height(idle, open_)))
        draw = ImageDraw.Draw(chip)
        pen = Mock(wraps=draw)
        boxes = core.render_idle_panel(pen, idle, 0, open_)
        return [call.args[1] for call in pen.text.call_args_list], boxes, pen, draw

    def test_closed_panel_is_one_line_and_open_lists_what_to_close(self):
        for scale in (1, 2):
            with patch.object(core, "SCALE", scale), self.subTest(scale=scale):
                idle = summary(extra=4)
                texts, boxes, _, _ = self.draw(idle, False)
                self.assertIn("Open sessions · 14", texts)
                self.assertIn("7 to close", texts)
                self.assertEqual([box[0] for box in boxes], ["idle"])
                texts, boxes, pen, draw = self.draw(idle, True)
                for text in ("Working now", "Used today", "Not used for 1 to 3 days", "Not used for 3 days or more",
                             "9 days", "7 days", "4 more", "shop", "shop · own work folder"):
                    self.assertIn(text, texts)
                self.assertEqual([box[0] for box in boxes], ["idle", "idle-open", "idle-open", "idle-open"])
                self.assertEqual([box[5]["host_session"] for box in boxes[1:]],
                                 [session_id(0), session_id(1), session_id(2)])
                height = core.idle_panel_height(idle, True)
                self.assertGreater(height, core.idle_panel_height(idle, False))
                for item in pen.text.call_args_list:
                    box = draw.textbbox(item.args[0], item.args[1], font=item.kwargs["font"])
                    self.assertLessEqual(box[2], core.CONSOLE_W - core.PAD_X + 1, item.args[1])
                    self.assertLess(box[3], height, item.args[1])
                for _, x0, y0, x1, y1, _ in boxes:
                    self.assertTrue(0 <= y0 < y1 <= height and 0 <= x0 < x1 <= core.CONSOLE_W)

    def test_nothing_to_close_says_so_and_no_summary_draws_nothing(self):
        texts, boxes, _, _ = self.draw(summary(stale=0), True)
        self.assertIn("Nothing to close", texts)
        self.assertEqual([box[0] for box in boxes], ["idle"])
        self.assertEqual(core.idle_panel_height(None, True), 0)

    def test_console_grows_by_the_panel_and_is_unchanged_without_it(self):
        agents = [dict(core.demo_agents()[1], sub=False)]
        plain, plain_boxes = core.render_console([], None, {}, agents, 0)
        idle = summary()
        for open_ in (False, True):
            image, boxes = core.render_console([], None, {}, agents, 0, idle=idle, idle_open=open_)
            self.assertEqual(image.height - plain.height, core.idle_panel_height(idle, open_))
            self.assertEqual(core.agents_height(agents, None, idle, open_) - core.agents_height(agents, None),
                             core.idle_panel_height(idle, open_))
            self.assertIn("idle", [box[0] for box in boxes])
        self.assertNotIn("idle", [box[0] for box in plain_boxes])
        empty, boxes = core.render_console([], None, {}, [], 0, idle=idle)
        self.assertIn("idle", [box[0] for box in boxes])


class ClickTests(unittest.TestCase):
    def widget(self, kind, payload=None):
        widget = app.SmithAgentsWidget.__new__(app.SmithAgentsWidget)
        widget.agents, widget.agent_open, widget.idle_open = [], None, False
        widget.config = {}
        widget.agent_rows = [(kind, 0, 0, 100, 20, payload)]
        return widget

    def test_the_panel_opens_and_shuts_without_opening_a_row(self):
        widget = self.widget("idle")
        with patch.object(widget, "_repaint") as repaint:
            self.assertTrue(widget._on_console_click(SimpleNamespace(x=5, y=5)))
            self.assertTrue(widget.idle_open)
            self.assertIsNone(widget.agent_open)
            widget._on_console_click(SimpleNamespace(x=5, y=5))
            self.assertFalse(widget.idle_open)
            self.assertEqual(repaint.call_count, 2)

    def test_open_shows_the_session_by_its_link_and_says_when_it_cannot(self):
        row = summary()["stale"][0]
        widget = self.widget("idle-open", row)
        with patch.object(widget, "_repaint"), \
             patch.object(app.platform, "open_session_link", return_value=True, create=True) as link, \
             patch.object(app.platform, "show_error") as error:
            widget._on_console_click(SimpleNamespace(x=5, y=5))
            link.assert_called_once_with(row)
            error.assert_not_called()
            link.return_value = False
            widget._on_console_click(SimpleNamespace(x=5, y=5))
            error.assert_called_once()
            self.assertIsNone(widget.agent_open)


class LinkTests(unittest.TestCase):
    row = {"entrypoint": "claude-desktop", "host_session": session_id(5)}
    link = "claude://claude.ai/epitaxy/" + session_id(5)

    def test_windows_opens_a_session_that_has_no_process_by_its_link(self):
        if sys.platform == "win32":
            from smith_agents import platform_win32 as win
        else:   # The module loads anywhere once Windows' own libraries are stood in for.
            import ctypes
            fake = Mock()
            fake.user32.GetDpiForSystem.return_value = 96
            with patch.object(ctypes, "windll", fake, create=True), \
                 patch.object(ctypes, "WinDLL", return_value=fake.kernel32, create=True):
                from smith_agents import platform_win32 as win
        with patch.object(win, "_open_session_link", return_value=True) as opened:
            self.assertTrue(win.open_session_link(self.row))
            opened.assert_called_once_with(self.link)
            self.assertFalse(win.open_session_link({"entrypoint": "claude-desktop", "host_session": "../x"}))
            self.assertEqual(opened.call_count, 1)

    @unittest.skipUnless(sys.platform == "darwin", "macOS link opening")
    def test_mac_opens_a_session_that_has_no_process_by_its_link(self):
        from smith_agents import platform_darwin as mac
        with patch.object(mac, "_open_session_url") as opened:
            self.assertTrue(mac.open_session_link(self.row))
            self.assertEqual(opened.call_args.args[:2], (self.link, True))
            self.assertFalse(mac.open_session_link({"entrypoint": "cli"}))
            self.assertEqual(opened.call_count, 1)


if __name__ == "__main__":
    unittest.main()

"""A usage reading that could not be refreshed must not pass as the number now."""
from datetime import datetime, timezone
import threading
import unittest
from unittest.mock import patch

from test_core import core, SmithAgentsWidget


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


READ_AT = 1_800_000_000.0
HOUR = 3600


def reading(key, pct, reset):
    return {"key": key, "label": key, "detail": key, "pct": pct, "severity": "normal",
            "resets_at": reset, "active": False}


class CurrentReadingsTests(unittest.TestCase):
    def test_a_reading_whose_window_is_still_open_is_kept(self):
        metrics = [reading("weekly", 40, iso(READ_AT + 48 * HOUR))]
        self.assertEqual(core.current_readings(metrics, READ_AT, READ_AT + 24 * HOUR), metrics)

    def test_a_reading_is_dropped_once_its_window_has_reset(self):
        session = reading("session", 80, iso(READ_AT + 2 * HOUR))
        weekly = reading("weekly", 40, iso(READ_AT + 48 * HOUR))
        self.assertEqual(core.current_readings([session, weekly], READ_AT, READ_AT + 3 * HOUR), [weekly])
        self.assertEqual(core.current_readings([session, weekly], READ_AT, READ_AT + 72 * HOUR), [])

    def test_a_fresh_reading_is_kept_through_a_reset_until_the_next_poll(self):
        metrics = [reading("session", 80, iso(READ_AT + 60))]
        self.assertEqual(core.current_readings(metrics, READ_AT, READ_AT + 500, fresh_for=600), metrics)
        self.assertEqual(core.current_readings(metrics, READ_AT, READ_AT + 700, fresh_for=600), [])

    def test_a_reset_already_past_when_the_reading_was_taken_is_the_servers_word(self):
        metrics = [reading("session", 0, iso(READ_AT - HOUR))]
        self.assertEqual(core.current_readings(metrics, READ_AT, READ_AT + 24 * HOUR), metrics)

    def test_a_reading_without_a_readable_reset_or_a_time_is_kept(self):
        metrics = [reading("session", 5, None), reading("weekly", 6, "soon")]
        self.assertEqual(core.current_readings(metrics, READ_AT, READ_AT + 999 * HOUR), metrics)
        expired = [reading("session", 80, iso(READ_AT + HOUR))]
        self.assertEqual(core.current_readings(expired, None, READ_AT + 999 * HOUR), expired)


class ReadingStampTests(unittest.TestCase):
    def test_today_is_a_clock_time_and_another_day_carries_its_date(self):
        now = datetime(2026, 11, 27, 10, 54)
        self.assertEqual(core.reading_stamp(datetime(2026, 11, 27, 9, 5), now), "09:05")
        self.assertEqual(core.reading_stamp(datetime(2026, 10, 19, 23, 12), now), "19 Oct 23:12")
        self.assertEqual(core.reading_stamp(datetime(2026, 11, 5, 23, 12), now), "5 Nov 23:12")


class EmptyUsageTests(unittest.TestCase):
    def texts(self, **kwargs):
        from PIL import ImageDraw
        original, texts = ImageDraw.ImageDraw.text, []

        def record(pen, xy, text, *args, **more):
            texts.append(text)
            return original(pen, xy, text, *args, **more)
        with patch.object(ImageDraw.ImageDraw, "text", record):
            core.render_console([], None, {"tokens": 0, "turns": 0}, [], 0, tab="usage",
                                provider="claude", **kwargs)
        return texts

    def test_the_usage_tab_says_why_there_is_no_reading(self):
        texts = self.texts(notice=("auth", "Sign in to Claude"))
        self.assertIn("Sign in to Claude", texts)
        self.assertNotIn("No usage reading available", texts)

    def test_without_a_reason_it_says_only_that_there_is_none(self):
        self.assertIn("No usage reading available", self.texts())


class WidgetReadingTests(unittest.TestCase):
    def widget(self, metrics, read_at, error=("auth", "Run Claude Code and sign in to connect usage.")):
        w = SmithAgentsWidget.__new__(SmithAgentsWidget)
        w.lock = threading.Lock()
        w.config = {"usage_provider": "claude", "bar_mode": "worst", "console_tab": "usage", "interval": 300}
        w.metrics, w.stats, w.spend, w.plan, w.active = metrics, {}, None, None, []
        w.error, w.updated_at = error, datetime.fromtimestamp(read_at)
        w.agents = []
        w.codex_data = {"metrics": [], "stats": {}, "usage_error": None, "stats_error": None}
        return w

    def test_claude_readings_from_windows_long_reset_are_not_shown(self):
        w = self.widget([reading("session", 0, iso(READ_AT + HOUR)),
                         reading("weekly", 7, iso(READ_AT + 90 * HOUR))], READ_AT)
        with patch("smith_agents.app.time.time", return_value=READ_AT + 38 * 24 * HOUR):
            self.assertEqual(w._snapshot()[0], [])
            # With nothing left to show, the sign-in message stands alone.
            self.assertIsNone(w._data_notice())

    def test_a_claude_reading_whose_window_is_open_stays_and_says_when_it_was_read(self):
        weekly = reading("weekly", 7, iso(READ_AT + 90 * HOUR))
        w = self.widget([reading("session", 0, iso(READ_AT + HOUR)), weekly], READ_AT)
        with patch("smith_agents.app.time.time", return_value=READ_AT + 30 * HOUR):
            self.assertEqual(w._snapshot()[0], [weekly])
            notice = w._data_notice()
        self.assertIn(core.reading_stamp(w.updated_at, datetime.fromtimestamp(READ_AT + 30 * HOUR)), notice)
        self.assertIn("sign in", notice)

    def test_a_reading_from_the_saved_file_is_not_described_twice(self):
        w = self.widget([reading("weekly", 7, iso(READ_AT + 90 * HOUR))], READ_AT,
                        error=("cached", "cached reading from 12:00"))
        with patch("smith_agents.app.time.time", return_value=READ_AT + 2 * HOUR):
            self.assertEqual(w._data_notice().lower().count("cached"), 1)

    def test_codex_readings_from_windows_long_reset_are_not_shown(self):
        weekly = dict(reading("codex:codex:primary", 71, iso(READ_AT + 90 * HOUR)), provider="codex")
        session = dict(reading("codex:codex:secondary", 31, iso(READ_AT + HOUR)), provider="codex")
        w = self.widget([], READ_AT, error=None)
        w.config["usage_provider"] = "codex"
        w.codex_data.update(metrics=[weekly, session], metrics_updated=READ_AT, usage_error="Offline")
        with patch("smith_agents.app.time.time", return_value=READ_AT + 30 * HOUR):
            self.assertEqual(w._snapshot()[0], [weekly])


if __name__ == "__main__":
    unittest.main()

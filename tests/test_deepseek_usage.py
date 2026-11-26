"""DeepSeek saved usage from session logs, its usage tab, and its top-bar lamp."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import ImageDraw

from smith_agents import deepseek_sessions as sessions, deepseek_usage as usage, tucked
from smith_agents.app import SmithAgentsWidget
from test_core import core
from test_desktop_apps import T0, SESSION, asked, event, header, said

OTHER = "session-2b3c4d5e-6f70-4812-9a3b-4c5d6e7f8091"
PROVIDERS = ["provider:claude", "provider:codex", "provider:hermes", "provider:deepseek"]


def log(session=SESSION, start=0, title="Checkout total", cwd="/projects/shop"):
    """Two replies on two models: 1,550 and 220 tokens, five seconds into the session."""
    return [header(session=session, cwd=cwd, createdAt=(T0 + start) * 1000),
            asked(start + 1, "Fix the checkout total"),
            event("session/title", start + 2, title=title),
            event("turn/start", start + 3, turn=1),
            event("tool/call", start + 4, turn=1, step=1, callId="c1", name="bash", arguments="{}"),
            said(start + 5, "One", model="deepseek-v4-pro", inputTokens=1000, outputTokens=100,
                 cacheReadTokens=400, cacheWriteTokens=50, totalTokens=1550),
            said(start + 6, "Two", model="deepseek-flash", inputTokens=200, outputTokens=20,
                 cacheReadTokens=0, cacheWriteTokens=0, totalTokens=220),
            event("turn/end", start + 7, turn=1, reason={"kind": "completed"})]


def overlap(a, b):
    return max(a[1], b[1]) < min(a[3], b[3]) and max(a[2], b[2]) < min(a[4], b[4])


class SessionTotalsTests(unittest.TestCase):
    def test_every_reply_is_counted_once_with_its_cache_as_its_own_tokens(self):
        session = usage.totals(log(), today_start=T0)
        self.assertEqual((session["id"], session["title"], session["cwd"], session["started_at"]),
                         (SESSION, "Checkout total", "/projects/shop", T0))
        self.assertEqual([session[key] for key in ("input_tokens", "output_tokens",
                                                   "cache_read_tokens", "cache_write_tokens", "tokens")],
                         [1200, 120, 400, 50, 1770])
        self.assertEqual((session["api_call_count"], session["tool_call_count"], session["message_count"]),
                         (2, 1, 3))
        # DeepSeek records no price, so no cost is invented.
        self.assertIsNone(session["estimated_cost_usd"])

    def test_models_are_broken_out_by_name(self):
        models = usage.totals(log(), today_start=T0)["models"]
        self.assertEqual([(m["model"], m["tokens"], m["input_tokens"], m["api_call_count"]) for m in models],
                         [("deepseek-flash", 220, 200, 1), ("deepseek-v4-pro", 1550, 1000, 1)])

    def test_today_counts_only_replies_since_midnight(self):
        self.assertEqual(usage.totals(log(), today_start=T0 + 6)["today_tokens"], 220)
        self.assertEqual(usage.totals(log(), today_start=T0 + 60)["today_tokens"], 0)

    def test_reply_without_a_total_adds_its_parts_and_bad_numbers_are_skipped(self):
        lines = log()[:5] + [said(5, "One", inputTokens=10, outputTokens=5, cacheReadTokens=3),
                             said(6, "Two", inputTokens=-4, outputTokens="9", totalTokens=True)]
        session = usage.totals(lines, today_start=T0)
        self.assertEqual((session["tokens"], session["input_tokens"], session["api_call_count"]), (18, 10, 2))

    def test_log_without_a_header_is_refused(self):
        self.assertIsNone(usage.totals(log()[1:], today_start=T0))
        self.assertIsNone(usage.totals([], today_start=T0))


@unittest.skipUnless(sessions.can_read_logs(), "needs zstd from Python 3.14")
class SavedUsageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def save(self, lines, name, project="--projects-shop--"):
        from compression import zstd
        folder = self.root / "sessions" / project / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "session.v4.jsonl.zstd").write_bytes(zstd.compress(b"\n".join(lines) + b"\n"))

    def fetch(self, **kwargs):
        return usage.Reader().fetch(self.root, today_start=kwargs.pop("today_start", T0), **kwargs)

    def test_saved_sessions_are_listed_newest_first_with_todays_total(self):
        self.save(log(), SESSION)
        self.save(log(session=OTHER, start=86400, title="Tax rules", cwd="/projects/blog"), OTHER,
                  "--projects-blog--")
        result = self.fetch(today_start=T0 + 86400)
        self.assertEqual([s["id"] for s in result["sessions"]], [OTHER, SESSION])
        self.assertEqual((result["today_tokens"], result["limited"]), (1770, False))
        chosen = usage.select(result, SESSION)
        self.assertEqual((chosen["provider"], chosen["tokens"], chosen["session"]["title"]),
                         ("deepseek", 1770, "Checkout total"))

    def test_changed_log_is_read_again_and_an_unchanged_one_is_not(self):
        self.save(log(), SESSION)
        reader = usage.Reader()
        self.assertEqual(reader.fetch(self.root, today_start=T0)["sessions"][0]["tokens"], 1770)
        with patch.object(usage, "totals_and_replies", wraps=usage.totals_and_replies) as parse:
            reader.fetch(self.root, today_start=T0)
            parse.assert_not_called()
        self.save(log() + [said(9, "Three", inputTokens=30, totalTokens=30)], SESSION)
        self.assertEqual(reader.fetch(self.root, today_start=T0)["sessions"][0]["tokens"], 1800)

    def test_damaged_log_is_left_out_without_hiding_the_others(self):
        self.save(log(), SESSION)
        self.save(log(session=OTHER), OTHER)
        (self.root / "sessions" / "--projects-shop--" / OTHER / "session.v4.jsonl.zstd").write_bytes(b"junk")
        self.assertEqual([s["id"] for s in self.fetch()["sessions"]], [SESSION])

    def test_only_the_newest_sessions_are_kept_and_the_cut_is_marked(self):
        self.save(log(), SESSION)
        self.save(log(session=OTHER, start=50), OTHER)
        with patch.object(usage, "SESSION_LIMIT", 1):
            result = self.fetch()
        self.assertEqual(([s["id"] for s in result["sessions"]], result["limited"]), ([OTHER], True))
        # Today's total still covers every session, not only the listed ones.
        self.assertEqual(result["today_tokens"], 3540)

    def test_no_saved_sessions_is_unavailable(self):
        with self.assertRaises(usage.Unavailable):
            self.fetch()

    def widget(self):
        self.save(log(), SESSION)
        self.save(log(session=OTHER, start=50, title="Tax rules"), OTHER)
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.lock = threading.Lock()
        widget.config = dict(core.DEFAULTS, usage_provider="deepseek")
        widget.deepseek_data = self.fetch()
        widget.deepseek_session_id = SESSION
        widget.deepseek_model_index = 0
        widget._save_config = Mock()
        widget._repaint = Mock()
        return widget

    def test_usage_tab_pages_through_sessions_and_models(self):
        widget = self.widget()
        data = widget._snapshot()[6]
        self.assertEqual((data["provider"], data["session"]["id"], data["model"]["model"]),
                         ("deepseek", SESSION, "deepseek-flash"))
        _, widget.agent_rows = core.render_console([], data, data, [], 0, tab="usage", provider="deepseek")
        for kind, expected in (("hermes-model", "deepseek-v4-pro"), ("hermes-session", OTHER)):
            box = next(b for b in widget.agent_rows if b[0] == kind and b[-1] == 1)
            widget._on_console_click(SimpleNamespace(x=(box[1] + box[3]) / 2, y=(box[2] + box[4]) / 2))
            data = widget._snapshot()[6]
            self.assertEqual(data["model"]["model"] if kind == "hermes-model" else data["session"]["id"], expected)

    def test_usage_and_stats_never_borrow_another_providers_words(self):
        data = self.widget()._snapshot()[6]
        for tab in ("usage", "stats"):
            pen = Mock(wraps=ImageDraw.Draw(core.console_base((core.CONSOLE_W, core.px(400)))))
            (core.render_usage(pen, [], None, data, 0) if tab == "usage" else core.render_stats(pen, data, 0))
            words = " | ".join(str(call.args[1]) for call in pen.text.call_args_list)
            with self.subTest(tab=tab):
                self.assertNotRegex(words, "Hermes|Nous|est\\.|\\$")
                self.assertIn("Checkout total", words)
        self.assertRegex(core.account_summary([], "deepseek", usage_data=data), r"^\S+ tokens · DeepSeek$")

    def test_header_shows_session_tokens_beside_todays(self):
        data = self.widget()._snapshot()[6]
        chip = core.console_base((core.CONSOLE_W, core.BAR_H))
        pen = Mock(wraps=ImageDraw.Draw(chip))
        core.render_console_bar(chip, pen, [], None, data, {}, 0, False, provider="deepseek")
        texts = {call.args[1] for call in pen.text.call_args_list}
        self.assertTrue({"Tokens", "Today"} <= texts)
        self.assertNotIn("Est. cost", texts)

    def test_large_totals_fit_their_header_field_without_being_cut(self):
        from smith_agents.hermes_usage_ui import _amounts, brief
        for value, expected in ((137676524, "138M"), (25283204, "25.3M"), (1770000, "1.77M"),
                                (320500, "320k"), (1770, "1.77k"), (999, "999"), (None, "—")):
            self.assertEqual(brief(value), expected)
        data = self.widget()._snapshot()[6]
        data["session"]["tokens"], data["today_tokens"] = 137676524, 25283204
        font = core.MONO("bold", 10)
        for text in _amounts(data):
            self.assertEqual(core.elide(text, font, core.px(39)), text)

    def test_tucked_drawer_opens_usage_on_every_edge(self):
        widget = self.widget()
        data = widget._snapshot()[6]
        widget.set_tuck = Mock()
        for edge in tucked.EDGES:
            _, widget.agent_rows, _ = tucked.render([], [], None, 0, side=edge, provider="deepseek",
                                                    usage_open=True, usage_data=data)
            box = next(b for b in widget.agent_rows if b[0] == "peek-hermes-usage")
            widget._on_peek_click(SimpleNamespace(x=(box[1] + box[3]) / 2, y=(box[2] + box[4]) / 2))
            widget.set_tuck.assert_called_with(False)
            self.assertEqual(widget.config["console_tab"], "usage")

    def test_failed_poll_keeps_the_last_reading_and_marks_it(self):
        widget = self.widget()
        widget.stopping = threading.Event()
        widget.deepseek_wake = Mock()
        widget.deepseek_wake.wait.side_effect = lambda _: widget.stopping.set()
        with patch.object(usage, "fetch", side_effect=usage.Unavailable("Logs busy")):
            widget._deepseek_poll_loop()
        self.assertEqual(widget._snapshot()[6]["tokens"], 1770)
        self.assertEqual(widget._snapshot()[4], ("deepseek", "Logs busy"))
        self.assertIn("Last reading", widget._data_notice())


class ProviderSelectorTests(unittest.TestCase):
    def test_selecting_deepseek_never_reuses_another_accounts_readings(self):
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = dict(core.DEFAULTS)
        widget.lock = threading.Lock()
        widget._save_config = Mock()
        widget.set_usage_provider("deepseek")
        self.assertEqual(widget.config["usage_provider"], "deepseek")
        metrics, spend, _plan, _active, _error, _updated, stats = widget._snapshot()
        self.assertEqual((metrics, spend["provider"], stats["session"]), ([], "deepseek", {}))

    def test_console_bar_has_a_lamp_for_each_provider(self):
        for provider in ("claude", "deepseek"):
            chip = core.console_base((core.CONSOLE_W, core.BAR_H))
            boxes = core.render_console_bar(chip, ImageDraw.Draw(chip), [], None, None, {}, 0, False,
                                            provider=provider)
            lamps = [box for box in boxes if box[0].startswith("provider:")]
            self.assertEqual([box[0] for box in lamps], PROVIDERS)
            for index, box in enumerate(lamps):
                self.assertTrue(0 <= box[1] < box[3] <= core.CONSOLE_W and 0 <= box[2] < box[4] <= core.BAR_H)
                self.assertFalse(any(overlap(box, other) for other in lamps[index + 1:]))

    def test_tucked_strip_has_a_lamp_for_each_provider_on_every_edge(self):
        for side in tucked.EDGES:
            image, boxes, _ = tucked.render([], [], None, 200, side=side, provider="deepseek")
            lamps = [box for box in boxes if box[0].startswith("provider:")]
            with self.subTest(side=side):
                self.assertEqual([box[0] for box in lamps], PROVIDERS)
                for index, box in enumerate(lamps):
                    self.assertTrue(0 <= box[1] < box[3] <= image.width and 0 <= box[2] < box[4] <= image.height)
                    # Wide enough to hit: at least a third of the mark each way.
                    self.assertGreaterEqual(min(box[3] - box[1], box[4] - box[2]), core.px(6))
                    self.assertFalse(any(overlap(box, other) for other in lamps[index + 1:]))

    def test_lamp_click_selects_deepseek(self):
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        chip = core.console_base((core.CONSOLE_W, core.BAR_H))
        widget.agent_rows = core.render_console_bar(chip, ImageDraw.Draw(chip), [], None, None, {}, 0,
                                                    False, provider="claude")
        widget.set_usage_provider, widget._repaint = Mock(), Mock()
        box = next(b for b in widget.agent_rows if b[0] == "provider:deepseek")
        widget._on_console_click(SimpleNamespace(x=(box[1] + box[3]) / 2, y=(box[2] + box[4]) / 2))
        widget.set_usage_provider.assert_called_with("deepseek")

    def test_selected_deepseek_lamp_is_lit(self):
        lit, dim = core.provider_lamp("deepseek", True), core.provider_lamp("deepseek", False)
        self.assertGreater(sum(lit.getchannel("A").tobytes()), sum(dim.getchannel("A").tobytes()))

    def test_whale_is_white_on_the_dark_widget(self):
        logo = core._provider_logo("deepseek", core.px(16))
        solid = [pixel[:3] for pixel in logo.getdata() if pixel[3] > 200]
        self.assertGreater(len(solid), 30)
        expected = core._tok("fg")[:3] if core.INVERT else (255, 255, 255)
        self.assertEqual(set(solid), {expected})


if __name__ == "__main__":
    unittest.main()

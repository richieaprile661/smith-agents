import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

_config = tempfile.TemporaryDirectory(prefix="widget-header-tests-")
os.environ["SMITH_AGENTS_CONFIG_DIR"] = _config.name

from PIL import ImageDraw
from smith_agents import core
from smith_agents.app import SmithAgentsWidget
from smith_agents.sample_data import demo_payload


class HeaderReadingTests(unittest.TestCase):
    def test_values_keep_zero_distinct_from_missing_reset(self):
        metric = {"pct": 0, "resets_at": None}
        self.assertEqual(core.header_reading_value(metric, "used"), "0%")
        self.assertEqual(core.header_reading_value(metric, "remaining"), "100%")
        self.assertEqual(core.header_reading_value(metric, "reset"), "—")
        metric.update(pct=71, resets_at="2026-09-12T12:00:00Z")
        self.assertEqual(core.header_reading_value(metric, "remaining"), "29%")
        self.assertEqual(core.header_reading_value(metric, "reset"), core.reset_stamp(metric['resets_at']))

    def test_clicking_gap_cycles_visible_data_without_folding_or_changing_provider(self):
        metrics = core.build_metrics(demo_payload())
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = dict(core.DEFAULTS)
        widget._save_config = Mock()
        widget._repaint = Mock()
        for expanded in (True, False):
            widget.config['console_open'] = expanded
            for expected in ('remaining', 'reset', 'used'):
                _, widget.agent_rows = core.render_console(metrics, None, {}, [], 0,
                    provider='claude', expanded=expanded, reading_view=widget.config['reading_view'])
                # The whitespace between the first two columns is also interactive.
                widget._on_console_click(SimpleNamespace(x=core.px(51), y=core.px(62)))
                self.assertEqual(widget.config['reading_view'], expected)
                self.assertEqual(widget.config['usage_provider'], 'claude')
                self.assertEqual(widget.config['console_open'], expanded)
        self.assertEqual(widget._save_config.call_count, 6)
        self.assertEqual(widget._repaint.call_count, 6)

    def test_tucked_reading_still_cycles_individual_limits(self):
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = dict(core.DEFAULTS, tucked=True)
        widget.agent_rows = [('reading', 0, 0, 100, 100, None)]
        widget._snapshot = Mock(return_value=(core.build_metrics(demo_payload()),))
        widget._save_config = Mock()
        widget._repaint = Mock()
        widget._on_console_click(SimpleNamespace(x=50, y=50))
        self.assertEqual(widget.config['bar_mode'], 'session')
        self.assertEqual(widget.config['reading_view'], 'used')

    def test_header_text_stays_below_provider_buttons_and_inside_columns(self):
        metrics = core.build_metrics(demo_payload())
        metrics[0].update(pct=100, resets_at='2026-09-10T14:00:00Z')
        for view in core.READING_VIEWS:
            chip = core.console_base((core.CONSOLE_W, core.BAR_H))
            draw = ImageDraw.Draw(chip)
            texts = []

            class RecordingPen:
                def __getattr__(self, name):
                    return getattr(draw, name)

                def text(self, xy, text, **kwargs):
                    texts.append((text, draw.textbbox(xy, text, font=kwargs['font'])))
                    return draw.text(xy, text, **kwargs)

            pen = RecordingPen()
            core.render_header_numbers(pen, metrics, metrics[0], {}, 'worst', None, view)
            buttons = core.render_provider_switch(chip, pen, 'claude')
            for text, bounds in texts[:7]:
                self.assertGreater(bounds[1], buttons[0][4], text)
                self.assertLessEqual(bounds[2], core.px(138), text)
                self.assertLess(bounds[3], core.BAR_H, text)


if __name__ == '__main__':
    unittest.main()

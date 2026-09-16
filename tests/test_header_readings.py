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

    def test_account_strip_opens_usage_without_changing_reading_or_provider(self):
        metrics = core.build_metrics(demo_payload())
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = dict(core.DEFAULTS, console_tab='agents', console_open=True)
        widget._save_config = Mock()
        widget._repaint = Mock()
        _, widget.agent_rows = core.render_console(metrics, None, {}, [], 0, provider='claude')
        box = next(b for b in widget.agent_rows if b[0] == 'account')
        widget._on_console_click(SimpleNamespace(x=(box[1]+box[3])/2, y=(box[2]+box[4])/2))
        self.assertEqual(widget.config['console_tab'], 'usage')
        self.assertEqual(widget.config['reading_view'], 'used')
        self.assertEqual(widget.config['usage_provider'], 'claude')
        self.assertTrue(widget.config['console_open'])
        widget._save_config.assert_called_once()
        widget._repaint.assert_called_once()

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

    def test_header_has_only_bare_provider_lamps_and_no_usage(self):
        metrics = core.build_metrics(demo_payload())
        for provider in ('claude', 'codex'):
            chip = core.console_base((core.CONSOLE_W, core.BAR_H))
            pen = Mock(wraps=ImageDraw.Draw(chip))
            boxes = core.render_console_bar(chip, pen, metrics, metrics[0], None, {}, 0,
                                            False, provider=provider)
            self.assertEqual([call.args[1] for call in pen.text.call_args_list], ['Smith'])
            pen.rounded_rectangle.assert_not_called()
            self.assertNotIn('reading', [box[0] for box in boxes])
            self.assertEqual([box[0] for box in boxes[:2]], ['provider:claude', 'provider:codex'])
            widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
            widget.agent_rows = boxes
            widget.set_usage_provider, widget._repaint = Mock(), Mock()
            for box in boxes[:2]:
                widget._on_console_click(SimpleNamespace(x=(box[1]+box[3])/2, y=(box[2]+box[4])/2))
                widget.set_usage_provider.assert_called_with(box[0].partition(':')[2])
            active, inactive = core.provider_lamp(provider, True), core.provider_lamp(provider, False)
            self.assertGreater(sum(active.getchannel('A').tobytes()), sum(inactive.getchannel('A').tobytes()))

    def test_smith_and_agents_are_live_text_with_the_same_font_and_size(self):
        chip = core.console_base((core.CONSOLE_W, core.BAR_H + core.TAB_H))
        pen = Mock(wraps=ImageDraw.Draw(chip))
        with patch.object(chip, 'alpha_composite', wraps=chip.alpha_composite) as composite:
            core.render_console_bar(chip, pen, [], None, None, {'done': 4}, 0, True,
                                    provider='claude', header_frame=0)
            core.render_tabs(chip, pen, 'agents', 0)
        labels = {call.args[1]: call for call in pen.text.call_args_list}
        smith, agents = labels['Smith'], labels['Agents']
        self.assertIs(smith.kwargs['font'], agents.kwargs['font'])
        self.assertEqual(smith.kwargs['font'].size, core.px(16))
        self.assertEqual(smith.args[0][0], agents.args[0][0])
        self.assertAlmostEqual(smith.args[0][0], core.CONSOLE_W/6, delta=core.px(1))
        self.assertEqual(smith.kwargs['anchor'], 'ms')
        self.assertEqual(agents.kwargs['anchor'], 'ms')
        self.assertNotIn('Ready', labels)
        pen.ellipse.assert_not_called()
        bounds = pen.textbbox(smith.args[0], 'Smith', font=smith.kwargs['font'], anchor='ms')
        self.assertLess(bounds[3], core.BAR_H)
        figure, (figure_x, figure_y) = composite.call_args_list[0].args
        self.assertLessEqual(figure_y+figure.height, bounds[1])
        self.assertAlmostEqual(figure_x+figure.width/2, smith.args[0][0], delta=1)



if __name__ == '__main__':
    unittest.main()

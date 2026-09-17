import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_core import core
from smith_agents.app import SmithAgentsWidget
from smith_agents.codex_usage import build_metrics


class QuietCardTests(unittest.TestCase):
    def test_panel_chevron_folds_reply_and_details_while_preserving_summary(self):
        row = dict(core.demo_agents()[2], name='smith-agents', provider='codex')
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = dict(core.DEFAULTS)
        widget.agents, widget.agent_open = [row], None
        widget._repaint, widget._reveal_agent = Mock(), Mock()
        folded, widget.agent_rows = core.render_console([], None, {}, [row], 0)
        for expanded in (True, False):
            box = next(b for b in widget.agent_rows if b[0] == 'row')
            widget._on_console_click(SimpleNamespace(x=box[3]-core.px(5), y=(box[2]+box[4])/2))
            self.assertEqual(widget.agent_open, row['id'] if expanded else None)
            image, widget.agent_rows = core.render_console([], None, {}, [row], 0, open_id=widget.agent_open)
            self.assertFalse(any(b[0] == 'kill' for b in widget.agent_rows))
            self.assertEqual(any(b[0] == 'details' for b in widget.agent_rows), expanded)
            self.assertTrue(any(b[0] == 'open' for b in widget.agent_rows))
            if expanded:
                self.assertGreater(image.height, folded.height)
            else:
                self.assertEqual(image.height, folded.height)

    def test_read_more_below_session_action_defaults_closed_and_toggles(self):
        row = dict(core.demo_agents()[2], _window_open=True)
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = dict(core.DEFAULTS)
        widget.agents, widget.agent_open = [row], row['id']
        widget._repaint = Mock()
        collapsed, widget.agent_rows = core.render_console([], None, {}, [row], 0, open_id=row['id'])
        self.assertTrue(any(b[0] == 'kill' for b in widget.agent_rows))
        self.assertFalse(any(b[0] == 'hide' for b in widget.agent_rows))
        action = next(b for b in widget.agent_rows if b[0] == 'open')
        for expanded in (True, False):
            box = next(b for b in widget.agent_rows if b[0] == 'details')
            self.assertGreater(box[2], action[4])
            widget._on_console_click(SimpleNamespace(x=(box[1]+box[3])/2, y=(box[2]+box[4])/2))
            image, widget.agent_rows = core.render_console([], None, {}, [row], 0, open_id=row['id'])
            self.assertEqual(row['_details_expanded'], expanded)
            self.assertTrue(any(b[0] == 'kill' for b in widget.agent_rows))
            self.assertEqual(any(b[0] == 'hide' for b in widget.agent_rows), expanded)
            self.assertEqual(image.height > collapsed.height, expanded)
            self.assertEqual(widget.agent_open, row['id'])

    def test_ready_and_activity_are_below_context_and_logo_above_figure(self):
        from PIL import ImageDraw
        for provider in ('claude', 'codex'):
            row = dict(core.demo_agents()[2], provider=provider, idle=120)
            chip = core.console_base((core.CONSOLE_W, core.agent_row_height(row)))
            pen = Mock(wraps=ImageDraw.Draw(chip))
            with patch.object(core, 'draw_provider_badge', wraps=core.draw_provider_badge) as badge:
                core.render_row(pen, chip, row, 0, 0, False, False)
            layout = core.quiet_card_layout(row)
            calls = pen.text.call_args_list
            ready = next(c for c in calls if c.args[1] == 'Ready')
            stamp = next(c for c in calls if c.args[1] == core.agent_activity_stamp(row))
            self.assertGreater(ready.args[0][1], layout['track_y']+core.px(3))
            self.assertEqual(ready.args[0][0], core.PAD_X+core.px(10))
            self.assertGreater(stamp.args[0][0], ready.args[0][0]+core.text_w('Ready', ready.kwargs['font']))
            self.assertEqual(badge.call_args.args[2:], (core.PAD_X-core.px(4), core.px(4)))

    def test_finished_helpers_are_compact_and_their_result_is_expandable(self):
        from PIL import ImageDraw
        parent = dict(core.demo_agents()[2], id='parent')
        helper = dict(parent, id='helper', parent='parent', sub=True, name='Test helper',
                      latest_message='All requested checks passed.',
                      activity={'status': 'completed', 'result': 'All requested checks passed.'})
        self.assertLess(core.agent_row_height(helper), core.agent_row_height(parent))
        _, collapsed = core.render_console([], None, {}, [parent, helper], 0)
        self.assertEqual([b[0] for b in collapsed if b[-1] is helper], ['row'])
        text = []
        original = ImageDraw.ImageDraw.text
        def record(pen, xy, value, *args, **kwargs):
            text.append(value)
            return original(pen, xy, value, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', record):
            _, expanded = core.render_console([], None, {}, [parent, helper], 0, open_id='helper')
        self.assertIn('1 session · 1 helper', text)
        self.assertIn('All requested checks passed.', text)
        self.assertTrue(any(b[0] == 'open' and b[-1] is helper for b in expanded))
        helper['state'] = 'working'
        self.assertFalse(core.compact_helper(helper))

    def test_last_activity_never_displays_turn_duration(self):
        row = dict(core.demo_agents()[2], idle=120,
                   activity={'status': 'completed', 'started_at': 100, 'ended_at': 5920})
        self.assertEqual(core.agent_activity_stamp(row), 'Last activity 2m 00s ago')
        row['idle'] = None
        self.assertEqual(core.agent_activity_stamp(row), 'Activity unknown')

    def test_long_reply_expands_within_unfolded_panel_and_survives_rescan(self):
        row = dict(core.demo_agents()[1], state='done',
                   latest_message='**First paragraph.**\n' + 'A long readable reply. ' * 40)
        row.pop('activity', None)
        collapsed = core.agent_row_height(row, True)
        _, boxes = core.render_console([], None, {}, [row], 0, open_id=row["id"])
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.agent_rows, widget.agents = boxes, [row]
        widget.agent_open = row["id"]
        widget.config = dict(core.DEFAULTS)
        widget._repaint = Mock()
        widget._figure_assignments = Mock()
        widget._sync_agent_windows = Mock()
        box = next(b for b in boxes if b[0] == 'reply')
        widget._on_console_click(SimpleNamespace(x=(box[1]+box[3])/2, y=(box[2]+box[4])/2))
        self.assertEqual(widget.agent_open, row["id"])
        self.assertGreater(core.agent_row_height(row, True), collapsed)
        self.assertIn('First paragraph.', core.quiet_card_layout(row)['lines'])
        row['_details_expanded'] = True
        fresh = dict(row)
        fresh.pop('_reply_expanded')
        fresh.pop('_details_expanded')
        with patch('smith_agents.app.list_agents', return_value=[fresh]), \
             patch('smith_agents.app.decorate_agents', side_effect=lambda rows: rows):
            widget._apply_agent_scan([fresh])
        self.assertTrue(widget.agents[0]['_reply_expanded'])
        self.assertTrue(widget.agents[0]['_details_expanded'])
        fields = core.agent_drawer_layout(row)[0]
        self.assertFalse(any(title in ('Latest message', 'Latest reply') for title, *_ in fields))

    def test_markdown_prose_is_readable_and_commands_are_not_rewritten(self):
        self.assertEqual(core.reply_text('## Result\n**Done** with `file.py`\n- one\n```sh\necho **/*.py\n```'),
                         'Result\nDone with file.py\n• one\necho **/*.py')
        command = 'echo **/*.py && printf "__literal__"'
        row = dict(core.demo_agents()[0], permissions=[{'actionable': True,
                   'request': {'tool_name': 'Bash', 'input': {'command': command}}}])
        layout = core.quiet_card_layout(row)
        self.assertIn('**/*.py', ' '.join(layout['lines']))
        self.assertEqual(row['permissions'][0]['request']['input']['command'], command)

    def test_account_strip_keeps_missing_zero_stale_and_scoped_limits_distinct(self):
        metrics = build_metrics({'rateLimits': {
            'primary': {'usedPercent': 8, 'windowDurationMins': 300},
            'secondary': {'usedPercent': 0, 'windowDurationMins': 10080}}})
        self.assertEqual(core.account_summary(metrics, 'codex'), 'Weekly · 0% used · Codex')
        self.assertTrue(core.account_summary(metrics, 'codex', data_notice='offline').startswith('Last reading · '))
        self.assertEqual(core.account_summary([], 'codex'), 'Account usage unavailable · Codex')
        self.assertEqual(core.account_summary([], 'claude', ('auth', 'Sign in to Claude')),
                         'Sign in to Claude · Claude')
        scoped = dict(metrics[-1], key='codex:spark:secondary', detail='Spark · 1w')
        self.assertIn('Spark · 1w', core.account_summary([scoped], 'codex'))


if __name__ == '__main__':
    unittest.main()

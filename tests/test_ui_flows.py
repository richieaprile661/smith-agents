import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import ImageDraw
from test_core import core as c
from smith_agents import tucked
from smith_agents.app import SmithAgentsWidget
from smith_agents.sample_data import demo_payload


class UserFlowTests(unittest.TestCase):
    def test_window_setup_is_skipped_in_demo_and_during_shutdown(self):
        w = SmithAgentsWidget.__new__(SmithAgentsWidget)
        w.config, w._save_config, w.stopping = {}, Mock(), Mock()
        setup = Mock()
        with patch("smith_agents.app.platform", SimpleNamespace(offer_accessibility_setup=setup)):
            w.demo = True
            w.stopping.is_set.return_value = False
            w.setup_window_controls()
            setup.assert_not_called()
            w.demo = False
            w.stopping.is_set.return_value = True
            w.setup_window_controls()
            setup.assert_not_called()
            w.stopping.is_set.return_value = False
            w.setup_window_controls(force=True)
            setup.assert_called_once_with(w.config, w._save_config, force=True)

    def widget(self, side, count=20, height=420):
        w = SmithAgentsWidget.__new__(SmithAgentsWidget)
        w.config = dict(c.DEFAULTS, tucked=True, tuck_side=side)
        w.agents = [dict(c.demo_agents()[1], id=str(i), name='Shared project', provider='claude')
                    for i in range(count)]
        w.metrics = c.build_metrics(demo_payload())
        w._snapshot = lambda: (w.metrics,)
        w._peek_open_id = None
        w._peek_scroll = w._peek_panel_scroll = 0
        w._peek_details = True
        w._confirm_kill = None
        w._drag = None
        w._paint_xy = (0, 0)
        w._bar_size = (c.CONSOLE_W, c.BAR_H)
        w._screen_bounds = lambda: (0, 0, c.px(900), c.px(height))
        w.root = SimpleNamespace(after=Mock(), winfo_x=lambda: 0, winfo_y=lambda: 0)
        w._save_config = Mock()
        w._sync_agent_windows = Mock()

        def paint():
            _, boxes, w._peek_layout = tucked.render(w.agents, w.metrics, w.metrics[0], 4,
                side=side, provider=w.config['usage_provider'], mode=w.config['bar_mode'],
                selected=w._peek_open_id, scroll=w._peek_scroll, panel_scroll=w._peek_panel_scroll,
                details=w._peek_details, confirm_id=w._confirm_kill,
                usage_open=getattr(w, '_peek_usage_open', False),
                max_width=c.px(900), max_height=c.px(height))
            w.agent_rows = [(kind, x0+c.SHADOW_PAD, y0+c.SHADOW_PAD,
                            x1+c.SHADOW_PAD, y1+c.SHADOW_PAD, agent)
                           for kind, x0, y0, x1, y1, agent in boxes]
        w._repaint = Mock(side_effect=paint)
        paint()
        return w

    def click(self, w, kind, identity=None):
        box = next(b for b in w.agent_rows if b[0] == kind
                   and (identity is None or b[-1]['id'] == identity))
        x, y = (box[1]+box[3])/2, (box[2]+box[4])/2
        event = SimpleNamespace(x=x, y=y, x_root=x, y_root=y)
        w._on_press(event)
        w._on_release(event)

    def reveal(self, w, kind):
        # Actions can be above the current position after inspecting details.
        while not any(b[0] == kind for b in w.agent_rows) and w._peek_panel_scroll:
            self.click(w, 'peek-panel-up')
        while not any(b[0] == kind for b in w.agent_rows):
            previous = w._peek_panel_scroll
            self.click(w, 'peek-panel-down')
            self.assertGreater(w._peek_panel_scroll, previous)

    def test_individual_open_hide_confirm_cancel_and_close_on_every_edge(self):
        for side in tucked.EDGES:
            with self.subTest(side=side):
                w = self.widget(side)
                self.click(w, 'peek-agent', '1')
                self.reveal(w, 'open')
                with patch('smith_agents.app.raise_agent_window', return_value=True) as opened, \
                     patch('smith_agents.app.hide_agent_window', return_value=True) as hidden, \
                     patch('smith_agents.app.terminate_agent', return_value=True) as ended:
                    self.click(w, 'open')
                    opened.assert_called_once_with(w.agents[1])
                    # Ending the session is available before opening the
                    # optional details (the original regression).
                    self.assertFalse(w.agents[1].get('_details_expanded', False))
                    self.reveal(w, 'kill')
                    self.click(w, 'kill')
                    self.assertEqual(w._confirm_kill, '1')
                    self.reveal(w, 'no')
                    self.click(w, 'no')
                    ended.assert_not_called()
                    w.agents[1]['_window_open'] = True
                    w._repaint()
                    self.reveal(w, 'details')
                    self.click(w, 'details')
                    self.reveal(w, 'hide')
                    self.click(w, 'hide')
                    hidden.assert_called_once_with(w.agents[1])
                    ended.assert_not_called()
                    self.reveal(w, 'kill')
                    self.click(w, 'kill')
                    self.assertEqual(w._confirm_kill, '1')
                    self.reveal(w, 'no')
                    self.click(w, 'no')
                    ended.assert_not_called()
                    self.reveal(w, 'kill')
                    self.click(w, 'kill')
                    self.reveal(w, 'yes')
                    self.click(w, 'yes')
                    ended.assert_called_once_with(w.agents[1])
                self.click(w, 'peek-close')
                self.assertIsNone(w._peek_open_id)
                self.assertTrue(w.config['tucked'])
                self.assertEqual(len(w.agents), 20)

    def test_clicking_the_face_raises_and_the_name_opens_without_untucking(self):
        for side in tucked.EDGES:
            with self.subTest(side=side):
                w = self.widget(side)
                with patch('smith_agents.app.raise_agent_window', return_value=True) as raised:
                    self.click(w, 'peek-open', '1')
                raised.assert_called_once_with(w.agents[1])
                self.assertIsNone(w._peek_open_id)
                self.assertTrue(w.config['tucked'])
                self.click(w, 'peek-agent', '1')
                self.assertEqual(w._peek_open_id, '1')
                self.assertTrue(w.config['tucked'])

    def test_provider_lamps_toggle_usage_drawer_and_cycle_without_untucking(self):
        for side in tucked.EDGES:
            for provider in ('codex', 'claude'):
                with self.subTest(side=side, provider=provider):
                    w = self.widget(side)
                    w.config.update(console_open=False, console_tab='stats', reading_view='used')
                    self.click(w, 'peek-agent', '0')
                    self.click(w, 'provider:'+provider)
                    self.assertEqual(w.config['usage_provider'], provider)
                    self.assertTrue(w.config['tucked'])
                    self.assertTrue(w._peek_usage_open)
                    self.assertFalse(w.config['console_open'])
                    self.assertEqual(w.config['console_tab'], 'stats')
                    self.assertIsNone(w._peek_open_id)
                    self.assertIsNone(w._confirm_kill)
                    first = c.bar_reading(w.metrics, w.metrics[0], w.config['bar_mode'])
                    self.click(w, 'peek-usage-cycle')
                    second = c.bar_reading(w.metrics, w.metrics[0], w.config['bar_mode'])
                    self.assertNotEqual(first['key'], second['key'])
                    self.click(w, 'provider:'+provider)
                    self.assertFalse(w._peek_usage_open)
                    self.click(w, 'provider:'+provider)
                    self.assertTrue(w._peek_usage_open)
                    self.click(w, 'peek-usage-close')
                    self.assertFalse(w._peek_usage_open)
                    self.assertTrue(w.config['tucked'])

    def test_scrolling_and_expand_on_every_edge(self):
        for side in tucked.EDGES:
            with self.subTest(side=side):
                w = self.widget(side)
                self.click(w, 'peek-agent', '0')
                panel_scroll = w._peek_panel_scroll
                rail = w._peek_layout.rail
                w._on_agent_wheel(SimpleNamespace(x=(rail[0]+rail[2])/2, y=(rail[1]+rail[3])/2, delta=-120))
                self.assertGreater(w._peek_scroll, 0)
                self.assertEqual(w._peek_panel_scroll, panel_scroll)
                while w._peek_scroll < w._peek_layout.rail_scroll_max:
                    self.click(w, 'peek-down')
                self.click(w, 'peek-agent', '19')
                self.assertEqual(w._peek_open_id, '19')
                self.click(w, 'peek-agent', '19')
                self.assertIsNone(w._peek_open_id)
                self.click(w, 'peek-expand')
                self.assertFalse(w.config['tucked'])

    def test_permission_buttons_target_only_the_selected_agent_on_every_edge(self):
        for side in tucked.EDGES:
            with self.subTest(side=side):
                w = self.widget(side, count=4)
                item = {'actionable': True, 'request_id': 'qa-request',
                        'request': {'tool_name': 'Bash', 'input': {'command': 'echo QA'}}}
                w.agents[2].update(state='needs', permissions=[item])
                w._repaint()
                self.click(w, 'peek-agent', '2')
                with patch('smith_agents.app.platform.review_permission', return_value='allow') as review, \
                     patch('smith_agents.app.answer_permission', return_value={'ok': True}) as answer:
                    self.reveal(w, 'permission')
                    self.click(w, 'permission')
                    review.assert_called_once_with(w.agents[2], item)
                    self.assertEqual(answer.call_args.args[1:], (item, 'allow'))
                    self.reveal(w, 'permission-deny')
                    self.click(w, 'permission-deny')
                    self.assertEqual(answer.call_args.args[1:], (item, 'deny'))

    def test_strip_has_icon_only_branding_and_independent_lamps_on_every_edge(self):
        original = ImageDraw.ImageDraw.text
        for side in tucked.EDGES:
            with self.subTest(side=side):
                texts = []
                metric = {'key': 'codex:spark', 'pct': 100,
                          'label': 'GPT-5.3-Codex-Spark · 1w', 'header_label': 'Spark'}
                def record(pen, xy, text, *args, **kwargs):
                    texts.append(text)
                    return original(pen, xy, text, *args, **kwargs)
                with patch.object(ImageDraw.ImageDraw, 'text', record):
                    _, boxes, _ = tucked.render([], [metric], metric, 0, side=side, provider='codex')
                self.assertFalse({'Smith', 'Agents'} & set(texts))
                self.assertNotIn('Open widget', texts)
                self.assertFalse(any('100' in text or 'Spark' in text or 'used' in text for text in texts))
                for provider in ('claude', 'codex'):
                    box = next(b for b in boxes if b[0] == 'provider:'+provider)
                    x, y = (box[1]+box[3])/2, (box[2]+box[4])/2
                    hits = [b[0] for b in boxes if b[1] < x < b[3] and b[2] < y < b[4]]
                    self.assertEqual(hits, ['provider:'+provider])

    def test_wrapped_names_leave_all_controls_reachable_on_small_screens(self):
        for side in tucked.EDGES:
            with self.subTest(side=side):
                w = self.widget(side, count=1, height=240)
                w.agents[0]['name'] = 'a-project-' + ('long-name-' * 8)
                w._repaint()
                self.click(w, 'peek-agent', '0')
                self.assertLessEqual(w._peek_layout.panel[3]-c.SHADOW_PAD, c.px(240))
                self.reveal(w, 'details')
                self.click(w, 'details')
                w._peek_panel_scroll = 0
                w._repaint()
                # Each press advances by at most half a viewport, so controls are not skipped.
                seen = set()
                while True:
                    seen.update(b[0] for b in w.agent_rows)
                    if w._peek_panel_scroll >= w._peek_layout.panel_scroll_max:
                        break
                    self.click(w, 'peek-panel-down')
                self.assertTrue({'open', 'kill'} <= seen)


if __name__ == '__main__':
    unittest.main()

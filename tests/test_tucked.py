import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from smith_agents import core, tucked, figure_actions as motion
from smith_agents.app import SmithAgentsWidget


def agents(count):
    sample = core.demo_agents()[1]
    return [dict(sample, id=str(i), name='Agent %d' % i,
                 state=('working', 'needs', 'done')[i % 3]) for i in range(count)]


class TuckedTests(unittest.TestCase):
    def render(self, rows, **kwargs):
        return tucked.render(rows, [], None, 0, figure_elapsed=lambda a: 4, **kwargs)

    def test_action_repeats_each_minute_then_returns_to_idle(self):
        for minute in range(5):
            start = minute * 60
            self.assertEqual(motion.frame_at(motion.periodic_elapsed(start)), 0)
            self.assertLess(motion.frame_at(motion.periodic_elapsed(start + 2)), motion.ACTION_FRAMES)
            for seconds in (3.2, 15, 59.99):
                self.assertGreaterEqual(motion.frame_at(motion.periodic_elapsed(start + seconds)), motion.ACTION_FRAMES)
        self.assertGreaterEqual(motion.frame_at(60), motion.ACTION_FRAMES)

    def test_top_scrolls_horizontally_with_fixed_counts_and_reading(self):
        rows = agents(20)
        first, top_boxes, layout = self.render(rows, side='top', max_width=core.px(700))
        last, end_boxes, end_layout = self.render(rows, side='top', max_width=core.px(700), scroll=10**6)
        self.assertTrue(layout.horizontal)
        self.assertGreater(layout.rail_scroll_max, 0)
        self.assertEqual(first.size, last.size)
        self.assertEqual(layout.rail, end_layout.rail)
        for kind in ('peek-expand', 'reading'):
            box = next(b for b in top_boxes if b[0] == kind)
            self.assertEqual(box, next(b for b in end_boxes if b[0] == kind))
            p = core.SHADOW_PAD
            crop = (box[1]+p, box[2]+p, box[3]+p, box[4]+p)
            self.assertEqual(first.crop(crop).tobytes(), last.crop(crop).tobytes())
        self.assertEqual([b[-1]['id'] for b in end_boxes if b[0] == 'peek-agent'][-1], '19')

    def test_top_selected_panel_opens_down_and_does_not_move_the_strip(self):
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = {'tuck_side': 'top'}
        widget._screen_bounds = lambda: (-core.px(1200), core.px(30), core.px(1200), core.px(650))
        origins = []
        for selected in (None, '0', '3'):
            image, boxes, layout = self.render(agents(4), side='top', selected=selected,
                                               max_width=core.px(1200), max_height=core.px(620), rail_x=core.px(550))
            widget._peek_layout = layout
            x, y = widget._peek_position(image.size)
            origins.append((x+layout.rail[0], y+layout.rail[1]))
            if selected:
                self.assertGreater(layout.panel[1], layout.rail[3])
                self.assertLessEqual(image.height-2*core.SHADOW_PAD, core.px(620))
                actions = [b for b in boxes if b[-1] and b[0] != 'peek-agent']
                self.assertEqual({b[-1]['id'] for b in actions}, {selected})
        self.assertEqual(len(set(origins)), 1)
        self.assertEqual(origins[0][1], core.px(30))

    def test_only_top_and_two_sides_are_drag_destinations(self):
        bounds = (-1000, 30, 1000, 800)
        for point, edge in (((-999, 400), 'left'), ((-1, 400), 'right'),
                            ((-500, 32), 'top'), ((-900, 829), 'left')):
            self.assertEqual(tucked.nearest_edge(bounds, *point), edge)
        self.assertNotIn('bottom', tucked.EDGES)

    def test_tucked_drag_selects_edge_saves_position_and_does_not_click(self):
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = dict(core.DEFAULTS, tucked=True, tuck_side='left')
        widget._peek_layout = tucked.Layout((64, 64, 280, 500), None, 0, 0)
        widget._paint_xy = (100, 100)
        widget.root = SimpleNamespace(winfo_x=lambda: 100, winfo_y=lambda: 100)
        widget._screen_bounds = lambda: (0, 0, 2000, 1200)
        widget._repaint = Mock()
        widget._save_config = Mock()
        widget._on_peek_click = Mock()
        event = SimpleNamespace(x=100, y=100, x_root=200, y_root=200)
        widget._on_press(event)
        end = SimpleNamespace(x=100, y=100, x_root=1000, y_root=2)
        widget._on_drag(end)
        self.assertEqual(widget.config['tuck_side'], 'top')
        self.assertIsNone(widget._peek_open_id)
        widget._on_release(end)
        widget._on_peek_click.assert_not_called()
        widget._save_config.assert_called_once()
        self.assertIsNone(widget._drag)
        self.assertIsNone(widget._peek_drag_point)
        self.assertEqual((widget.config['tuck_x'], widget.config['tuck_y']), (164, 164))

    def test_tucked_click_without_movement_still_opens_the_agent(self):
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = dict(core.DEFAULTS, tucked=True)
        widget._peek_layout = tucked.Layout((64, 64, 280, 500), None, 0, 0)
        widget._paint_xy = (100, 100)
        widget.root = SimpleNamespace(winfo_x=lambda: 100, winfo_y=lambda: 100)
        widget._repaint = Mock()
        widget._on_peek_click = Mock()
        event = SimpleNamespace(x=100, y=100, x_root=200, y_root=200)
        widget._on_press(event)
        widget._on_release(event)
        widget._on_peek_click.assert_called_once_with(event)

    def test_saved_top_edge_and_coordinates_survive_loading(self):
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        with patch('smith_agents.app.json.load', return_value=dict(core.DEFAULTS, tucked=True,
                     tuck_side='top', tuck_x=-1200, tuck_y=50)), patch('builtins.open'):
            loaded = widget._load_config()
        self.assertEqual((loaded['tuck_side'], loaded['tuck_x'], loaded['tuck_y']), ('top', -1200, 50))

    def test_rescans_keep_targets_stable_and_helpers_grouped(self):
        order = tucked.AgentOrder()
        rows = agents(3)
        helper = dict(rows[0], id='helper', sub=True, parent='0')
        self.assertEqual([a['id'] for a in order.sync(rows + [helper])], ['0', 'helper', '1', '2'])
        rows[0]['state'] = 'done'
        self.assertEqual([a['id'] for a in order.sync([helper] + rows[::-1])], ['0', 'helper', '1', '2'])
        rows[1]['state'] = 'closed'
        self.assertEqual([a['id'] for a in order.sync(rows + [helper])], ['0', 'helper', '2'])

    def test_short_list_fits_and_twenty_scroll_with_fixed_header_and_usage(self):
        _, boxes, layout = self.render(agents(7))
        self.assertEqual(layout.rail_scroll_max, 0)
        self.assertEqual(len([b for b in boxes if b[0] == 'peek-agent']), 7)
        image, top, layout = self.render(agents(20))
        bottom_image, bottom, bottom_layout = self.render(agents(20), scroll=10**6)
        self.assertGreater(layout.rail_scroll_max, 0)
        self.assertEqual(image.size, bottom_image.size)
        self.assertEqual(layout.rail, bottom_layout.rail)
        for kind in ('peek-expand', 'reading'):
            a = next(b for b in top if b[0] == kind)
            b = next(b for b in bottom if b[0] == kind)
            self.assertEqual(a, b)
            p = core.SHADOW_PAD
            rect = (a[1]+p, a[2]+p, a[3]+p, a[4]+p)
            self.assertEqual(image.crop(rect).tobytes(), bottom_image.crop(rect).tobytes())
        self.assertEqual([b[-1]['id'] for b in bottom if b[0] == 'peek-agent'][-1], '19')

    def test_only_selected_agent_has_panel_actions_and_rail_stays_anchored(self):
        rows = agents(12)
        for side in ('left', 'right'):
            widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
            widget.config = {'tuck_side': side}
            widget._screen_bounds = lambda: (0, 0, core.px(1400), core.px(900))
            origins = []
            for selected in (None, '0', '11'):
                im, boxes, layout = self.render(rows, side=side, selected=selected,
                                                max_height=core.px(750), rail_y=core.px(100))
                widget._peek_layout = layout
                x, y = widget._peek_position(im.size)
                origins.append((x+layout.rail[0], y+layout.rail[1]))
                actions = [b for b in boxes if b[-1] and b[0] != 'peek-agent']
                self.assertEqual({b[-1]['id'] for b in actions}, {selected} if selected else set())
                if selected:
                    self.assertIn('open', [b[0] for b in actions])
                    self.assertIn('kill', [b[0] for b in actions])
            self.assertEqual(origins[0], origins[1])
            self.assertEqual(origins[0], origins[2])

    def test_small_screen_scrolls_panel_and_clips_actions(self):
        rows = agents(20)
        _, boxes, layout = self.render(rows, selected='0', max_height=core.px(240))
        self.assertGreater(layout.panel_scroll_max, 0)
        self.assertNotIn('kill', [b[0] for b in boxes])
        im, boxes, layout = self.render(rows, selected='0', max_height=core.px(240), panel_scroll=10**6)
        self.assertIn('kill', [b[0] for b in boxes])
        self.assertLessEqual(im.height-2*core.SHADOW_PAD, core.px(240))
        for kind, x0, y0, x1, y1, agent in boxes:
            self.assertTrue(0 <= x0 < x1 <= im.width-2*core.SHADOW_PAD, kind)
            self.assertTrue(0 <= y0 < y1 <= im.height-2*core.SHADOW_PAD, kind)

    def test_confirmation_and_helper_controls(self):
        rows = agents(2)
        _, boxes, _ = self.render(rows, selected='0', confirm_id='0')
        self.assertTrue({'yes', 'no'} <= {b[0] for b in boxes})
        self.assertNotIn('kill', [b[0] for b in boxes])
        rows[1].update(sub=True, parent='0')
        _, boxes, _ = self.render(rows, selected='1')
        self.assertNotIn('kill', [b[0] for b in boxes])

    def test_controller_selects_switches_and_closes_without_untucking(self):
        rows = agents(3)
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = {'tucked': True}
        widget._peek_open_id = None
        widget._repaint = Mock()
        widget.set_tuck = Mock()
        def draw_and_click(identity):
            _, boxes, widget._peek_layout = self.render(rows, selected=widget._peek_open_id)
            widget.agent_rows = boxes
            box = next(b for b in boxes if b[0] == 'peek-agent' and b[-1]['id'] == identity)
            widget._on_peek_click(SimpleNamespace(x=(box[1]+box[3])/2, y=(box[2]+box[4])/2))
        draw_and_click('0')
        self.assertEqual(widget._peek_open_id, '0')
        draw_and_click('1')
        self.assertEqual(widget._peek_open_id, '1')
        draw_and_click('1')
        self.assertIsNone(widget._peek_open_id)
        widget.set_tuck.assert_not_called()

    def test_panel_action_dispatch_targets_only_selected_session(self):
        rows = agents(3)
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.config = {'tucked': True}
        widget._peek_open_id = '1'
        widget._repaint = Mock()
        widget._sync_agent_windows = Mock()
        _, widget.agent_rows, widget._peek_layout = self.render(rows, selected='1')
        box = next(b for b in widget.agent_rows if b[0] == 'open')
        with patch('smith_agents.app.raise_agent_window', return_value=True) as open_window:
            widget._on_peek_click(SimpleNamespace(x=(box[1]+box[3])/2, y=(box[2]+box[4])/2))
        open_window.assert_called_once_with(rows[1])


if __name__ == '__main__':
    unittest.main()

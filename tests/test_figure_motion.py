import unittest
from unittest.mock import patch

from PIL import ImageChops
from smith_agents import artwork, core, figure_actions as motion, figure_motion, scooter_motion


def difference(left, right):
    return sum(value * count for value, count in
               enumerate(ImageChops.difference(left, right).histogram()))


class FigureMotionTests(unittest.TestCase):
    def test_pc_changes_only_inside_the_screen(self):
        original = artwork.alpha_mask('approved_23')
        outside = ImageChops.invert(figure_motion.laptop_screen_mask(original.size))
        frames = set()
        for frame in range(motion.ACTION_FRAMES + motion.IDLE_FRAMES):
            padded = motion.alpha_frame('approved_23', frame)
            animated = padded.crop((motion.PAD, motion.PAD,
                                    motion.PAD + original.width, motion.PAD + original.height))
            change = ImageChops.difference(original, animated)
            self.assertIsNone(ImageChops.multiply(change, outside).getbbox())
            frames.add(animated.tobytes())
        self.assertGreater(len(frames), 10)

    def test_every_pose_has_a_stronger_entrance_then_continuous_quiet_idle(self):
        for name in motion.LABELS:
            with self.subTest(name=name):
                source = artwork.alpha_mask(name).tobytes()
                frames = [artwork.render(name, 'black', 114, 88, frame=f).getchannel('A')
                          for f in range(motion.ACTION_FRAMES + motion.IDLE_FRAMES)]
                resting = frames[motion.ACTION_FRAMES]
                entrance = max(difference(resting, frame) for frame in frames[:motion.ACTION_FRAMES])
                idle = max(difference(resting, frame) for frame in frames[motion.ACTION_FRAMES:])
                self.assertGreater(entrance, 2 * idle)
                self.assertGreater(len({f.tobytes() for f in frames[motion.ACTION_FRAMES:]}), 5)
                # The handoff and idle wrap are small relative to the action,
                # so neither can look like the entrance restarting.
                self.assertLess(difference(resting, frames[motion.ACTION_FRAMES-1]), entrance * .08)
                self.assertLess(difference(resting, frames[-1]), entrance * .08)
                self.assertEqual(artwork.alpha_mask(name).tobytes(), source)

    def test_entrance_never_repeats_after_long_elapsed_time(self):
        self.assertEqual(motion.frame_at(0), 0)
        self.assertEqual(motion.frame_at(3.2), motion.ACTION_FRAMES)
        for elapsed in (6.4, 60, 3600, 86400, 10**9):
            frame = motion.frame_at(elapsed)
            self.assertGreaterEqual(frame, motion.ACTION_FRAMES)
            self.assertLess(frame, motion.ACTION_FRAMES + motion.IDLE_FRAMES)
        self.assertEqual(motion.canonical_frame(128), 64)
        self.assertEqual(motion.canonical_frame(-20), 0)
        self.assertEqual(motion.frame_at(4), motion.frame_at(7.2))

    def test_session_timing_survives_rescans_and_resets_on_state_change_or_replay(self):
        timeline = motion.Timeline()
        agent = {'id': 'session-a', 'state': 'working'}
        pose = (core.agent_style(agent)[0], agent['state'])
        self.assertEqual(timeline.elapsed(agent['id'], pose, 100), 0)
        timeline.elapsed('session-b', 'another-pose', 101)
        timeline.sync({'session-a': pose, 'session-b': 'another-pose'})
        self.assertEqual(timeline.elapsed(dict(agent)['id'], pose, 102), 2)
        timeline.sync({'session-a': ('approved_30', 'done')})
        self.assertEqual(timeline.elapsed(agent['id'], ('approved_30', 'done'), 103), 0)
        timeline.replay(agent['id'])
        self.assertEqual(timeline.elapsed(agent['id'], ('approved_30', 'done'), 110), 0)
        timeline.replay()
        self.assertFalse(timeline.starts)
        for i in range(1000):
            timeline.elapsed(str(i), 'pose', i)
        self.assertLessEqual(len(timeline.starts), 512)

    def test_offscreen_and_folded_figures_do_not_consume_entrances(self):
        rows = core.demo_agents(all_figures=True)
        self.assertEqual(len(rows), 13)
        self.assertEqual(len({core.agent_style(row)[0] for row in rows}), 13)
        timeline = motion.Timeline()
        now = 100
        def elapsed(row):
            return timeline.elapsed(row['id'], core.agent_style(row)[0], now)
        height = core.BAR_H + core.TAB_H + core.FOOT_H + core.px(190)
        for options in ({'expanded': False}, {'tab': 'usage'}, {'tab': 'stats'}):
            core.render_console([], None, {}, rows, 0, max_height=height,
                                figure_elapsed=elapsed, **options)
        self.assertFalse(timeline.starts)
        core.render_console([], None, {}, rows, 0, max_height=height, figure_elapsed=elapsed)
        initial = set(timeline.starts)
        self.assertGreater(len(initial), 0)
        self.assertLess(len(initial), len(rows))
        now = 110
        core.render_console([], None, {}, rows, 0, max_height=height, scroll=100000,
                            figure_elapsed=elapsed)
        newly_visible = set(timeline.starts) - initial
        self.assertTrue(newly_visible)
        self.assertTrue(all(timeline.starts[key][1] == now for key in newly_visible))
        core.render_console([], None, {}, rows, 0, max_height=height, figure_elapsed=elapsed)
        self.assertTrue(all(timeline.starts[key][1] == 100 for key in initial))
        # A row's text can be visible after its figure has scrolled away.
        timeline.replay()
        core.render_console([], None, {}, rows, 0, max_height=height,
                            scroll=core.px(8) + core.ROW_FIGURE_H,
                            figure_elapsed=elapsed)
        self.assertNotIn(core.sort_agents(rows)[0]['id'], timeline.starts)

    def test_visible_sizes_keep_the_baseline_and_approved_scooter_motion(self):
        for name in motion.LABELS:
            for density in (1, 2):
                with self.subTest(name=name, density=density):
                    base = artwork.render(name, 'black', 57*density, 44*density)
                    bottom = base.getchannel('A').getbbox()[3]
                    for frame in (8, 16, 24, 40, 63, 64, 80, 96, 127):
                        image = artwork.render(name, 'black', 57*density, 44*density, frame=frame)
                        self.assertEqual(image.size, base.size)
                        self.assertEqual(image.getchannel('A').getbbox()[3], bottom)
        for frame in range(128):
            self.assertEqual(motion.alpha_frame('approved_35', frame).tobytes(),
                             scooter_motion.alpha_frame(frame).tobytes())

    def test_helper_taps_only_during_entrance_and_keeps_other_contours_fixed(self):
        from smith_agents import little_helper_motion as helper
        reference = helper.alpha_frame(0)
        # Only the raised arm and the back revealed behind it may change.
        left, top = helper._point(710, 190)
        right, bottom = helper._point(1070, 630)
        for frame in range(motion.ACTION_FRAMES):
            box = ImageChops.difference(reference, helper.alpha_frame(frame)).getbbox()
            if box:
                self.assertGreaterEqual(box[0], int(left)-2)
                self.assertGreaterEqual(box[1], int(top)-2)
                self.assertLessEqual(box[2], int(right)+2)
                self.assertLessEqual(box[3], int(bottom)+2)
        # Contact follows a lift twice, followed by the approved resting pose.
        for lift, touch in ((.85, 1.10), (1.35, 1.60)):
            self.assertLess(helper.pose(lift)[1], helper.pose(touch)[1])
        self.assertEqual(helper.alpha_frame(56).tobytes(), reference.tobytes())
        self.assertEqual(helper.alpha_frame(63).tobytes(), reference.tobytes())
        late = motion.frame_at(3600)
        self.assertGreaterEqual(late, motion.ACTION_FRAMES)
        self.assertLessEqual(helper.alpha_frame.cache_info().currsize, 128)

    def test_caches_stay_bounded_and_long_times_use_idle_frames(self):
        with patch.object(core, '_FIGURE_CACHE', {}):
            for frame in range(2500):
                core.agent_figure('approved_23', frame, 'black', 57, 44)
            self.assertLessEqual(len(core._FIGURE_CACHE), 128)
            late = core.agent_figure('approved_23', 2500, 'black', 57, 44)
            wrapped = core.agent_figure('approved_23', motion.canonical_frame(2500), 'black', 57, 44)
            self.assertEqual(late.tobytes(), wrapped.tobytes())
        self.assertLessEqual(len(core._SHADOW_CACHE), 4)


if __name__ == '__main__':
    unittest.main()

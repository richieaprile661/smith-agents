import hashlib
import unittest
from unittest.mock import patch

from smith_agents import artwork, core


class ApprovedArtworkTests(unittest.TestCase):
    def test_approved_assignments_are_stable_and_all_variants_are_used(self):
        expected = {
            "working": [17, 23, 35], "needs": [3, 7, 8, 12, 34],
            "done": [30, 14, 20, 28, 29], "closed": [14, 20, 28, 29],
        }
        for state, numbers in expected.items():
            seen = set()
            for n in range(100):
                agent = {"id": "session-%d" % n, "state": state}
                name, frame = core.agent_phase(agent, 1)
                self.assertEqual(core.agent_phase(agent, 1000)[0], name)
                seen.add(name)
            self.assertEqual(seen, {"approved_%d" % n for n in numbers})
        self.assertFalse(artwork.contains("approved_24"))
        self.assertFalse(artwork.contains("approved_33"))
        self.assertIsNotNone(core.row_figure({"id": "unknown", "state": "unknown"}, 0))

    def test_ready_figures_are_spread_before_repeating(self):
        for count in (1, 3, 5, 13):
            with self.subTest(count=count):
                rows = [{'id': 'ready-%d' % i, 'state': 'done'} for i in range(count)]
                selector = artwork.FigureAssignments()
                selector.assign(rows)
                poses = [core.agent_style(row)[0] for row in rows]
                variants = artwork.STATE_FIGURES['done']
                self.assertEqual(len(set(poses)), min(count, len(variants)))
                frequencies = [poses.count(pose) for pose in variants]
                self.assertLessEqual(max(frequencies) - min(frequencies), 1)

    def test_assignments_survive_rescans_reordering_and_departures(self):
        selector = artwork.FigureAssignments()
        rows = [{'id': 'ready-%d' % i, 'state': 'done'} for i in range(5)]
        selector.assign(rows)
        before = dict(selector.choices)
        rescanned = [{'id': row['id'], 'state': 'done', 'idle': 999} for row in reversed(rows)]
        selector.assign(rescanned)
        self.assertEqual(selector.choices, before)
        self.assertEqual({row['id']: core.agent_style(row)[0] for row in rescanned},
                         {identity: choice[1] for identity, choice in before.items()})
        other = artwork.FigureAssignments()
        other.assign(list(reversed(rows)))
        self.assertEqual(other.choices, before)
        remaining = rescanned[1:]
        remaining.append({'id': 'new-ready', 'state': 'done'})
        selector.assign(remaining)
        departed = rescanned[0]['id']
        self.assertNotIn(departed, selector.choices)
        for row in remaining[:-1]:
            self.assertEqual(selector.choices[row['id']], before[row['id']])
        self.assertEqual(len({core.agent_style(row)[0] for row in remaining}), 5)
        selector.assign([])
        self.assertFalse(selector.choices)

    def test_entering_ready_uses_its_unused_pose_even_if_shared_with_closed(self):
        selector = artwork.FigureAssignments()
        pool = artwork.STATE_FIGURES['done']
        selector.choices = {'ready-%d' % i: ('done', pose) for i, pose in enumerate(pool[:-1])}
        selector.choices['returning'] = ('closed', pool[-1])
        rows = [{'id': identity, 'state': 'done'} for identity in selector.choices]
        selector.assign(rows)
        self.assertEqual(len({core.agent_style(row)[0] for row in rows}), len(pool))

    def test_state_change_uses_the_new_pool_and_invalidates_stale_metadata(self):
        selector = artwork.FigureAssignments()
        row = {'id': 'session-a', 'state': 'done'}
        selector.assign([row])
        row['state'] = 'working'
        self.assertIn(core.agent_style(row)[0], artwork.STATE_FIGURES['working'])
        selector.assign([row])
        working_pose = core.agent_style(row)[0]
        self.assertIn(working_pose, artwork.STATE_FIGURES['working'])
        row['state'] = 'done'
        selector.assign([row])
        self.assertIn(core.agent_style(row)[0], artwork.STATE_FIGURES['done'])
        self.assertNotEqual(core.agent_style(row)[0], working_pose)
        selector.assign([row])
        self.assertEqual(core.agent_style(row)[0], selector.choices[row['id']][1])

    def test_subagents_keep_helper_pose_without_using_parent_variants(self):
        selector = artwork.FigureAssignments()
        parents = [{'id': 'parent-'+str(i), 'state': 'working'} for i in range(3)]
        children = [{'id': 'agent-'+str(i), 'state': 'working', 'sub': True, 'parent': parents[0]['id']}
                    for i in range(4)]
        selector.assign(parents + children)
        self.assertEqual({core.agent_style(row)[0] for row in parents}, set(artwork.STATE_FIGURES['working']))
        for state in ('working', 'needs', 'done', 'closed'):
            for row in children:
                row['state'] = state
            selector.assign(parents + children)
            for row in children:
                expected = {'working': 'helper_working', 'needs': 'helper_needs',
                            'done': 'helper_finished', 'closed': 'helper_unknown'}[state]
                self.assertEqual(core.agent_style(row)[0], expected)
                self.assertEqual(row['_figure_pose'], expected)
                self.assertIsNotNone(core.row_figure(row, 0, 78, 52))
        self.assertEqual(core.agent_style({'id': 'new-child', 'sub': True, 'state': 'working'})[0], 'helper_working')

    def test_helper_poses_follow_current_evidence_and_restart_on_tool_change(self):
        from smith_agents.activity import Activity
        from smith_agents.figure_actions import Timeline
        row = dict(id='helper', sub=True, state='working', last_request='Run pytest then review code')
        self.assertEqual(artwork.helper_pose(row), 'helper_working')
        a = Activity(); a.start('read', 'Read', {'path': 'app.py'}, 10)
        row['activity'] = a.data
        self.assertEqual(artwork.helper_pose(row), 'helper_reviewing')
        timeline = Timeline()
        timeline.elapsed(row['id'], core.agent_style(row)[0], 10)
        a.finish('read', 'contents', 11)
        a.start('tests', 'exec_command', {'cmd': 'python -m unittest discover'}, 12)
        self.assertEqual(artwork.helper_pose(row), 'helper_testing')
        self.assertEqual(timeline.elapsed(row['id'], core.agent_style(row)[0], 12), 0)
        a.start('other', 'Edit', {'path': 'app.py'}, 13)
        self.assertEqual(artwork.helper_pose(row), 'helper_working')
        row['permissions'] = [{'request': {}}]
        self.assertEqual(artwork.helper_pose(row), 'helper_needs')
        row['permissions'] = []
        for status in ('failed', 'stopped', 'unknown'):
            a.transition(status, 14)
            self.assertEqual(artwork.helper_pose(row), 'helper_unknown')
        a.transition('completed', 15)
        a.data['disconnected'] = True
        self.assertEqual(artwork.helper_pose(row), 'helper_finished')
        a = Activity(); row['activity'] = a.data
        a.start('edit', 'Edit', {'path': 'cat.py'}, 16)
        self.assertEqual(artwork.helper_pose(row), 'helper_working')
        a.finish('edit', 'ok', 17)
        a.start('echo', 'Bash', {'command': 'echo pytest'}, 18)
        self.assertEqual(artwork.helper_pose(row), 'helper_working')

    def test_packaged_sources_match_the_reviewed_artwork(self):
        for name, digest in artwork.MANIFEST["sources"].items():
            with self.subTest(source=name):
                self.assertEqual(hashlib.sha256((artwork.ROOT / name).read_bytes()).hexdigest(), digest)

    def test_all_figures_fit_at_windows_and_retina_sizes_with_transparent_paper(self):
        names = [name for variants in artwork.STATE_FIGURES.values() for name in variants]
        names.extend((artwork.HEADER, artwork.SUBAGENT))
        names.extend(artwork.HELPER_FIGURES)
        for name in names:
            logical_bounds = []
            for density in (1, 2):
                with self.subTest(name=name, density=density):
                    image = artwork.render(name, (217, 119, 87), 44 * density, 34 * density)
                    self.assertEqual(image.size, (44 * density, 34 * density))
                    alpha = image.getchannel("A")
                    self.assertEqual(alpha.getpixel((0, 0)), 0)
                    self.assertGreater(alpha.getextrema()[1], 32)
                    bounds = alpha.getbbox()
                    self.assertIsNotNone(bounds)
                    logical_bounds.append(((bounds[2] - bounds[0]) / density,
                                           (bounds[3] - bounds[1]) / density))
            for one, two in zip(*logical_bounds):
                # Coverage may round out by a pixel at each edge on a 1x
                # screen. The separate sizing check compares poses at the
                # same density and enforces a tighter one-pixel difference.
                self.assertLessEqual(abs(one - two), 2)

    def test_all_animation_frames_fit_fixed_envelopes_without_clipping(self):
        from smith_agents import figure_actions as motion
        geometry = artwork.MANIFEST['session_drawing_geometry']
        checked = 0
        for name in sorted(artwork.SESSION_FIGURES):
            for frame in range(motion.ACTION_FRAMES + motion.IDLE_FRAMES):
                source = motion.alpha_frame(name, frame)
                box = source.point(lambda v: v if v > geometry['alpha_cutoff'] else 0).getbbox()
                envelope = geometry['animation_bounds'][name]
                self.assertTrue(envelope[0] <= box[0] < box[2] <= envelope[2])
                self.assertTrue(envelope[1] <= box[1] < box[3] <= envelope[3])
                for density in (1, 1.25, 1.5, 2):
                    heights = []
                    for width, height in ((56, 42), (80, 40)):
                        w, h = round(width*density), round(height*density)
                        image = artwork.render(name, 'white', w, h, frame=frame)
                        box = image.getchannel('A').getbbox()
                        ink_height = box[3]-box[1]
                        with self.subTest(name=name, frame=frame, density=density, cell=(w,h)):
                            self.assertAlmostEqual(artwork.session_body_height(w, h), 23*density)
                            self.assertGreater(box[0], 0)
                            self.assertGreater(box[1], 0)
                            self.assertLess(box[2], w)
                            self.assertLess(box[3], h)
                        heights.append(ink_height)
                        checked += 1
                    self.assertEqual(*heights)
        self.assertEqual(checked, 19 * 128 * 4 * 2)

    def test_moving_prop_does_not_resize_or_shift_stationary_body(self):
        from PIL import Image, ImageDraw, ImageChops
        from smith_agents import figure_actions as motion
        frames = []
        for top in (5, 30):
            mask = Image.new('L', (100, 80))
            pen = ImageDraw.Draw(mask)
            pen.rectangle((40, 25, 60, 69), fill=255)
            pen.line((5, 69, 79, 69), fill=255, width=2)
            pen.rectangle((10, top, 20, top+9), fill=255)
            frames.append(mask)
        geometry = artwork.MANIFEST['session_drawing_geometry']['animation_bounds']
        baselines = artwork.MANIFEST['session_drawing_geometry']['baselines']
        with patch.dict(geometry, approved_3=[5, 5, 80, 70]), \
             patch.dict(baselines, approved_3=70), \
             patch.object(motion, 'alpha_frame', side_effect=frames):
            first = artwork.render('approved_3', 'white', 112, 84, frame=0)
            second = artwork.render('approved_3', 'white', 112, 84, frame=1)
        self.assertIsNotNone(ImageChops.difference(first, second).getbbox())
        self.assertIsNone(ImageChops.difference(first.crop((56, 0, 112, 84)),
                                              second.crop((56, 0, 112, 84))).getbbox())

    def test_current_figures_have_firm_strokes_at_each_display_scale(self):
        for density in (1, 1.25, 1.5, 2):
            for name in artwork.SESSION_FIGURES:
                for width, height in ((56, 42), (80, 40)):
                    with self.subTest(name=name, density=density, cell=(width, height)):
                        alpha = artwork.render(name, 'white', round(width*density),
                                               round(height*density)).getchannel('A')
                        histogram = alpha.histogram()
                        self.assertGreater(sum(histogram[220:]) / sum(histogram[32:]), .05)
                        self.assertGreater(sum(histogram[1:220]), 0)  # Smooth edges remain.

    def test_current_session_figures_do_not_acquire_theme_blur(self):
        name = 'approved_23'
        core._FIGURE_CACHE.clear()
        try:
            with patch.dict(core.T, glow=True):
                actual = core.agent_figure(name, 0, (255, 255, 255, 255), 56, 42)
            expected = artwork.render(name, (255, 255, 255, 255), 56, 42)
            self.assertEqual(actual.tobytes(), expected.tobytes())
        finally:
            core._FIGURE_CACHE.clear()

    def test_tray_has_transparent_padding_smooth_edges_and_alarm_tint(self):
        for size in (16, 20, 22, 24, 32, 44):
            with self.subTest(size=size), patch.object(core, "tray_size", return_value=size):
                normal = core.render_tray(None, False, None)
                alert = core.render_tray(None, False, "error")
                self.assertEqual(normal.size, (size, size))
                alpha = normal.getchannel("A")
                self.assertEqual(alpha.tobytes(), alert.getchannel("A").tobytes())
                left, top, right, bottom = alpha.getbbox()
                self.assertGreaterEqual(left, 1)
                self.assertGreaterEqual(top, 1)
                self.assertLess(right, size)
                self.assertLess(bottom, size)
                self.assertTrue(any(alpha.histogram()[1:255]))
                self.assertNotEqual(normal.tobytes(), alert.tobytes())

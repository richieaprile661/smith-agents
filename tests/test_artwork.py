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
                self.assertEqual(core.agent_style(row)[0], artwork.SUBAGENT)
                self.assertEqual(row['_figure_pose'], artwork.SUBAGENT)
                self.assertIsNotNone(core.row_figure(row, 0, 78, 52))
        self.assertEqual(core.agent_style({'id': 'new-child', 'sub': True, 'state': 'working'})[0], artwork.SUBAGENT)

    def test_packaged_sources_match_the_reviewed_artwork(self):
        for name, digest in artwork.MANIFEST["sources"].items():
            with self.subTest(source=name):
                self.assertEqual(hashlib.sha256((artwork.ROOT / name).read_bytes()).hexdigest(), digest)

    def test_all_figures_fit_at_windows_and_retina_sizes_with_transparent_paper(self):
        names = [name for variants in artwork.STATE_FIGURES.values() for name in variants]
        names.extend((artwork.HEADER, artwork.SUBAGENT))
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

    def test_character_body_scale_excludes_raised_hands_and_props(self):
        from smith_agents import figure_actions as motion
        names = {name for variants in artwork.STATE_FIGURES.values() for name in variants}
        names.add(artwork.SUBAGENT)
        for width, height in ((80, 40), (57, 44)):
            for density in (1, 2):
                w, h = width*density, height*density
                bounds = [artwork.render(name, "black", w, h).getchannel("A").getbbox()
                          for name in names]
                self.assertEqual(len({box[3] for box in bounds}), 1)
                body_heights = []
                # Vertical slices through each person's crown and torso. These
                # reviewed source coordinates exclude the cloth's raised hand
                # and the watering can/plant. Whole-image fitting fails here.
                for name, left, right in (("approved_17", 110, 159),
                                           ("approved_28", 40, 110)):
                    reference = motion.alpha_frame(name, 0)
                    size, _y = artwork._session_placement(name, w, h)
                    scale_x = size[0] / reference.width
                    x = (w-size[0])//2
                    rect = (round(x+(left+motion.PAD)*scale_x), 0,
                            round(x+(right+motion.PAD)*scale_x), h)
                    body = artwork.render(name, "black", w, h).getchannel("A").crop(rect)
                    box = body.point(lambda v: 255 if v > 32 else 0).getbbox()
                    body_heights.append(box[3]-box[1])
                self.assertLessEqual(abs(body_heights[0]-body_heights[1]), density)

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

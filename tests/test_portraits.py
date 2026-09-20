import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageChops

from smith_agents import portraits

ROOT = Path(__file__).resolve().parents[1]


class PortraitTests(unittest.TestCase):
    def test_packaged_portraits_match_approved_hashes(self):
        manifest = portraits.manifest()
        self.assertEqual(set(manifest), set(portraits.NAMES))
        for name, entry in manifest.items():
            data = (portraits.ROOT / entry["image"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"], name)

    def test_state_colour_and_rate(self):
        self.assertEqual({state: (portraits.state_colour(state), portraits.rate(state))
                          for state in ("done", "needs", "working", "closed")},
                         {"done": ("green", 0.1), "needs": ("yellow", 1.0),
                          "working": ("red", 2.5), "closed": ("white", 0.0)})
        self.assertEqual(portraits.colour("gray"), "white")
        self.assertEqual(portraits.colour("grey"), "white")
        self.assertEqual(portraits.rate("working", reduced=True), 0.0)

    def test_main_sessions_draw_expressions_and_subagents_never_do(self):
        faces = portraits.Assignments()
        mains = [{"id": "m%d" % i} for i in range(40)]
        faces.assign(mains + [{"id": "h%d" % i, "sub": True} for i in range(5)])
        drawn = {agent["_portrait"] for agent in mains}
        self.assertTrue(drawn <= set(portraits.EXPRESSIONS))
        # Forty sessions spread across the expressions rather than sharing one.
        self.assertGreater(len(drawn), 1)
        for agent in [{"id": "s", "sub": True}, {"id": "t", "sub": True, "state": "done"}]:
            self.assertIn(portraits.default_face(agent), portraits.HELPERS)
        self.assertIn(portraits.default_face({"id": "main"}), portraits.EXPRESSIONS)

    def test_faces_are_stable_across_state_rescan_and_order(self):
        main = {"id": "main", "state": "working"}
        helpers = [{"id": "h%d" % i, "sub": True, "state": "working"} for i in range(7)]
        faces = portraits.Assignments()
        faces.assign([main] + helpers)
        first = {agent["id"]: agent["_portrait"] for agent in [main] + helpers}
        self.assertIn(first["main"], portraits.EXPRESSIONS)
        # Five helpers get five distinct faces; extras reuse the least used.
        self.assertEqual(set(first[h["id"]] for h in helpers[:7]), set(portraits.HELPERS))
        rows = [dict(agent, state="done") for agent in reversed([main] + helpers)]
        faces.assign(rows)
        self.assertEqual({agent["id"]: agent["_portrait"] for agent in rows}, first)
        # Input order does not change who gets which face in a fresh batch.
        again = portraits.Assignments()
        shuffled = [dict(agent) for agent in reversed([main] + helpers)]
        again.assign(shuffled)
        self.assertEqual({agent["id"]: agent["_portrait"] for agent in shuffled}, first)

    def test_clock_caps_gaps_freezes_and_thaws_without_reset(self):
        clock = portraits.Clock()
        self.assertEqual(clock.advance("a", 2.5, 10.0), 0.0)
        self.assertAlmostEqual(clock.advance("a", 2.5, 10.05), 0.125)
        # A long gap (hidden, scrolled away) counts for at most 0.1 seconds.
        self.assertAlmostEqual(clock.advance("a", 2.5, 99.0), 0.375)
        frozen = clock.advance("a", 0.0, 99.5)
        self.assertEqual(clock.advance("a", 0.0, 100.0), frozen)
        self.assertAlmostEqual(clock.advance("a", 1.0, 100.05), frozen + 0.05)
        clock.sync({"b"})
        self.assertEqual(clock.advance("a", 1.0, 101.0), 0.0)

    def test_frozen_frames_do_not_change_and_moving_frames_do(self):
        still = portraits.frame("jones", 42, 84, "white", 3.0)
        self.assertIs(portraits.frame("jones", 42, 84, "white", 3.0), still)
        moved = portraits.frame("jones", 42, 84, "red", 3.4)
        self.assertIsNotNone(ImageChops.difference(
            portraits.frame("jones", 42, 84, "red", 3.0), moved).getbbox())

    def test_tint_starts_from_the_untinted_base_every_time(self):
        green = portraits.base("brown", 42, 84, "green")
        for tint in ("red", "yellow", "white", "red", "yellow"):
            portraits.base.cache_clear()
            portraits.base("brown", 42, 84, tint)
        portraits.base.cache_clear()
        self.assertIsNone(ImageChops.difference(green, portraits.base("brown", 42, 84, "green")).getbbox())
        red = portraits.base("brown", 42, 84, "red")
        white = portraits.base("brown", 42, 84, "white")
        peak = ImageChops.lighter(*white.split()[:2])
        self.assertEqual(red.getchannel("R").tobytes(), peak.tobytes())

    def test_columns_follow_the_reference_seed(self):
        self.assertEqual(len(portraits.columns(40)), 14)
        self.assertEqual(len(portraits.columns(42)), 14)
        self.assertNotEqual(portraits.columns(40), portraits.columns(42))

    def test_cell_is_square_centred_and_screen_blended(self):
        agent = {"id": "x", "state": "needs", "sub": True, "_portrait": "jackson"}
        cell = portraits.cell(agent, 1.0, 112, 84, 2.0)
        self.assertEqual(cell.size, (112, 84))
        self.assertEqual(cell.getchannel("A").getbbox(), (14, 0, 98, 84))
        dest = Image.new("RGBA", (120, 90), (40, 60, 50, 255))
        portraits.composite(dest, cell, (2, 3))
        # Screen never darkens; black portrait pixels leave the widget untouched.
        self.assertEqual(dest.getpixel((0, 0)), (40, 60, 50, 255))
        self.assertTrue(all(a >= b for a, b in zip(dest.getpixel((60, 40)), (40, 60, 50, 255))))


class MatrixThemeTests(unittest.TestCase):
    def run_widget(self, code, config=None):
        with tempfile.TemporaryDirectory() as directory:
            if config is not None:
                Path(directory, "config.json").write_text(json.dumps(config))
            env = dict(os.environ, SMITH_AGENTS_CONFIG_DIR=directory)
            result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

    def test_matrix_is_the_default_and_listed_first(self):
        out = self.run_widget("from smith_agents import core as c; print(c.THEME_NAME, list(c.THEMES))")
        self.assertEqual(out, "matrix ['matrix', 'claude', 'eink']")

    def test_only_matrix_uses_portraits(self):
        code = ("from smith_agents import core as c\n"
                "a=dict(c.demo_agents()[0], state='working')\n"
                "f=c.row_figure(a, 1.0, c.ROW_FIGURE_W, c.ROW_FIGURE_H)\n"
                "print(c.PORTRAITS, f.info.get('blend'))")
        self.assertEqual(self.run_widget(code, {"theme": "matrix"}), "True screen")
        for theme in ("claude", "eink"):
            self.assertEqual(self.run_widget(code, {"theme": theme}), "False None", theme)

    def test_matrix_tray_uses_the_smith_face(self):
        code = ("from smith_agents import core as c, artwork\n"
                "a=c.render_tray().getchannel('A')\n"
                "b=artwork.tray_icon(a.width, 'white', artwork.MATRIX_TRAY).getchannel('A')\n"
                "print(a.tobytes()==b.tobytes())")
        self.assertEqual(self.run_widget(code, {"theme": "matrix"}), "True")
        for theme in ("claude", "eink"):
            self.assertEqual(self.run_widget(code, {"theme": theme}), "False", theme)


if __name__ == "__main__":
    unittest.main()

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

# Never load a developer's saved settings during tests.
_config = tempfile.TemporaryDirectory(prefix="smith-agents-tests-")
os.environ["SMITH_AGENTS_CONFIG_DIR"] = _config.name

from smith_agents import core
from smith_agents.app import SmithAgentsWidget
from smith_agents.sample_data import demo_payload, demo_stats


class CoreTests(unittest.TestCase):
    def test_figures_keep_their_logical_size_at_retina_density_and_zoom(self):
        for strip in core.FIG:
            for width, height in ((88, 52), (78, 52), (40, 24)):
                with patch.multiple(core, SCALE=1.0, ZOOM=1.0):
                    baseline = core.figure_scale(strip, width, height)[0]
                for density, zoom in ((2.0, 1.0), (1.0, 1.5), (2.0, 1.5)):
                    with self.subTest(strip=strip, cell=(width, height), density=density, zoom=zoom):
                        with patch.multiple(core, SCALE=density, ZOOM=zoom):
                            scale = core.figure_scale(strip, core.px(width), core.px(height))[0]
                        self.assertAlmostEqual(scale / (density * zoom), baseline)

    def test_figure_edges_keep_source_alpha_without_double_masking(self):
        # Half-covered source pixels must stay half-covered, including at 1x.
        source = Image.new("RGBA", (88 * core.AGENT_FRAMES, 36), (255, 255, 255, 128))
        with patch.multiple(core, SCALE=1.0, ZOOM=1.0, T={}, _FIGURE_CACHE={}):
            with patch.object(core.Image, "open", return_value=source):
                for width, height in ((88, 52), (44, 34)):
                    for fade, expected in ((False, 128), (True, 99)):
                        rendered = core.agent_figure("pc_complete", 0, (217, 119, 87),
                                                     width, height, fade=fade)
                        self.assertEqual(rendered.getpixel((width // 2, height - 5))[3], expected)

    def test_compact_poses_share_the_typists_scale(self):
        with patch.multiple(core, SCALE=1.0, ZOOM=1.0):
            reference = core.figure_scale("pc_complete", 44, 34)[0]
            for strip in ("cooking_complete", "sleeping_complete", "selfie"):
                with self.subTest(strip=strip):
                    self.assertEqual(core.figure_scale(strip, 44, 34)[0], reference)

    def test_both_usage_schemas_and_severity_priority(self):
        metrics = core.build_metrics(demo_payload())
        self.assertEqual([m["key"] for m in metrics], ["session", "weekly", "scoped"])
        self.assertEqual(core.choose_front(metrics)[0]["key"], "weekly")
        metrics[0]["severity"] = "critical"
        self.assertEqual(core.choose_front(metrics)[0]["key"], "session")
        legacy = core.build_metrics({"five_hour": {"utilization": 25}, "seven_day": {"utilization": 80}})
        self.assertEqual([m["pct"] for m in legacy], [25, 80])

    def test_renderer_and_hit_targets_for_each_view(self):
        metrics = core.build_metrics(demo_payload())
        for tab in ("agents", "usage", "stats"):
            image, boxes = core.render_console(metrics, None, demo_stats(), core.demo_agents(), time.time(), tab=tab)
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.width, core.CONSOLE_W + core.SHADOW_PAD * 2)
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertTrue(any(row[0] == "tab:usage" for row in boxes))
            for kind, x0, y0, x1, y1, agent in boxes:
                self.assertGreaterEqual(x1, x0, kind)
                self.assertGreaterEqual(y1, y0, kind)
                self.assertLessEqual(x1, image.width, kind)
                self.assertLessEqual(y1, image.height, kind)

    def test_no_data_still_has_tabs_and_agent_rows(self):
        image, boxes = core.render_console([], None, {}, core.demo_agents(), time.time(), notice=("auth", "Sign in to Claude"))
        self.assertTrue(any(row[0] == "tab:agents" for row in boxes))
        self.assertTrue(any(row[-1] is not None for row in boxes))

    def test_header_layout_and_controls_match_across_platforms(self):
        metrics = core.build_metrics(demo_payload())
        for readings in (metrics, []):
            for expanded in (False, True):
                results = []
                for platform_name in ("darwin", "win32"):
                    with patch("sys.platform", platform_name):
                        image, boxes = core.render_console(readings, None, {}, [], 0, expanded=expanded)
                        results.append((image.tobytes(), boxes))
                self.assertEqual(results[0], results[1])
                kinds = [box[0] for box in results[0][1]]
                self.assertIn("tuck", kinds)
                self.assertIn("bar", kinds)
                self.assertEqual("reading" in kinds, bool(readings))

    def test_agent_context_state_and_activity_never_overlap(self):
        from PIL import ImageDraw, ImageFont
        real_font = core.FONT
        for extra in (0, core.px(3)):
            def font(weight, size):
                original = real_font(weight, size)
                return ImageFont.truetype(original.path, original.size + extra)
            for verb in ("Bash", "mcp__chrome_devtools__evaluate_script"):
                row = core.demo_agents()[1]
                row.update(name="accounting-long-project-name-that-wraps", idle=3661,
                           context_tokens=486814, context_capacity=1000000,
                           tail=[("cmd", verb + " very long command argument " * 10)])
                with patch.object(core, "FONT", side_effect=font):
                    height = core.agent_row_height(row)
                    chip = core.console_base((core.CONSOLE_W, height))
                    draw = ImageDraw.Draw(chip)
                    texts, tracks = [], []

                    class RecordingPen:
                        def __getattr__(self, name):
                            return getattr(draw, name)

                        def text(self, xy, text, **kwargs):
                            texts.append((text, draw.textbbox(xy, text, font=kwargs["font"])))
                            return draw.text(xy, text, **kwargs)

                        def rounded_rectangle(self, xy, **kwargs):
                            tracks.append(xy)
                            return draw.rounded_rectangle(xy, **kwargs)

                    core.render_row(RecordingPen(), chip, row, 0, 0, False, False)
                timer = next(bounds for text, bounds in texts if text == core.agent_activity_stamp(row))
                self.assertGreater(timer[1], tracks[0][3])
                activity = texts[-1][1]
                self.assertGreater(activity[1], timer[3])
                for text, bounds in texts:
                    self.assertLessEqual(bounds[2], core.CONSOLE_W - core.PAD_X + 1, text)
                    self.assertLess(bounds[3], height, text)
                    if bounds[1] < timer[3] and bounds[3] > timer[1] and text != core.agent_activity_stamp(row):
                        self.assertLessEqual(bounds[2], timer[0] - core.px(4), text)

    def test_collapsed_row_open_shortcut_does_not_expand_the_drawer(self):
        from types import SimpleNamespace
        for entrypoint in ('claude-cli', 'claude-vscode'):
            with self.subTest(entrypoint=entrypoint):
                row = dict(core.demo_agents()[1], entrypoint=entrypoint)
                _, boxes = core.render_console([], None, {}, [row], 0)
                shortcut = next(box for box in boxes if box[0] == 'open')
                row_box = next(box for box in boxes if box[0] == 'row')
                self.assertLess(boxes.index(shortcut), boxes.index(row_box))
                self.assertGreaterEqual(shortcut[2], row_box[2])
                self.assertLessEqual(shortcut[4], row_box[4])
                widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
                widget.agent_rows = boxes
                widget.agents = [row]
                widget.agent_open = None
                # The text and its arrow both route to the existing open action.
                for x in ((shortcut[1] + shortcut[3]) / 2, shortcut[3] - 1):
                    event = SimpleNamespace(x=x, y=(shortcut[2] + shortcut[4]) / 2)
                    with patch('smith_agents.app.raise_agent_window', return_value=True) as open_session, \
                         patch.object(widget, '_repaint'), patch.object(widget, '_reveal_agent') as reveal:
                        self.assertTrue(widget._on_console_click(event))
                        open_session.assert_called_once_with(row)
                        reveal.assert_not_called()
                        self.assertIsNone(widget.agent_open)

    def test_scroll_keeps_header_footer_and_only_visible_actions(self):
        rows = core.demo_agents()
        row = rows[0]
        row["permissions"] = [{"actionable": True, "request": {"tool_name": "Bash", "input": {"command": "echo example"}}}]
        full, full_boxes = core.render_console([], None, {}, rows, 0, open_id=row["id"])
        height = core.px(420)
        top = core.BAR_H + core.TAB_H
        bottom = height - core.FOOT_H
        seen = set()
        for scroll in range(0, full.height, core.px(20)):
            image, boxes = core.render_console([], None, {}, rows, 0, open_id=row["id"], max_height=height, scroll=scroll)
            self.assertEqual(image.height, height + core.SHADOW_PAD * 2)
            for box in boxes:
                kind, x0, y0, x1, y1, agent = box
                self.assertLessEqual(y1, height)
                self.assertGreaterEqual(y0, 0)
                if agent is not None:
                    self.assertGreaterEqual(y0, top, kind)
                    self.assertLessEqual(y1, bottom, kind)
                    seen.add((kind, agent["id"]))
                if kind in ("bar", "tuck") or kind.startswith("tab:"):
                    self.assertIn(box, full_boxes)
        for kind in ("permission", "permission-deny", "open", "kill"):
            self.assertIn((kind, row["id"]), seen)
        self.assertIn(("row", rows[-1]["id"]), seen)

    def test_unknown_capacity_and_missing_details_render_without_inventing_values(self):
        row = {"id": "unknown", "state": "needs", "idle": None}
        self.assertEqual(core.agent_status(row), "Quiet · check chat")
        self.assertEqual(core.agent_activity_stamp(row), "Activity unknown")
        self.assertEqual(core.agent_model_source(row), "Model unknown · Claude Code")
        image, boxes = core.render_console([], None, {}, [row], 0, open_id=row["id"])
        self.assertNotIn("permission", [box[0] for box in boxes])
        self.assertEqual(image.height, core.BAR_H + core.TAB_H + core.agents_height([row], row["id"]) + 2 * core.SHADOW_PAD)

    def test_all_themes_render_in_fresh_processes(self):
        for theme in core.THEMES:
            with tempfile.TemporaryDirectory() as directory:
                import json
                (Path(directory) / "config.json").write_text(json.dumps({"theme": theme}))
                env = dict(os.environ, SMITH_AGENTS_CONFIG_DIR=directory)
                code = "from smith_agents import core as c; import time; i,b=c.render_console([],None,{},c.demo_agents(),time.time()); assert i.width>0; print(c.THEME_NAME)"
                result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), theme)

    def test_shell_code_is_not_in_shared_core(self):
        text = Path(core.__file__).read_text(encoding="utf-8")
        for platform_api in ("ctypes.windll", "import AppKit", "import tkinter", "import pystray"):
            self.assertNotIn(platform_api, text)
        for file in Path(core.__file__).parent.glob("*.py"):
            ast.parse(file.read_text(encoding="utf-8"), filename=str(file))

    def test_multiline_row_previews_render_on_both_platforms(self):
        row = core.demo_agents()[1]
        row["tail"] = [("cmd", "Bash echo first\necho second\r\n\techo third")]
        for open_id in (None, row["id"]):
            image, boxes = core.render_console([], None, {}, [row], time.time(), open_id=open_id)
            self.assertGreater(image.height, 0)
            self.assertTrue(any(box[0] == "row" for box in boxes))
        self.assertNotIn("\n", core.elide(row["tail"][0][1], core.MONO("book", 9), 10000))

    def test_windows_queries_permission_relay(self):
        from smith_agents.permissions import pending_permissions
        with patch("sys.platform", "win32"), patch("smith_agents.permissions.permission_call", return_value={"ok": True, "pending": []}) as call:
            self.assertEqual(pending_permissions("unused", {"id": "session", "state": "needs"}), [])
            call.assert_called_once_with("unused", "session", {"action": "list", "task_id": None})


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        self.widget.config = dict(core.DEFAULTS)
        self.widget.root = object()
        self.widget._bar_size = (core.CONSOLE_W + 2 * core.SHADOW_PAD, 500)

    def test_docking_respects_secondary_screen_origin_and_usable_frame(self):
        bounds = (-3000, 70, 2800, 1700)
        with patch("smith_agents.app.platform.screen_bounds", return_value=bounds):
            self.widget.config["dock"] = "top-left"
            x, y = self.widget._position(self.widget._bar_size)
            self.assertEqual(x + core.SHADOW_PAD, bounds[0] + core.px(14))
            self.assertEqual(y + core.SHADOW_PAD, bounds[1] + core.px(14))
            self.widget.config["dock"] = "bottom-right"
            x, y = self.widget._position(self.widget._bar_size)
            self.assertEqual(x + self.widget._bar_size[0] - core.SHADOW_PAD, bounds[0] + bounds[2] - core.px(14))
            self.assertEqual(y + self.widget._bar_size[1] - core.SHADOW_PAD, bounds[1] + bounds[3] - core.px(14))

    def test_saved_position_recovers_after_monitor_is_removed(self):
        self.widget.config.update(dock="free", x=-10000, y=10000)
        with patch("smith_agents.app.platform.screen_bounds", return_value=(0, 50, 2000, 1500)):
            x, y = self.widget._position(self.widget._bar_size)
            self.assertGreaterEqual(x, -core.SHADOW_PAD)
            self.assertLessEqual(y + self.widget._bar_size[1] - core.SHADOW_PAD, 1550)

    def test_malformed_old_settings_do_not_prevent_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            import json
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"x": "bad", "opacity": None, "interval": "bad", "console_tab": "missing"}))
            with patch("smith_agents.app.CONFIG_PATH", str(path)):
                config = self.widget._load_config()
                self.assertEqual(config["interval"], 300)
                self.assertEqual(config["opacity"], 1)
                self.assertIsNone(config["x"])
                self.assertEqual(config["console_tab"], "agents")


if __name__ == "__main__":
    unittest.main()

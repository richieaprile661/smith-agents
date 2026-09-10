"""Exercise the real AppKit shell with sample data and synthetic window events.

Run from the repo: .venv/bin/python tools/smoke_macos.py
No global mouse/keyboard events, credential reads, or account requests are made.
"""
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
demo_dir = tempfile.TemporaryDirectory(prefix="smith-agents-smoke-")
os.environ["SMITH_AGENTS_CONFIG_DIR"] = demo_dir.name

import AppKit as A
from smith_agents.app import SmithAgentsWidget
from smith_agents import core
from smith_agents import platform_darwin as mac


def main():
    widget = SmithAgentsWidget(demo=True)
    failures = []

    def click(kind):
        row = next(row for row in widget.agent_rows if row[0] == kind)
        x, y = (row[1] + row[3]) / 2, (row[2] + row[4]) / 2
        local = A.NSMakePoint(x / mac.RASTER_SCALE, y / mac.RASTER_SCALE)
        window_point = widget.root.view.convertPoint_toView_(local, None)
        for event_type, method in ((A.NSEventTypeLeftMouseDown, widget.root.view.mouseDown_),
                                    (A.NSEventTypeLeftMouseUp, widget.root.view.mouseUp_)):
            event = A.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                event_type, window_point, 0, 0, widget.root.panel.windowNumber(), None, 1, 1, 1.)
            method(event)

    def exercise():
        try:
            widget._repaint()
            assert widget.root.panel.isVisible()
            assert widget.root.view.image is not None
            assert widget.tray.item.button().image().isTemplate()
            assert widget.root.panel.frame().size.width == widget._bar_size[0] / mac.RASTER_SCALE
            assert widget.root.panel.canBecomeKeyWindow() is False
            for tab in ("usage", "stats", "agents"):
                click("tab:" + tab)
                assert widget.config["console_tab"] == tab, tab
            click("reading")
            assert widget.config["bar_mode"] == "session"
            click("bar")
            assert widget.config["console_open"] is False
            click("bar")
            assert widget.config["console_open"] is True
            widget.set_tuck(True)
            for _ in range(16):
                widget._repaint()
            assert widget.tuck_anim == 1.0
            click("peek")
            assert widget.config["tucked"] is False
            for _ in range(16):
                widget._repaint()
            widget.toggle_bar()
            assert not widget.root.panel.isVisible()
            widget.toggle_bar()
            assert widget.root.panel.isVisible()
            for dock in ("top-left", "top-right", "bottom-right"):
                widget.set_dock(dock)
                widget._repaint()
                frame = widget.root.panel.frame()
                assert frame.size.width > 0 and frame.size.height > 0
            widget.tray.refresh_menu(widget.tray.menu)
            assert widget.tray.menu.numberOfItems() >= 12
            # Exercise each native review button directly, without presenting
            # a real permission request or sending input globally.
            for title, expected in (("Cancel", None), ("Allow once", "allow"), ("Deny", "deny")):
                alert, target = mac.permission_alert({"name": "Demo agent", "cwd": "/sample"},
                    {"request": {"tool_name": "Bash", "input": {"command": "echo example"}}})
                button = next(button for button in alert.buttons() if button.title() == title)
                button.performClick_(None)
                assert target.clicked and target.choice == expected, (title, target.clicked, target.choice)
            # Use a constrained screen to exercise native clicks through the
            # scroll viewport, including a non-destructive End session cancel.
            saved_agents = widget.agents
            bounds = widget._screen_bounds()
            with patch.object(widget, "_scan_agents"), patch.object(widget, "_screen_bounds",
                    return_value=(bounds[0], bounds[1], bounds[2], core.px(400))):
                widget.agents = [core.demo_agents()[1]]
                widget._repaint()
                click("row")
                assert widget.agent_open == widget.agents[0]["id"]
                assert widget._agent_scroll_max > 0
                click("scroll-down")
                assert widget._agent_scroll > 0
                widget._scroll_agents(widget._agent_scroll_max)
                click("kill")
                assert widget._confirm_kill == widget.agents[0]["id"]
                click("no")
                assert widget._confirm_kill is None
                widget._scroll_agents(widget._agent_scroll_max)
                click("scroll-up")
                assert widget._agent_scroll < widget._agent_scroll_max
            widget.agents = saved_agents
            widget.agent_open = None
            widget._agent_scroll = 0
            widget._repaint()
            widget.root.after(100, widget._repaint)
            log = Path(demo_dir.name) / "widget.log"
            assert not log.exists() or "failed" not in log.read_text()
            # Capture only this view, not unrelated desktop content.
            view = widget.root.view
            rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
            view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
            png = rep.representationUsingType_properties_(A.NSBitmapImageFileTypePNG, {})
            target = Path("build/previews/mac-native.png")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bytes(png))
            print("PASS: native panel, Retina image, template icon, tabs, reading, fold, tuck, visibility, docking, menu, agent drawer, scrolling, End session cancel")
        except Exception:
            failures.append(traceback.format_exc())
        finally:
            widget.quit()

    widget.root.after(300, exercise)
    widget.root.after(15000, widget.quit)
    widget.run()
    for failure in failures:
        print(failure, file=sys.stderr)
    demo_dir.cleanup()
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())

"""Shared application controller and command-line entry point."""
import argparse
import faulthandler
import json
import os
import random
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from . import core, artwork, figure_actions, tucked, codex_usage
from .runtime import backend
from .permissions import answer_permission
from .core import (
    AGENT_FRAME_MS,
    APP_NAME,
    BAR_H,
    CONFIG_DIR,
    CONFIG_PATH,
    CONSOLE_W,
    DEFAULTS,
    DOCKS,
    IDLE_REFRESH_MS,
    MAX_BACKOFF_S,
    PEEK_W,
    SHADOW_PAD,
    TAB_H,
    THEMES,
    THEME_NAME,
    TUCK_FRAME_MS,
    TUCK_STEP,
    UsageError,
    ZOOM,
    active_projects,
    build_metrics,
    build_spend,
    build_stats,
    choose_front,
    decorate_agents,
    demo_agents,
    fetch_usage,
    list_agents,
    load_cache,
    log_line,
    next_bar_mode,
    px,
    raise_agent_window,
    hide_agent_window,
    render_console,
    render_tray,
    save_cache,
    session_metric,
    terminate_agent,
)

platform = backend()

class SmithAgentsWidget:
    def __init__(self, demo=False):
        self.demo = demo
        self.config = self._load_config()
        if demo:
            self.config.update(demo_figures=True, visible=True)
        self.lock = threading.Lock()
        self.metrics = []
        self.spend = None
        self.plan = None
        self.active = []
        self.error = None
        self.updated_at = None
        self.cached_at = 0
        self.phase = 0
        self.tuck_anim = 1.0 if self.config["tucked"] else 0.0
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.codex_wake = threading.Event()
        self._drag = None
        self._tray_cache = None
        self._tick_job = None
        self._paint_xy = (0, 0)
        self._bar_size = (CONSOLE_W + SHADOW_PAD * 2, BAR_H + SHADOW_PAD * 2)
        self.stats = {}
        self.codex_data = {"metrics": [], "stats": codex_usage.build_stats({}),
                           "usage_error": "Loading Codex usage…", "stats_error": "Loading Codex stats…"}
        if not demo:
            try:
                with open(os.path.join(CONFIG_DIR, "codex-usage.json"), encoding="utf-8") as handle:
                    cached = json.load(handle)
                if isinstance(cached.get("metrics"), list) and isinstance(cached.get("stats"), dict):
                    self.codex_data.update(cached, usage_error="Cached Codex reading · refreshing…",
                                           stats_error="Cached Codex reading · refreshing…")
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        self.agents = []
        self.agent_rows = []            # (kind, box, agent) in window coords
        self.agent_open = None          # the one row whose drawer is open
        self._agent_scroll = 0
        self._agent_scroll_max = 0
        self._confirm_kill = None
        self._agents_scan = 0.0
        self._figure_timeline = figure_actions.Timeline()
        self._figure_assignments = artwork.FigureAssignments()
        self._peek_order = tucked.AgentOrder()
        self._peek_open_id = None
        self._peek_scroll = 0
        self._peek_panel_scroll = 0
        self._peek_details = True
        self._peek_layout = None

        # Warm start from the last good response, so a restart shows numbers
        # immediately and does not spend a request against the rate limit.
        payload, plan, fetched_at = (None, None, 0) if demo else load_cache()
        if payload:
            self.metrics = build_metrics(payload)
            self.spend = build_spend(payload)
            self.plan = plan
            self.cached_at = fetched_at
            self.updated_at = datetime.fromtimestamp(fetched_at)
            # Only call it stale once the cache has outlived a poll interval;
            # a reading from two minutes ago is still the current answer.
            if time.time() - fetched_at > self.config["interval"]:
                self.error = ("cached", "cached reading from %s"
                              % self.updated_at.strftime("%H:%M"))

        self.root, self.surface = platform.create_shell(self, core)

        self.menu = self._build_context_menu()
        self.tray = self._build_tray()

        if demo:
            from .sample_data import demo_payload, demo_stats
            self.metrics = build_metrics(demo_payload())
            self.stats = demo_stats()
            self.codex_data = {"metrics": codex_usage.build_metrics({"rateLimits": {
                "primary": {"usedPercent": 37, "windowDurationMins": 300},
                "secondary": {"usedPercent": 62, "windowDurationMins": 10080}}}),
                "stats": codex_usage.build_stats({"summary": {"lifetimeTokens": 1234567,
                    "peakDailyTokens": 345678, "longestRunningTurnSec": 321,
                    "currentStreakDays": 3, "longestStreakDays": 8}, "dailyUsageBuckets": []}),
                "usage_error": None, "stats_error": None}
            self.plan = "Demo"
            self.updated_at = datetime.now()
        else:
            threading.Thread(target=self._poll_loop, daemon=True).start()
            threading.Thread(target=self._codex_poll_loop, daemon=True).start()
        platform.start_tray(self.tray)
        self.root.after(120, self._tick)

    # -- config ------------------------------------------------------------
    def _load_config(self):
        config = dict(DEFAULTS)
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                saved = json.load(fh)
                if isinstance(saved, dict):
                    config.update(saved)
        except (OSError, ValueError):
            pass
        if config.get("dock") not in DOCKS:
            config["dock"] = DEFAULTS["dock"]
        for key in ("x", "y", "tuck_x", "tuck_y"):
            try:
                config[key] = int(config[key]) if config.get(key) is not None else None
            except (TypeError, ValueError, OverflowError):
                config[key] = None
        for key, default, lower, upper in (("opacity", 1.0, .4, 1.),
                                           ("interval", 300, 15, 86400),
                                           ("margin", 14, 0, 200)):
            try:
                config[key] = min(upper, max(lower, float(config.get(key, default))))
            except (TypeError, ValueError):
                config[key] = default
        if config.get("console_tab") not in ("agents", "usage", "stats"):
            config["console_tab"] = "agents"
        if config.get("tuck_side") not in tucked.EDGES:
            config["tuck_side"] = "right"
        if config.get("bar_mode") not in core.BAR_MODES:
            config["bar_mode"] = "worst"
        if config.get("reading_view") not in core.READING_VIEWS:
            config["reading_view"] = "used"
        if config.get("usage_provider") not in ("claude", "codex"):
            config["usage_provider"] = "claude"
        config["zoom"] = ZOOM      # already clamped, and baked into the sizes
        config["theme"] = THEME_NAME    # already validated, baked into the tokens
        return config

    def _save_config(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(self.config, fh, indent=2)
        except OSError:
            pass

    def connect_claude(self):
        import webbrowser
        platform.show_error("Open Terminal, run claude, and sign in. Then choose Refresh now in this menu.")
        webbrowser.open("https://code.claude.com/docs/en/authentication")

    def connect_codex(self):
        from .codex_hooks import install
        try:
            install()
        except (OSError, ValueError) as error:
            platform.show_error(str(error))
            return
        platform.show_error("Codex status hooks are installed. In Codex, use /hooks to review "
                            "and trust the Widget session status hooks, then start or resume a session. "
                            "They only report activity; approvals stay in Codex. "
                            "Existing terminal and VS Code sessions are also detected automatically.")

    def _screen_bounds(self):
        point = getattr(self, '_peek_drag_point', None)
        if point is None:
            keys = ('tuck_x', 'tuck_y') if self.config.get('tucked') else ('x', 'y')
            if self.config.get(keys[0]) is None or self.config.get(keys[1]) is None:
                keys = ('x', 'y')
            if self.config.get(keys[0]) is not None and self.config.get(keys[1]) is not None:
                point = self.config[keys[0]], self.config[keys[1]]
        return platform.screen_bounds(self.root, point)

    # -- polling -----------------------------------------------------------
    def _scan_local(self):
        """Transcript-derived state. Local and cheap, so it never waits on the
        API poll clock - a warm start has today's numbers straight away."""
        try:
            found = active_projects()
        except Exception:
            found = []
        try:
            stats = build_stats()
        except Exception:
            stats = None
        with self.lock:
            self.active = found
            if stats is not None:
                self.stats = stats

    def _poll_loop(self):
        backoff = 0
        self._scan_local()
        # A warm cache still counts as a poll: wait out its remaining life first.
        if self.cached_at:
            remaining = self.config["interval"] - (time.time() - self.cached_at)
            if remaining > 0:
                self.wake.wait(remaining)
                self.wake.clear()
        while not self.stopping.is_set():
            try:
                payload, oauth = fetch_usage()
                metrics = build_metrics(payload)
                spend = build_spend(payload)
                plan = oauth.get("subscriptionType")
                with self.lock:
                    self.metrics, self.spend, self.plan = metrics, spend, plan
                    self.error = None
                    self.updated_at = datetime.now()
                save_cache(payload, plan)
                backoff = 0
            except UsageError as exc:
                with self.lock:
                    self.error = (exc.kind, str(exc))
                if exc.kind == "ratelimit":
                    # 120, 240, 480 ... The endpoint sends Retry-After: 0, which is
                    # useless, and the real penalty outlasts a minute, so start high.
                    backoff = min(MAX_BACKOFF_S,
                                  max(exc.retry_after, backoff * 2 if backoff else 120))
                else:
                    backoff = 0
            except Exception as exc:  # never let the poller die
                with self.lock:
                    self.error = ("other", str(exc))
                backoff = 0
            self._scan_local()
            # Jitter keeps the poll off Claude Code's own /usage cadence.
            delay = backoff or self.config["interval"]
            self.wake.wait(delay + random.uniform(0, delay * 0.2))
            self.wake.clear()

    def refresh_now(self, *_args):
        self.wake.set()
        self.codex_wake.set()

    def _codex_poll_loop(self):
        from .codex_hooks import atomic_json
        from pathlib import Path
        while not self.stopping.is_set():
            try:
                result = codex_usage.fetch()
                with self.lock:
                    for key, target, builder in (("limits", "metrics", codex_usage.build_metrics),
                                                  ("activity", "stats", codex_usage.build_stats)):
                        error_key = "usage_error" if key == "limits" else "stats_error"
                        if key in result:
                            self.codex_data[target] = builder(result[key])
                            self.codex_data[error_key] = None
                            self.codex_data[target + "_updated"] = time.time()
                        else:
                            self.codex_data[error_key] = result.get(key + "_error", "Codex reading unavailable")
                    cached = dict(self.codex_data)
                try:
                    atomic_json(Path(CONFIG_DIR) / "codex-usage.json", cached)
                except OSError:
                    pass  # A cache-write failure does not invalidate a live reading.
            except Exception as error:
                message = str(error) if isinstance(error, codex_usage.Unavailable) else "Codex usage unavailable"
                with self.lock:
                    self.codex_data.update(usage_error=message, stats_error=message)
            self.codex_wake.wait(max(60, self.config["interval"]))
            self.codex_wake.clear()

    def set_usage_provider(self, provider):
        if provider not in ("claude", "codex"):
            return
        self.config["usage_provider"] = provider
        self.config["bar_mode"] = "worst"
        self._save_config()

    def _data_notice(self):
        if self.config.get("usage_provider") != "codex":
            return "Cached reading · " + self.error[1] if self.error and self.metrics else None
        with self.lock:
            key = "stats" if self.config.get("console_tab") == "stats" else "metrics"
            error = self.codex_data.get("stats_error" if key == "stats" else "usage_error")
            if error and self.codex_data.get(key + "_updated"):
                return "Last reading · " + error
            return error

    # -- rendering ---------------------------------------------------------
    def _snapshot(self):
        with self.lock:
            if self.config.get("usage_provider") == "codex":
                data = self.codex_data
                error = data.get("usage_error")
                stamp = data.get("metrics_updated")
                return (list(data["metrics"]), None, None, [],
                        ("codex", error) if error else None,
                        datetime.fromtimestamp(stamp) if stamp else None, dict(data["stats"]))
            return (list(self.metrics), self.spend, self.plan, list(self.active),
                    self.error, self.updated_at, dict(self.stats))

    def _paint_frame(self):
        metrics, spend, plan, active, error, updated, stats = self._snapshot()
        live = bool(active) and not error
        if live:
            self.phase = (self.phase + 9) % 360

        front, _rest = choose_front(metrics)
        animating = self._step_tuck()
        now = time.time()
        animation_now = time.monotonic()
        header_frame = (figure_actions.frame_at(self._figure_timeline.elapsed(
            ('header',), artwork.HEADER, animation_now)) if self.config["visible"] else 0)

        def figure_elapsed(agent):
            if not self.config["visible"]:
                return 0
            return self._figure_timeline.elapsed(
                ('row', agent.get('id') or agent.get('name')),
                (core.agent_style(agent)[0], agent.get('state')), animation_now)

        if now - self._agents_scan > 1.0 or not self.agents:
            self._scan_agents(now)
        if self.tuck_anim >= 1.0:
            rows = self._peek_order.sync(self.agents)
            if not any(a.get('id') == self._peek_open_id for a in rows):
                self._peek_open_id = None
            bounds = self._screen_bounds()
            bar_x, bar_y = self._position(self._bar_size)
            anchor_x = self.config.get('tuck_x')
            anchor_y = self.config.get('tuck_y')
            image, boxes, self._peek_layout = tucked.render(
                rows, metrics, front, now, side=self.config['tuck_side'], mode=self.config['bar_mode'],
                max_height=bounds[3]-px(28), max_width=bounds[2],
                rail_y=(bar_y+SHADOW_PAD if anchor_y is None else anchor_y)-bounds[1]-px(14),
                rail_x=(bar_x+SHADOW_PAD if anchor_x is None else anchor_x)-bounds[0],
                selected=self._peek_open_id, scroll=self._peek_scroll,
                panel_scroll=self._peek_panel_scroll, details=self._peek_details,
                confirm_id=self._confirm_kill,
                figure_elapsed=lambda agent: figure_actions.periodic_elapsed(figure_elapsed(agent)),
                provider=self.config.get("usage_provider", "claude"))
            self._peek_scroll = min(self._peek_scroll, self._peek_layout.rail_scroll_max)
            self._peek_panel_scroll = min(self._peek_panel_scroll, self._peek_layout.panel_scroll_max)
            x, y = self._peek_position(image.size)
            self.agent_rows = [(kind, x0 + SHADOW_PAD, y0 + SHADOW_PAD,
                                x1 + SHADOW_PAD, y1 + SHADOW_PAD, agent)
                               for kind, x0, y0, x1, y1, agent in boxes]
        else:
            notice = None
            if front is None:
                kind = error[0] if error else ""
                notice = (kind, {"auth": "Sign in to Claude",
                                 "codex": error[1] if error else "Codex usage unavailable",
                                 "ratelimit": "Rate limited, retrying",
                                 "cached": "No reading yet"}.get(
                                     kind, "Usage offline"))
            max_height = self._screen_bounds()[3] - px(28) - 2 * SHADOW_PAD
            self._agent_scroll_max = max(0, BAR_H + TAB_H + core.agents_height(self.agents, self.agent_open) - max_height)
            self._agent_scroll = min(self._agent_scroll, self._agent_scroll_max)
            image, boxes = render_console(
                metrics, spend, stats, self.agents, now,
                self.config["console_tab"], self.agent_open,
                self._confirm_kill, self.config["console_open"],
                self.config["bar_mode"], notice, max_height=max_height, scroll=self._agent_scroll,
                figure_elapsed=figure_elapsed, header_frame=header_frame,
                provider=self.config.get("usage_provider", "claude"), data_notice=self._data_notice(),
                reading_view=self.config.get("reading_view", "used"))
            self._bar_size = image.size
            x, y = self._slide_position(image.size)
            # boxes are shell-relative; the shadow pad offsets them in the image
            self.agent_rows = [(kind, x0 + SHADOW_PAD, y0 + SHADOW_PAD,
                                x1 + SHADOW_PAD, y1 + SHADOW_PAD, agent)
                               for kind, x0, y0, x1, y1, agent in boxes]

        if self.config["visible"]:
            self.root.geometry("%dx%d+%d+%d" % (image.size[0], image.size[1], x, y))
            self.surface.paint(image, x, y, self.config["opacity"])
        # UpdateLayeredWindow positions the surface itself, so Tk's idea of the
        # window origin cannot be trusted for hit testing - keep the real one.
        self._paint_xy = (x, y)
        self._figure_timeline.sync({
            ('header',): artwork.HEADER,
            **{('row', agent.get('id') or agent.get('name')):
               (core.agent_style(agent)[0], agent.get('state')) for agent in self.agents},
        })

        # the tray follows the session, not the worst limit
        self._update_tray(session_metric(metrics) or front, live, error)
        if animating:
            delay = TUCK_FRAME_MS
        elif self.config["visible"]:
            # Both the agent drawings and the header/peek stars have gentle
            # loops. Keep their timing consistent across tabs and folded views.
            delay = max(1, AGENT_FRAME_MS - round((time.monotonic() - animation_now) * 1000))
        else:
            delay = IDLE_REFRESH_MS
        self._tick_job = self.root.after(delay, self._tick)

    def _tick(self):
        """The paint loop's guard. An exception anywhere in a frame used to end
        the `after` chain outright - no traceback, no window, no clue, just a
        widget that stopped. Now it lands in the log and the next frame runs."""
        try:
            self._paint_frame()
        except Exception:
            log_line("tick failed\n" + traceback.format_exc().rstrip())
            self._tick_job = self.root.after(IDLE_REFRESH_MS, self._tick)

    def _tuck_side(self):
        """Which edge the chip would tuck to from where it sits now."""
        x, y = self._position(self._bar_size)
        left, top, width, _height = self._screen_bounds()
        gaps = {'left': abs(x+SHADOW_PAD-left),
                'right': abs(left+width-(x+self._bar_size[0]-SHADOW_PAD)),
                'top': abs(y+SHADOW_PAD-top)}
        return min(tucked.EDGES, key=gaps.get)

    def _on_motion(self, event):
        """Kept so a future hover treatment has somewhere to live; the console
        draws no hover state, which a layered window cannot track reliably
        anyway."""
        return None

    def _repaint(self):
        """Redraw straight away instead of waiting out the idle tick."""
        if self._tick_job:
            self.root.after_cancel(self._tick_job)
            self._tick_job = None
        self._tick()

    # -- tucking -----------------------------------------------------------
    def _step_tuck(self):
        """Advance the slide animation. Returns True while it is still moving."""
        target = 1.0 if self.config["tucked"] else 0.0
        if abs(self.tuck_anim - target) < 1e-3:
            self.tuck_anim = target
            return False
        step = TUCK_STEP if target > self.tuck_anim else -TUCK_STEP
        self.tuck_anim = min(1.0, max(0.0, self.tuck_anim + step))
        return True

    @staticmethod
    def _ease(t):
        return t * t * (3 - 2 * t)          # smoothstep

    def _slide_position(self, size):
        """Parked position, then slid toward the edge by the animation."""
        x, y = self._position(size)
        if self.tuck_anim <= 0:
            return x, y
        left, top, screen_w, _height = self._screen_bounds()
        chip_w = size[0] - SHADOW_PAD * 2
        if self.config['tuck_side'] == 'top':
            gone = top-size[1]+SHADOW_PAD+px(80)
            return x, int(round(y+(gone-y)*self._ease(self.tuck_anim)))
        if self.config["tuck_side"] == "left":
            gone = left - chip_w + PEEK_W - SHADOW_PAD
        else:
            gone = left + screen_w - PEEK_W - SHADOW_PAD
        return int(round(x + (gone - x) * self._ease(self.tuck_anim))), y

    def _peek_position(self, size):
        """Anchor the strip itself, so opening a panel cannot move it.

        Top placement keeps its saved x; side placement keeps its saved y.
        The legacy fallback uses the console's bar until a strip is rendered.
        """
        if getattr(self, '_peek_layout', None) is not None:
            left, top, screen_w, _height = self._screen_bounds()
            if self._peek_layout.horizontal:
                return (left+self._peek_layout.rail_x-self._peek_layout.rail[0],
                        top-self._peek_layout.rail[1])
            x = left-SHADOW_PAD if self.config['tuck_side'] == 'left' else left+screen_w-size[0]+SHADOW_PAD
            return x, top+px(14)+self._peek_layout.rail_y-self._peek_layout.rail[1]
        x, _unused = self._position(size)
        _also, y = self._position(self._bar_size)
        left, _top, screen_w, _height = self._screen_bounds()
        if self.config["tuck_side"] == "left":
            x = left - SHADOW_PAD
        else:
            x = left + screen_w - PEEK_W - SHADOW_PAD
        return x, y

    def set_tuck(self, tucked, side=None):
        self._peek_open_id = None
        self._peek_panel_scroll = 0
        self._confirm_kill = None
        if tucked:
            if not self.config['tucked']:
                x, y = self._position(self._bar_size)
                self.config['tuck_x'], self.config['tuck_y'] = x+SHADOW_PAD, y+SHADOW_PAD
            self.config['tuck_side'] = side or self._tuck_side()
            self._peek_scroll = 0
        self.config["tucked"] = bool(tucked)
        self._save_config()

    def toggle_tuck(self):
        self.set_tuck(not self.config["tucked"])

    def _position(self, size):
        """Where the shadow canvas goes, so that the chip itself lands on the dock."""
        width, height = size
        chip_w = width - SHADOW_PAD * 2
        chip_h = height - SHADOW_PAD * 2
        dock = self.config["dock"]
        margin = px(self.config["margin"])
        left, top, screen_w, screen_h = self._screen_bounds()
        if dock == "free" and self.config.get("x") is not None and self.config.get("y") is not None:
            x, y = int(self.config["x"]), int(self.config["y"])
            # Recover a saved position after unplugging a monitor, while
            # retaining the shadow margin outside the visible frame.
            return (max(left - SHADOW_PAD, min(x, left + screen_w - width + SHADOW_PAD)),
                    max(top - SHADOW_PAD, min(y, top + screen_h - height + SHADOW_PAD)))
        if dock.endswith("left"):
            chip_x = margin
        elif dock.endswith("center"):
            chip_x = (screen_w - chip_w) // 2
        else:
            chip_x = screen_w - chip_w - margin
        chip_y = margin if dock.startswith("top") else screen_h - chip_h - margin
        return left + chip_x - SHADOW_PAD, top + chip_y - SHADOW_PAD

    # -- tray --------------------------------------------------------------
    def _update_tray(self, front, live, error):
        """`front` here is the session limit, not the bar's leading one."""
        lines = []
        if error:
            lines.append("! " + error[1])
        metrics, spend, _plan, active, _err, _upd, _today = self._snapshot()
        for metric in metrics:
            lines.append("%-14s %3d%%" % (metric["detail"], round(metric["pct"])))
        if spend:
            lines.append("%-14s %s" % ("Credits", spend["text"]))
        lines.append("Active: %s" % (", ".join(active) if active else "idle"))
        # keyed on the text it will show, not on the front metric alone: the
        # tooltip lists every limit, the spend and the active projects, and any
        # of those can move while the front one stands still
        key = (tuple(lines), bool(live), bool(error))
        if key == self._tray_cache:
            return
        self._tray_cache = key
        try:
            self.tray.icon = render_tray(front, live, error)
            # Shell_NotifyIcon hard-caps the tooltip at 128 chars including the title.
            self.tray.title = ("%s\n%s" % (APP_NAME, "\n".join(lines)))[:127]
        except Exception:
            pass

    def _build_tray(self):
        return platform.build_tray(self)


    # -- context menu ------------------------------------------------------
    def toggle_demo(self):
        """Swap the real sessions for every approved pose, to look at the
        figures. Nothing is terminated or touched while it is on."""
        if self.demo:
            return  # --demo never reads real sessions
        self.config["demo_figures"] = not self.config["demo_figures"]
        if hasattr(self, "_demo_var"):
            self._demo_var.set(self.config["demo_figures"])
        self.agents = []
        self._save_config()
        self._repaint()

    def _build_context_menu(self):
        return platform.build_context_menu(self)


    def _on_context(self, event):
        platform.popup_menu(self, event)


    # -- actions -----------------------------------------------------------
    def set_dock(self, name):
        self.config["dock"] = name
        if name != "free":
            self.config["x"] = self.config["y"] = None
            self.config['tuck_x'] = self.config['tuck_y'] = None
            if self.config['tucked']:
                self.config['tuck_side'] = self._tuck_side()
        self._save_config()

    def set_theme(self, name):
        """Colour, font and geometry are baked in at import, so a theme change
        relaunches the widget rather than trying to rebuild every constant."""
        if name == THEME_NAME or name not in THEMES:
            return
        self.config["theme"] = name
        self._save_config()
        self.relaunch()

    def set_zoom(self, value):
        """Like the theme, every size is baked in at import, so changing it
        relaunches rather than trying to rebuild the constants."""
        if abs(ZOOM - value) < 0.01:
            return
        self.config["zoom"] = value
        self._save_config()
        self.relaunch()

    def relaunch(self):
        argv, cwd = platform.launch_argv()
        if self.demo:
            argv.append("--demo")
        # Drop the single-instance lock first, otherwise the process we are
        # about to start would see us and exit, taking the widget with it.
        platform.release_single_instance()
        try:
            platform.relaunch(argv, cwd)
        except OSError:
            platform.claim_single_instance()
            return
        self.quit()

    def set_opacity(self, value):
        self.config["opacity"] = value
        self._save_config()

    def toggle_bar(self):
        self.config["visible"] = not self.config["visible"]
        if self.config["visible"]:
            self.root.deiconify()
        else:
            self.root.withdraw()
        self._save_config()

    def toggle_autostart(self):
        try:
            platform.set_autostart(not platform.autostart_enabled())
        except (OSError, subprocess.SubprocessError) as exc:
            log_line("autostart failed: " + type(exc).__name__)
            platform.show_error("Could not change automatic startup. Please try again.")


    def quit(self, *_args):
        self.stopping.set()
        self.wake.set()
        self.codex_wake.set()
        try:
            self.tray.stop()
        except Exception:
            pass
        self.root.after(0, self.root.destroy)

    # -- dragging ----------------------------------------------------------
    def _on_press(self, event):
        """Every press arms a drag and remembers where it started. The console
        covers its whole surface with controls, so there is no dead margin left
        to grab it by - a press that never moves is the click, one that moves
        is the drag."""
        if self.config["tucked"]:
            self._drag = None
            layout = self._peek_layout
            if layout is None or not (layout.rail[0] <= event.x <= layout.rail[2]
                                       and layout.rail[1] <= event.y <= layout.rail[3]):
                return
            x, y = self._paint_xy
            self._peek_drag_origin = x+layout.rail[0], y+layout.rail[1]
        self._drag = (event.x_root, event.y_root,
                      self.root.winfo_x(), self.root.winfo_y(), False)

    def _on_drag(self, event):
        if not self._drag:
            return
        sx, sy, wx, wy, _moved = self._drag
        dx, dy = event.x_root - sx, event.y_root - sy
        if abs(dx) < px(4) and abs(dy) < px(4):
            return                       # still inside the slop; not a drag yet
        self._drag = (sx, sy, wx, wy, True)
        if self.config['tucked']:
            self._peek_drag_point = event.x_root, event.y_root
            bounds = self._screen_bounds()
            self.config['tuck_x'] = self._peek_drag_origin[0]+dx
            self.config['tuck_y'] = self._peek_drag_origin[1]+dy
            edge = tucked.nearest_edge(bounds, event.x_root, event.y_root)
            if edge != self.config['tuck_side']:
                self._peek_scroll = 0
            self.config['tuck_side'] = edge
            self._peek_open_id = None
            self._confirm_kill = None
            self._repaint()
            return
        self.config["dock"] = "free"
        self.config["x"], self.config["y"] = wx + dx, wy + dy

    def _on_release(self, event):
        if self.config["tucked"]:
            moved = self._drag is not None and self._drag[4]
            self._drag = None
            if moved:
                x, y = self._paint_xy
                self.config['tuck_x'] = x+self._peek_layout.rail[0]
                self.config['tuck_y'] = y+self._peek_layout.rail[1]
                self._peek_drag_point = None
                self._save_config()
            else:
                self._on_peek_click(event)
            self._repaint()
            return
        if not self._drag:
            return
        moved = self._drag[4]
        self._drag = None
        if moved:
            self._snap()
            self._save_config()
        else:
            self._on_console_click(event)

    def _scan_agents(self, now):
        """Read the sessions, minus the closed rows you have cleared away.

        The dismissal list is pruned against what the scan actually saw, so an
        id whose session file is finally gone stops being remembered rather
        than accumulating in the config forever."""
        if self.config["demo_figures"]:
            # the demo rows are not sessions, so they can neither be pruned
            # against nor dismissed - pruning against them threw the list away
            self.agents = demo_agents(all_figures=True)
            self._agents_scan = now
            return
        found = decorate_agents(list_agents())
        dismissed = [d for d in self.config.get("dismissed") or []
                     if any(a.get("id") == d for a in found)]
        if dismissed != (self.config.get("dismissed") or []):
            self.config["dismissed"] = dismissed
            self._save_config()
        self.agents = [a for a in found if a.get("id") not in dismissed]
        self._figure_assignments.assign(self.agents)
        self._sync_agent_windows()
        self._agents_scan = now

    def _sync_agent_windows(self):
        from .session_windows import window_agent
        for agent in self.agents:
            if not agent.get('sub'):
                agent['_window_state'] = platform.agent_window_state(agent)
                agent['_window_open'] = platform.agent_window_is_visible(agent)
        for agent in self.agents:
            if agent.get('sub'):
                parent = window_agent(agent, self.agents)
                agent['_window_state'] = parent.get('_window_state', 'unknown') if parent else 'unknown'
                agent['_window_open'] = bool(parent and parent.get('_window_open'))

    def _finish_window_hide(self, agent, remaining=5):
        """Observe the target after its event loop handles the hide request."""
        if platform.agent_window_is_visible(agent):
            if remaining:
                self.root.after(150, lambda: self._finish_window_hide(agent, remaining - 1))
                return
            platform.show_error("This window could not be hidden. The agent is still running.")
        self._sync_agent_windows()
        self._repaint()

    def _dismiss(self, ids):
        """Clear a closed row away. Nothing is killed and nothing is deleted -
        the session is already over, this is the list catching up."""
        ids = [i for i in ids if i]
        if not ids:
            return
        keep = list(self.config.get("dismissed") or [])
        keep.extend(i for i in ids if i not in keep)
        self.config["dismissed"] = keep
        self._save_config()
        self.agents = [a for a in self.agents if a.get("id") not in ids]

    def _on_peek_click(self, event):
        """The strip stays tucked while its selected panel uses normal actions."""
        for kind, x0, y0, x1, y1, agent in self.agent_rows:
            if not (x0 <= event.x <= x1 and y0 <= event.y <= y1):
                continue
            if kind == 'peek-expand':
                self.set_tuck(False)
            elif kind == 'peek-agent':
                self._peek_open_id = None if self._peek_open_id == agent['id'] else agent['id']
                self._peek_details = True
                self._peek_panel_scroll = 0
                self._confirm_kill = None
            elif kind == 'peek-close':
                self._peek_open_id = None
                self._confirm_kill = None
            elif kind in ('peek-up', 'peek-down', 'peek-panel-up', 'peek-panel-down'):
                attribute = '_peek_panel_scroll' if kind.startswith('peek-panel') else '_peek_scroll'
                maximum = self._peek_layout.panel_scroll_max if kind.startswith('peek-panel') else self._peek_layout.rail_scroll_max
                step = px(120)
                if attribute == '_peek_panel_scroll':
                    panel = self._peek_layout.panel
                    viewport = panel[3]-panel[1]-px(26)-core.FOOT_H
                    step = min(step, max(1, viewport//2))
                movement = -step if kind.endswith('up') else step
                setattr(self, attribute, max(0, min(maximum, getattr(self, attribute)+movement)))
            else:
                return self._on_console_click(event)
            self._repaint()
            return True
        if self._peek_open_id is not None:
            self._peek_open_id = None
            self._confirm_kill = None
            self._repaint()
        return False

    def _scroll_agents(self, pixels):
        if not self.config["console_open"] or self.config["console_tab"] != "agents":
            return
        self._agent_scroll = max(0, min(self._agent_scroll_max, self._agent_scroll + int(pixels)))
        self._repaint()

    def _on_agent_wheel(self, event):
        if self.config.get('tucked'):
            self._scroll_tucked_at(event, -event.delta/120*px(45))
            return
        if event.y >= SHADOW_PAD + BAR_H + TAB_H:
            self._scroll_agents(-event.delta / 120 * px(45))

    def _scroll_tucked_at(self, event, pixels):
        layout = self._peek_layout
        if layout is None:
            return
        for rect, attribute, maximum in ((layout.rail, '_peek_scroll', layout.rail_scroll_max),
                                          (layout.panel, '_peek_panel_scroll', layout.panel_scroll_max)):
            if rect and rect[0] <= event.x <= rect[2] and rect[1] <= event.y <= rect[3]:
                setattr(self, attribute, max(0, min(maximum, getattr(self, attribute)+int(pixels))))
                self._repaint()
                return

    def _reveal_agent(self, agent):
        # Opening a drawer or arming End session brings its identity into view.
        if getattr(self, 'config', {}).get('tucked'):
            self._peek_panel_scroll = 0
            return
        top = 0
        for row in core.sort_agents(self.agents):
            if row.get("id") == agent.get("id"):
                self._agent_scroll = top
                break
            top += core.agent_row_height(row)
            if row.get("id") == self.agent_open:
                top += core.agent_drawer_height(row)

    def _on_console_click(self, event):
        """The bar expands, the corner stroke tucks it away, a tab switches the
        pane, a row opens its drawer, End session arms a confirmation and
        only 'yes' ends a session."""
        for kind, x0, y0, x1, y1, agent in self.agent_rows:
            if not (x0 <= event.x <= x1 and y0 <= event.y <= y1):
                continue
            if kind in ("scroll-up", "scroll-down"):
                self._scroll_agents(px(-100 if kind == "scroll-up" else 100))
                return True
            if kind == "reading":
                if self.config.get("tucked"):
                    # The narrow strip still walks through individual limits.
                    metrics = self._snapshot()[0]
                    self.config["bar_mode"] = next_bar_mode(metrics, self.config["bar_mode"])
                else:
                    view = self.config.get("reading_view", "used")
                    self.config["reading_view"] = core.READING_VIEWS[
                        (core.READING_VIEWS.index(view) + 1) % len(core.READING_VIEWS)]
                self._save_config()
            elif kind.startswith("provider:"):
                provider = kind.partition(":")[2]
                if provider == "toggle":
                    provider = "claude" if self.config.get("usage_provider") == "codex" else "codex"
                self.set_usage_provider(provider)
            elif kind == "tuck":
                self.set_tuck(True)
            elif kind == "bar":
                self.config["console_open"] = not self.config["console_open"]
                self._save_config()
            elif kind.startswith("tab:"):
                self.config["console_tab"] = kind[4:]
                self._confirm_kill = None
                self._save_config()
            elif kind == "yes":
                try:
                    if not terminate_agent(agent):
                        platform.show_error("Could not verify and stop this specific session. "
                                            "Open its chat to stop it there.")
                except PermissionError as error:
                    platform.show_error(str(error))
                self._confirm_kill = None
                self._agents_scan = 0       # refresh without clearing the other rows
            elif kind == "no":
                self._confirm_kill = None
            elif kind == "kill":
                if agent.get("state") == "closed":
                    # there is no process left to confirm killing, so the
                    # cross does the only thing left: clears the row away
                    self._dismiss([agent.get("id")])
                else:
                    self._confirm_kill = agent["id"]
                    self._reveal_agent(agent)
            elif kind == "clear":
                self._dismiss([a.get("id") for a in self.agents
                               if a.get("state") == "closed" and not a.get("sub")])
            elif kind == "open":
                from .session_windows import window_agent
                target = window_agent(agent, getattr(self, 'agents', ()))
                try:
                    if target is None or not raise_agent_window(target):
                        platform.show_error("Could not identify this session's window. Open it manually; other windows were left alone.")
                except PermissionError as error:
                    platform.show_error(str(error))
                self._sync_agent_windows()
            elif kind == "hide":
                from .session_windows import window_agent
                target = window_agent(agent, getattr(self, 'agents', ()))
                if target is None or not hide_agent_window(target):
                    platform.show_error("Could not hide this window. It may have closed or changed. The agent was not stopped.")
                else:
                    self.root.after(150, lambda: self._finish_window_hide(target))
                self._sync_agent_windows()
            elif kind in ("permission", "permission-deny"):
                pending = agent.get("permissions") or []
                if pending:
                    item = pending[0]
                    decision = "deny" if kind == "permission-deny" else platform.review_permission(agent, item)
                    if decision:
                        result = answer_permission(os.path.join(core.CLAUDE_DIR, "widget-context"), item, decision)
                        if not result.get("ok"):
                            platform.show_error(result.get("error") or "Could not answer. Open the chat to check this request.")
                        self._agents_scan = 0
            else:                            # a row: one drawer at a time
                self._confirm_kill = None
                if getattr(self, 'config', {}).get('tucked'):
                    self._peek_details = not self._peek_details
                    self._peek_panel_scroll = 0
                    self._repaint()
                    return True
                self.agent_open = (None if self.agent_open == agent["id"]
                                   else agent["id"])
                if self.agent_open:
                    self._reveal_agent(agent)
            self._repaint()
            return True
        return False

    def _snap(self):
        """Snap back to a named dock when dropped near a corner."""
        width, height = self._bar_size
        chip_w = width - SHADOW_PAD * 2
        chip_h = height - SHADOW_PAD * 2
        left, top, screen_w, screen_h = self._screen_bounds()
        x = int(self.config["x"]) + SHADOW_PAD - left
        y = int(self.config["y"]) + SHADOW_PAD - top
        threshold = px(60)
        vertical = "top" if y < screen_h / 2 else "bottom"
        if x < threshold:
            horizontal = "left"
        elif x + chip_w > screen_w - threshold:
            horizontal = "right"
        elif abs((x + chip_w / 2) - screen_w / 2) < threshold:
            horizontal = "center"
        else:
            return
        if y < threshold or (y + chip_h) > screen_h - threshold:
            self.config["dock"] = "%s-%s" % (vertical, horizontal)
            self.config["x"] = self.config["y"] = None

    def run(self):
        self.root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=APP_NAME + " for Windows and macOS")
    parser.add_argument("--demo", action="store_true", help="Use sample data without reading credentials or sessions")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not platform.claim_single_instance(wait=4.0):
        return 0
    core.log_line("start pid=%d python=%s platform=%s" % (os.getpid(), sys.version.split()[0], sys.platform))
    fault_log = None
    try:
        os.makedirs(core.CONFIG_DIR, exist_ok=True)
        fault_log = open(core.LOG_PATH, "a", encoding="utf-8")
        faulthandler.enable(file=fault_log)
        widget = SmithAgentsWidget(demo=args.demo)
        smoke_errors = []
        if args.smoke_test:
            def finish_smoke():
                try:
                    widget._repaint()
                    assert widget.agent_rows, "No rendered hit targets"
                    if sys.platform == "darwin":
                        assert widget.root.view.image is not None, "No native image"
                        assert widget.root.panel.title() == APP_NAME, "Incorrect app name"
                    elif sys.platform == "win32":
                        assert widget.root.title() == APP_NAME, "Incorrect app name"
                    if args.demo:
                        from types import SimpleNamespace
                        from unittest.mock import patch
                        assert len(widget.metrics) == 3, "Demo data missing"
                        bounds = widget._screen_bounds()
                        with patch.object(widget, "_scan_agents"), patch.object(widget, "_screen_bounds",
                                return_value=(bounds[0], bounds[1], bounds[2], px(400))):
                            widget.config.update(console_open=True, console_tab="agents")
                            widget.agents = [demo_agents()[1]]
                            widget.agent_open = widget.agents[0]["id"]
                            widget._repaint()
                            assert widget._agent_scroll_max > 0, "Missing agent viewport"
                            if sys.platform == "win32":
                                widget.root.event_generate("<MouseWheel>", delta=-120,
                                    x=SHADOW_PAD + px(30), y=SHADOW_PAD + BAR_H + TAB_H + px(30))
                                assert widget._agent_scroll > 0, "Native mouse wheel did not scroll"
                            widget._scroll_agents(widget._agent_scroll_max)
                            def click(kind):
                                box = next(box for box in widget.agent_rows if box[0] == kind)
                                widget._on_console_click(SimpleNamespace(x=(box[1] + box[3]) / 2,
                                                                        y=(box[2] + box[4]) / 2))
                            click("kill")
                            assert widget._confirm_kill == widget.agents[0]["id"]
                            click("no")
                            assert widget._confirm_kill is None
                            click("provider:codex")
                            assert widget._snapshot()[0][0]['key'].startswith('codex:')
                            for expected_view in ("remaining", "reset", "used"):
                                click("reading")
                                assert widget.config['reading_view'] == expected_view
                            click("tab:usage")
                            click("tab:stats")
                            assert widget._snapshot()[-1]['provider'] == 'codex'
                            click("provider:claude")
                            assert widget._snapshot()[0][0]['key'] == 'session'
                            click("tab:agents")
                            widget.config['tucked'] = True
                            widget.tuck_anim = 1.0
                            sample = widget.agents[0]
                            widget.agents = [dict(sample, id='smoke-%d' % i) for i in range(20)]
                            widget._repaint()
                            def peek_click(kind, identity=None):
                                box = next(box for box in widget.agent_rows if box[0] == kind
                                           and (identity is None or box[-1]['id'] == identity))
                                widget._on_peek_click(SimpleNamespace(x=(box[1]+box[3])/2,
                                                                     y=(box[2]+box[4])/2))
                            assert widget._peek_layout.rail_scroll_max > 0
                            peek_click('peek-agent', 'smoke-1')
                            assert widget._peek_open_id == 'smoke-1'
                            assert widget.config['tucked']
                            assert widget._peek_layout.panel is not None
                            peek_click('peek-agent', 'smoke-2')
                            assert widget._peek_open_id == 'smoke-2'
                            while widget._peek_panel_scroll < widget._peek_layout.panel_scroll_max:
                                peek_click('peek-panel-down')
                            peek_click('kill')
                            assert widget._confirm_kill == 'smoke-2'
                            peek_click('no')
                            peek_click('peek-close')
                            assert widget._peek_open_id is None
                            peek_click('peek-down')
                            assert widget._peek_scroll > 0
                            with patch.object(widget, '_save_config'):
                                for edge in tucked.EDGES:
                                    widget.set_tuck(True, side=edge)
                                    widget._repaint()
                                    assert widget._peek_layout.horizontal == (edge == 'top')
                                    previous_provider = widget.config['usage_provider']
                                    peek_click('provider:toggle')
                                    assert widget.config['usage_provider'] != previous_provider
                                    peek_click('peek-agent', 'smoke-1')
                                    assert widget._peek_open_id == 'smoke-1'
                                    if edge == 'top':
                                        assert widget._peek_layout.panel[1] > widget._peek_layout.rail[3]
                                    peek_click('peek-close')
                                # A drag ending over a control must only move.
                                rail = widget._peek_layout.rail
                                x, y = rail[0]+px(12), rail[1]+px(20)
                                origin = widget._paint_xy
                                widget._on_press(SimpleNamespace(x=x, y=y, x_root=origin[0]+x, y_root=origin[1]+y))
                                end = SimpleNamespace(x=x, y=y, x_root=bounds[0]+bounds[2]//2, y_root=bounds[1]+px(2))
                                widget._on_drag(end)
                                widget._on_release(end)
                                assert widget.config['tuck_side'] == 'top'
                                assert widget._peek_open_id is None
                                assert widget.config['tucked']
                            # Demo checks never persist their temporary layout.
                            widget.config['tucked'] = False
                    core.log_line("smoke test passed")
                except Exception:
                    smoke_errors.append(traceback.format_exc())
                    core.log_line("smoke test failed\n" + smoke_errors[-1])
                finally:
                    widget.quit()
            widget.root.after(1800, finish_smoke)
        widget.run()
        return 1 if smoke_errors else 0
    except Exception:
        core.log_line("crash\n" + traceback.format_exc().rstrip())
        raise
    finally:
        platform.release_single_instance()
        if fault_log:
            faulthandler.disable()
            fault_log.close()
        core.log_line("exit pid=%d" % os.getpid())

if __name__ == "__main__":
    sys.exit(main())

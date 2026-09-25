"""Render the README images from the widget's own renderer and sample sessions.

    py tools/render_readme_images.py

Writes docs/images/github-*.png using the current platform's widget fonts.
Nothing here reads credentials, transcripts, account readings, or settings;
each theme renders in its own process against a temporary settings folder.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
# Matrix leads: it is the default theme, and the one the README shows first.
THEMES = ("matrix", "claude", "eink")

# One set of sessions for every image, so counts and names always agree.
# Idle times are seconds since the session's last transcript write.
# Main sessions name their Smith expression: the three sample ids happen to
# hash to the same face, and the README should show that faces differ.
SESSIONS = [
    {"id": "readme-storefront", "name": "storefront", "provider": "codex",
     "model": "gpt-5.3-codex", "entrypoint": "codex-vscode", "state": "working", "idle": 4,
     "_portrait": "smith-03-speaking",
     "tail": [("cmd", "Bash pytest -q tests/checkout")],
     "last_request": "Add coupon support to checkout and cover it with tests.",
     "latest_message": "Coupons apply at checkout. Running the test suite now.",
     "context_tokens": 64200, "context_capacity": 200000,
     # the session being typed in: its cell wears the front bar when tucked
     "_window_state": "front", "can_terminate": False},
    # Subagents wear Smith's colleagues, so the README shows more than his face.
    {"id": "readme-storefront-tests", "name": "checkout tests", "provider": "codex",
     "model": "gpt-5.3-codex", "entrypoint": "codex-vscode", "state": "working", "idle": 2,
     "sub": True, "parent": "readme-storefront",
     "tail": [("cmd", "Bash pytest -q tests/checkout")],
     "last_request": "Run the checkout suite and report failures.",
     "latest_message": "12 passed, 1 failing on coupon expiry.",
     "context_tokens": 18400, "context_capacity": 200000,
     "_window_state": "background", "can_terminate": False},
    {"id": "readme-storefront-docs", "name": "release notes", "provider": "codex",
     "model": "gpt-5.3-codex", "entrypoint": "codex-vscode", "state": "done", "idle": 130,
     "sub": True, "parent": "readme-storefront",
     "tail": [("cmd", "Write CHANGELOG.md")],
     "last_request": "Draft release notes for the coupon feature.",
     "latest_message": "Release notes drafted in CHANGELOG.md.",
     "context_tokens": 9100, "context_capacity": 200000,
     "_window_state": "background", "can_terminate": False},
    # In-widget Allow/Deny exists only for Claude Code in VS Code with the
    # optional launcher, so the session asking for approval is a VS Code one.
    {"id": "readme-api", "name": "api-service", "provider": "claude",
     "model": "claude-opus-4-6", "entrypoint": "claude-vscode", "state": "needs", "idle": 95,
     "_portrait": "smith-05-over-glasses",
     "tail": [("cmd", "Bash npm run migrate")],
     "permissions": [{"actionable": True, "request": {
         "tool_name": "Bash", "input": {"command": "npm run migrate"},
         "description": "Run the database migration"}}],
     "last_request": "Rename the orders table columns and migrate the data.",
     "latest_message": "The migration is ready. I need approval to run it.",
     "context_tokens": 412000, "context_capacity": 1000000,
     "_window_state": "background"},
    {"id": "readme-design", "name": "design-system", "provider": "claude",
     "model": "claude-sonnet-4-6", "entrypoint": "cli", "state": "done", "idle": 42,
     "_portrait": "smith-09-faint-smile",
     "tail": [("cmd", "Edit src/tokens.css")],
     "last_request": "Tighten the spacing scale and update the button tokens.",
     "latest_message": "Components are ready to review. Buttons now use the new scale.",
     "context_tokens": 31800, "context_capacity": 200000,
     "_window_state": "hidden"},
]


def sessions(now):
    rows = []
    for spec in SESSIONS:
        row = dict(spec, pid=0, cwd="~/Projects/" + spec["name"], branch="main",
                   since=now - spec["idle"] - 3 * 3600, active=spec["state"] == "working",
                   stale=False)
        row["tail"] = list(spec["tail"]) + [("prompt", "")]
        rows.append(row)
    return rows


def render_parts(theme, zoom, out_dir):
    """Render real UI in a child process with isolated theme and zoom settings."""
    from smith_agents import runtime
    runtime.backend().display_scale = lambda: (96, 1.0)
    from smith_agents import core, codex_usage, tucked
    from smith_agents.sample_data import demo_stats

    now = time.time()
    agents = sessions(now)
    metrics = {
        "claude": core.build_metrics({"limits": [
            {"kind": "session", "percent": 42},
            {"kind": "weekly_all", "percent": 18}]}),
        "codex": codex_usage.build_metrics({"rateLimits": {
            "primary": {"usedPercent": 71, "windowDurationMins": 300},
            "secondary": {"usedPercent": 31, "windowDurationMins": 10080}}}),
    }
    settled = lambda agent: 20.0

    def save(name, image):
        image.save(Path(out_dir) / (name + ".png"))

    for provider, readings in metrics.items():
        image, _ = core.render_console(readings, None, demo_stats(), agents, now,
                                       provider=provider, figure_elapsed=settled)
        save("console-" + provider, image)
        save("header-" + provider, image.crop((core.SHADOW_PAD - 20, core.SHADOW_PAD - 20,
                                              image.width - core.SHADOW_PAD + 20,
                                              core.SHADOW_PAD + core.BAR_H)))
        for side in ("right", "top"):
            image, _, _ = tucked.render(
                # The top strip runs along the sheet's width: main sessions only
                # there, the colleagues ride in the side rail beneath their parent.
                core.sort_agents(agents if side != 'top' else [a for a in agents if not a.get('sub')]),
                readings, readings[0], now, side=side,
                provider=provider, usage_open=True, figure_elapsed=settled,
                max_width=core.px(900), max_height=core.px(900))
            save(side + "-" + provider, image)
    image, _ = core.render_console(metrics["claude"], None, demo_stats(), agents[:2], now,
                                   open_id="readme-storefront", provider="claude",
                                   figure_elapsed=settled)
    save("console-open", image)
    image, _ = core.render_console(metrics["claude"], None, demo_stats(), agents[:2], now,
                                   provider="claude", figure_elapsed=settled)
    save("hero", image)
    # Keep the full name on one baseline, in the widget's live brand typeface.
    # Separate Smith/AGENTS assets made the README title look disconnected.
    from PIL import Image, ImageDraw
    for name, fill in (("wordmark", (39, 53, 43)), ("wordmark-dark", (228, 234, 223))):
        wordmark = Image.new("RGBA", (620, 110))
        ImageDraw.Draw(wordmark).text((0, 0), "Smith Agents", font=core.brand_font(26),
                                      anchor="lt", fill=fill)
        save(name, wordmark)


DEMO_SECONDS = 4.8
DEMO_FPS = 12.5


def render_demo(out_dir):
    """The widget in motion: portraits animate at the pace their state sets,
    the way the running widget draws them. Saved as an animated WebP."""
    from smith_agents import runtime
    runtime.backend().display_scale = lambda: (96, 1.0)
    from smith_agents import core, portraits
    from smith_agents.sample_data import demo_stats

    now = time.time()
    # Main sessions only: short enough to sit near the top of the README.
    agents = [agent for agent in sessions(now) if not agent.get("sub")]
    metrics = core.build_metrics({"limits": [{"kind": "session", "percent": 42},
                                             {"kind": "weekly_all", "percent": 18}]})
    frames = []
    for index in range(round(DEMO_SECONDS * DEMO_FPS)):
        t = index / DEMO_FPS
        motion = lambda agent, t=t: 20.0 + portraits.rate(agent.get("state")) * t
        image, _ = core.render_console(metrics, None, demo_stats(), agents, now + t,
                                       provider="claude", figure_elapsed=motion)
        frames.append(image)
    frames[0].save(Path(out_dir) / "demo.webp", save_all=True, append_images=frames[1:],
                   duration=round(1000 / DEMO_FPS), loop=0, quality=82, method=6)


def render_theme(theme, zoom, out_dir, worker="--worker"):
    settings = Path(out_dir) / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "config.json").write_text(json.dumps({"theme": theme, "zoom": zoom}),
                                         encoding="utf-8")
    claude_dir = Path(out_dir) / "claude"
    claude_dir.mkdir(exist_ok=True)
    env = dict(os.environ, SMITH_AGENTS_CONFIG_DIR=str(settings),
               CLAUDE_CONFIG_DIR=str(claude_dir), PYTHONPATH=str(ROOT))
    command = [sys.executable, __file__, worker, str(out_dir)]
    if worker == "--worker":
        command[2:] = [worker, theme, str(zoom), str(out_dir)]
    subprocess.run(command, check=True, env=env, cwd=ROOT)


# Two pixels per displayed README pixel. UI is rendered natively at each zoom.
WIDTH = 1600
# GitHub shows the README on a light or a dark page; each sheet is rendered
# for both so neither scheme gets a glaring block of the other.
PALETTES = {
    "light": {"paper": "#eeeee7", "ink": "#27352b", "muted": "#667368",
              "line": "#d2d8cc", "frame": "#e3e6dd", "orange": "#b85739", "blue": "#466de0"},
    "dark": {"paper": "#0d110e", "ink": "#e4eadf", "muted": "#8f9c90",
             "line": "#26302a", "frame": "#141a15", "orange": "#e0704f", "blue": "#7f9cf0"},
}
PAPER = INK = MUTED = LINE = FRAME = ORANGE = BLUE = None
WORDMARK = "wordmark"


def use_palette(name):
    global PAPER, INK, MUTED, LINE, FRAME, ORANGE, BLUE
    colours = PALETTES[name]
    PAPER, INK, MUTED, LINE = colours["paper"], colours["ink"], colours["muted"], colours["line"]
    FRAME, ORANGE, BLUE = colours["frame"], colours["orange"], colours["blue"]


def font(size, bold=False):
    from PIL import ImageFont
    result = ImageFont.truetype(str(ROOT / "smith_agents/fonts/SpaceGrotesk.ttf"), size)
    result.set_variation_by_name("Bold" if bold else "Regular")
    return result


def load(parts, name):
    from PIL import Image
    return Image.open(parts / (name + ".png")).convert("RGBA")


def sheet(height, number, title, subtitle):
    from PIL import Image, ImageDraw
    canvas = Image.new("RGBA", (WIDTH, height), PAPER)
    pen = ImageDraw.Draw(canvas)
    pen.text((72, 48), number + "  /  SMITH AGENTS", font=font(22, True), fill=MUTED)
    pen.text((72, 96), title, font=font(52, True), fill=INK)
    pen.text((72, 170), subtitle, font=font(27), fill=MUTED)
    pen.line((72, 228, WIDTH - 72, 228), fill=LINE, width=2)
    return canvas, pen


def note(pen, x, y, number, heading, lines):
    pen.text((x, y), number, font=font(24, True), fill=ORANGE)
    pen.text((x, y + 42), heading, font=font(38, True), fill=INK)
    for i, line in enumerate(lines):
        pen.text((x, y + 102 + i * 39), line, font=font(27), fill=MUTED)


def compose_hero(parts):
    from PIL import Image, ImageDraw
    console = load(parts, "hero")
    height = max(1050, console.height + 96)
    canvas = Image.new("RGBA", (WIDTH, height), PAPER)
    pen = ImageDraw.Draw(canvas)
    canvas.alpha_composite(load(parts, WORDMARK), (76, 96))
    y = max(310, (height - 420) // 2)
    pen.text((72, y), "Your agents.", font=font(88, True), fill=INK)
    pen.text((72, y + 104), "In view.", font=font(88, True), fill=INK)
    for i, line in enumerate(("See the work. Watch your usage.", "Keep your screen.")):
        pen.text((76, y + 250 + i * 46), line, font=font(31), fill=MUTED)
    pen.line((76, height - 172, 684, height - 172), fill=LINE, width=2)
    pen.text((76, height - 143), "CLAUDE CODE  +  CODEX  +  HERMES AGENT", font=font(25, True), fill=INK)
    pen.text((76, height - 101), "Windows & macOS", font=font(25), fill=MUTED)
    canvas.alpha_composite(console, (WIDTH - console.width - 48, (height - console.height) // 2))
    return canvas


def compose_social(parts):
    """The 1280x640 card GitHub shows when the repository link is shared:
    the promise on the left, the dashboard and the right-edge strip beside it."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    path = parts / "dashboard.png"
    if not path.exists():
        return None
    width, height = 1280, 640
    canvas = Image.new("RGBA", (width, height), PAPER)
    pen = ImageDraw.Draw(canvas)
    wordmark = load(parts, WORDMARK)
    wordmark = wordmark.crop(wordmark.getbbox())
    wordmark = wordmark.resize((250, round(wordmark.height * 250 / wordmark.width)), Image.LANCZOS)
    canvas.alpha_composite(wordmark, (60, 76))
    pen.text((56, 176), "Your agents.", font=font(60, True), fill=INK)
    pen.text((56, 248), "In view.", font=font(60, True), fill=INK)
    for i, line in enumerate(("Sessions tucked at the edge.", "Usage in one dashboard.")):
        pen.text((60, 358 + i * 38), line, font=font(25), fill=MUTED)
    pen.text((60, 530), "CLAUDE CODE + CODEX + HERMES", font=font(19, True), fill="#3ddc6a")
    pen.text((60, 560), "WINDOWS & MACOS", font=font(19, True), fill=MUTED)

    def drop_shadow(box, radius, blur, alpha):
        shade = Image.new("RGBA", canvas.size)
        ImageDraw.Draw(shade).rounded_rectangle(box, radius, fill=(0, 0, 0, alpha))
        canvas.alpha_composite(shade.filter(ImageFilter.GaussianBlur(blur)))

    # The dashboard's top: header, totals, and the build-history calendar.
    shot = Image.open(path).convert("RGBA")
    shot = shot.crop((0, 0, shot.width, shot.width * 1500 // 2880))
    shot = shot.resize((720, round(shot.height * 720 / shot.width)), Image.LANCZOS)
    corners = Image.new("L", shot.size)
    ImageDraw.Draw(corners).rounded_rectangle((0, 0, shot.width - 1, shot.height - 1), 14, fill=255)
    shot.putalpha(ImageChops.multiply(shot.getchannel("A"), corners))
    x, y = 450, (height - shot.height) // 2 + 10
    drop_shadow((x, y + 8, x + shot.width, y + shot.height + 8), 14, 24, 170)
    canvas.alpha_composite(shot, (x, y))
    # The strip tucked to the right edge, overlapping the dashboard as on a desktop.
    strip = load(parts, "right-claude")
    strip = strip.crop(strip.getchannel("A").point(lambda v: 255 if v > 200 else 0).getbbox())
    strip = strip.resize((round(strip.width * 560 / strip.height), 560), Image.LANCZOS)
    x, y = width - strip.width - 44, 40
    drop_shadow((x - 6, y + 6, x + strip.width, y + strip.height + 6), 8, 20, 220)
    canvas.alpha_composite(strip, (x, y))
    return canvas


def compose_work(parts):
    console = load(parts, "console-open")
    canvas, pen = sheet(console.height + 304, "01", "A clear view of every session.",
                        "Figures, context, activity, and a direct path back to work.")
    canvas.alpha_composite(console, (40, 260))
    note(pen, 860, 550, "01", "Recognize the session", [
        "A figure, project name, and provider.", "Context sits just below the identity."])
    note(pen, 860, 1090, "02", "Read the latest activity", [
        "Open a card for its current command", "or latest reply, then Go to session."])
    note(pen, 860, 1470, "03", "Keep the details folded", [
        "Read more reveals extra controls", "and session metadata when needed."])
    return canvas


def compose_usage(parts):
    canvas, pen = sheet(880, "02", "Usage, in a field of signals.",
                        "Each lit cell is 1% used. Five hours and a week, side by side.")
    for x, provider, colour in ((48, "codex", BLUE), (824, "claude", ORANGE)):
        pen.text((x + 24, 274), provider.capitalize(), font=font(34, True), fill=colour)
        canvas.alpha_composite(load(parts, "header-" + provider), (x, 346))
        pen.text((x + 24, 644), "5h + week, always in the header", font=font(28, True), fill=INK)
        pen.text((x + 24, 694), "Switch providers with the glowing logos.", font=font(25), fill=MUTED)
    pen.line((72, 778, WIDTH - 72, 778), fill=LINE, width=2)
    pen.text((72, 806), "Tucked? Click a provider logo to unfold its attached usage drawer.",
             font=font(26), fill=MUTED)
    return canvas


def compose_tuck(parts):
    side = load(parts, "right-codex")
    top = load(parts, "top-claude")
    # The rail sits 350 down; leave it 40 inside the frame, which ends 60 up.
    height = max(1220, side.height + 450)
    canvas, pen = sheet(height, "03", "Small at every edge.",
                        "Dock to the screen. Keep sessions and usage within reach.")
    # A desktop frame, not an editor window: rails belong to the monitor edges.
    pen.rounded_rectangle((60, 278, WIDTH - 60, height - 60), radius=28,
                          fill=FRAME, outline=LINE, width=2)
    canvas.alpha_composite(top, (90, 290))
    canvas.alpha_composite(side, (WIDTH - side.width - 64, 350))
    note(pen, 118, 760, "01", "Top or bottom", [
        "A horizontal row of figures.", "The drawer stays attached to the strip."])
    note(pen, 118, 1030, "02", "Left or right", [
        "A slim vertical stack with icon-only branding.", "Click Smith to return to the full widget."])
    return canvas


def compose_themes(themes):
    consoles = [load(parts, "console-claude") for parts in themes]
    canvas, pen = sheet(max(im.height for im in consoles) + 410, "04", "Choose your atmosphere.",
                        "One layout. Three themes, available from the tray menu.")
    labels = {"matrix": "The Matrix", "claude": "Claude", "eink": "E-ink"}
    for x, console, label in zip((28, 548, 1068), consoles, (labels[t] for t in THEMES)):
        pen.text((x + 25, 280), label, font=font(32, True), fill=INK)
        canvas.alpha_composite(console, (x, 350))
    return canvas


# The dashboard sample: three projects over September 2026, with account
# readings saved from 17 September, so the calendar shows days before and
# after recording began. Fixed dates and a fixed seed keep the image stable.
DASHBOARD_TODAY = "2026-09-24"
DASHBOARD_PROJECTS = [
    ("storefront", [("Add coupon support to checkout", "Claude Code"),
                    ("Checkout test suite", "Codex"),
                    ("Refactor payment adapters", "Claude Code"),
                    ("Release notes for coupons", "Codex")]),
    ("api-service", [("Rename the orders table columns", "Claude Code"),
                     ("Migration dry run", "Codex")]),
    ("design-system", [("Tighten the spacing scale", "Claude Code")]),
]
DASHBOARD_ACTIONS = ("Command calls", "File reads", "File-edit calls", "File reads", "Command calls")


def sample_history():
    """Snapshots in the shape history.snapshot returns, and saved readings."""
    import random
    from datetime import datetime, timedelta, timezone
    from smith_agents.dashboard import allowance, history
    rng = random.Random(7)
    days = [9, 10, 11, 14, 15, 16, 17, 18, 21, 22, 23, 24]
    snapshots, all_events = {}, []
    for p_index, (name, titles) in enumerate(DASHBOARD_PROJECTS):
        path = "/sample/" + name
        sessions, events, actions = [], [], []
        for s_index, (title, provider) in enumerate(titles):
            for day in days:
                if rng.random() > (0.75 if p_index == 0 else 0.35):
                    continue
                identity = "%s-%d-%d" % (name, s_index, day)
                start = datetime(2026, 9, day, 8 + rng.randrange(0, 9), rng.randrange(60),
                                 tzinfo=timezone.utc)
                sessions.append({"id": identity, "parent": None, "model": "sample",
                                 "provider": provider, "title": title, "nickname": None,
                                 "started": start.isoformat(timespec="seconds"),
                                 "helper": False, "method": "response receipts"})
                for step in range(rng.randrange(12, 40)):
                    at = (start + timedelta(minutes=step * rng.randrange(2, 6))).isoformat(timespec="seconds")
                    events.append({"id": "%s:%d" % (identity, step), "session": identity, "at": at,
                                   "tokens": [rng.randrange(1500, 9000), rng.randrange(30000, 140000),
                                              rng.randrange(200, 1600)]})
                    if step % 3 == 0:
                        actions.append({"at": at, "session": identity,
                                        "label": DASHBOARD_ACTIONS[step % len(DASHBOARD_ACTIONS)]})
        events.sort(key=lambda e: e["at"])
        snapshots[path] = {
            "project": name, "id": history.project_id(path), "timezone": "UTC",
            "generated": DASHBOARD_TODAY + "T20:00:00+00:00",
            "sources": sorted({s["provider"] for s in sessions}), "today": DASHBOARD_TODAY,
            "sessions": sessions, "events": events, "actions": actions, "commits": [],
            "coverage": {"sourceFiles": {}}, "first": events[0]["at"], "last": events[-1]["at"],
            "version": name}
        providers = {s["id"]: allowance.ACCOUNTS.get(s["provider"]) for s in sessions}
        all_events += [dict(e, provider=providers[e["session"]]) for e in events]
    # Readings every five minutes from 17 September, day and night,, rising with local use
    # plus a little from elsewhere, in whole percents as Claude reports them.
    readings = []
    for account, base, scale in (("Claude", 12.0, 1 / 380000), ("Codex", 20.0, 1 / 420000)):
        mine = sorted((datetime.fromisoformat(e["at"]).timestamp(), allowance.weight(e))
                      for e in all_events if e["provider"] == account)
        t = datetime(2026, 9, 17, 7, tzinfo=timezone.utc).timestamp()
        end = datetime(2026, 9, 24, 21, tzinfo=timezone.utc).timestamp()
        used, i = base, 0
        while t < end:
            while i < len(mine) and mine[i][0] <= t:
                used += mine[i][1] * scale
                i += 1
            readings.append({"at": t, "provider": account, "window": "week",
                             "pct": float(int(min(100, used))), "reset": "2026-09-25T16:00:00+00:00"})
            t += 300
    return snapshots, readings


def render_dashboard(out_dir):
    """The real dashboard page, served with sample history, in a headless browser."""
    from unittest.mock import patch
    from smith_agents import allowance_log
    from smith_agents.dashboard import history
    from smith_agents.dashboard.service import DashboardService
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Skipping the dashboard image: install Playwright (pip install playwright; "
              "playwright install chromium).")
        return
    snapshots, readings = sample_history()
    projects = [{"id": data["id"], "name": data["project"], "path": path,
                 "sessions": len(data["sessions"]), "sources": {}} for path, data in snapshots.items()]
    service = DashboardService()
    with patch.object(history, "discover_projects", return_value=projects), \
         patch.object(history, "snapshot", side_effect=lambda path: dict(snapshots[path])), \
         patch.object(allowance_log, "read", return_value=readings):
        url = service.url(projects[0]["id"])
        with sync_playwright() as browser:
            chrome = browser.chromium.launch()
            page = chrome.new_page(viewport={"width": 1440, "height": 1040}, device_scale_factor=2)
            page.goto(url)
            page.wait_for_selector(".cal-cell .cal-allow")
            page.wait_for_timeout(800)
            page.screenshot(path=str(Path(out_dir) / "dashboard.png"))
            chrome.close()
    service.stop()


def compose_dashboard(parts):
    from PIL import Image
    path = parts / "dashboard.png"
    if not path.exists():
        return None
    shot = Image.open(path).convert("RGBA")
    width = WIDTH - 144
    shot = shot.resize((width, round(shot.height * width / shot.width)), Image.LANCZOS)
    canvas, pen = sheet(shot.height + 390, "05", "See where your usage went.",
                        "Each day and session, with its share of the weekly limit.")
    frame = Image.new("RGBA", (shot.width + 4, shot.height + 4), LINE)
    canvas.alpha_composite(frame, (70, 270))
    canvas.alpha_composite(shot, (72, 272))
    y = shot.height + 306
    pen.text((72, y), "wk +X%  is the share of the weekly limit;  ~  marks a split between sessions "
             "that overlapped.", font=font(25), fill=MUTED)
    pen.text((72, y + 40), "Recorded from the day the widget starts saving readings. "
             "Earlier days stay unrecorded.", font=font(25), fill=MUTED)
    return canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worker", nargs=3, metavar=("THEME", "ZOOM", "DIR"), help=argparse.SUPPRESS)
    parser.add_argument("--dashboard-worker", metavar="DIR", help=argparse.SUPPRESS)
    parser.add_argument("--demo-worker", metavar="DIR", help=argparse.SUPPRESS)
    parser.add_argument("--out", default=str(OUT), help="Output folder (default: docs/images)")
    args = parser.parse_args()
    if args.demo_worker:
        render_demo(args.demo_worker)
        return
    if args.dashboard_worker:
        render_dashboard(args.dashboard_worker)
        return
    if args.worker:
        theme, zoom, out_dir = args.worker
        render_parts(theme, float(zoom), out_dir)
        return
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        parts = scratch / "main"
        render_theme("matrix", 2.5, parts)
        subprocess.run([sys.executable, __file__, "--dashboard-worker", str(parts)], check=True,
                       env=dict(os.environ, SMITH_AGENTS_CONFIG_DIR=str(parts / "settings"),
                                PYTHONPATH=str(ROOT), TZ="UTC"), cwd=ROOT)
        demo = scratch / "demo"
        render_theme("matrix", 1.6, demo, worker="--demo-worker")
        (out / "github-matrix-demo.webp").write_bytes((demo / "demo.webp").read_bytes())
        print(out / "github-matrix-demo.webp")
        themes = []
        for theme in THEMES:
            theme_parts = scratch / theme
            render_theme(theme, 1.45, theme_parts)
            themes.append(theme_parts)
        global WORDMARK
        for scheme in ("light", "dark"):
            use_palette(scheme)
            WORDMARK = "wordmark" if scheme == "light" else "wordmark-dark"
            suffix = "" if scheme == "light" else "-dark"
            images = {
                "github-matrix-hero%s.png" % suffix: compose_hero(parts),
                "github-matrix-work%s.png" % suffix: compose_work(parts),
                "github-matrix-usage%s.png" % suffix: compose_usage(parts),
                "github-matrix-tuck%s.png" % suffix: compose_tuck(parts),
                "github-matrix-themes%s.png" % suffix: compose_themes(themes),
            }
            dashboard = compose_dashboard(parts)
            if dashboard is not None:
                images["github-matrix-dashboard%s.png" % suffix] = dashboard
            social = compose_social(parts) if scheme == "dark" else None
            if social is not None:
                images["github-social-preview.png"] = social
            for name, image in images.items():
                image.convert("RGB").save(out / name, optimize=True)
                print("%s  %dx%d" % (out / name, *image.size))


if __name__ == "__main__":
    main()

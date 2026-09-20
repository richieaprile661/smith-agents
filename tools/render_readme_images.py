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
SESSIONS = [
    {"id": "readme-storefront", "name": "storefront", "provider": "codex",
     "model": "gpt-5.3-codex", "entrypoint": "codex-vscode", "state": "working", "idle": 4,
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
    wordmark = Image.new("RGBA", (620, 110))
    ImageDraw.Draw(wordmark).text((0, 0), "Smith Agents", font=core.brand_font(26),
                                  anchor="lt", fill=(39, 53, 43))
    save("wordmark", wordmark)


def render_theme(theme, zoom, out_dir):
    settings = Path(out_dir) / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "config.json").write_text(json.dumps({"theme": theme, "zoom": zoom}),
                                         encoding="utf-8")
    claude_dir = Path(out_dir) / "claude"
    claude_dir.mkdir(exist_ok=True)
    env = dict(os.environ, SMITH_AGENTS_CONFIG_DIR=str(settings),
               CLAUDE_CONFIG_DIR=str(claude_dir), PYTHONPATH=str(ROOT))
    subprocess.run([sys.executable, __file__, "--worker", theme, str(zoom), str(out_dir)],
                   check=True, env=env, cwd=ROOT)


# Two pixels per displayed README pixel. UI is rendered natively at each zoom.
WIDTH = 1600
PAPER = "#eeeee7"
INK = "#27352b"
MUTED = "#667368"
LINE = "#d2d8cc"
ORANGE = "#b85739"
BLUE = "#466de0"


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
    canvas.alpha_composite(load(parts, "wordmark"), (76, 96))
    y = max(310, (height - 420) // 2)
    pen.text((72, y), "Your agents.", font=font(88, True), fill=INK)
    pen.text((72, y + 104), "In view.", font=font(88, True), fill=INK)
    for i, line in enumerate(("See the work. Watch your usage.", "Keep your screen.")):
        pen.text((76, y + 250 + i * 46), line, font=font(31), fill=MUTED)
    pen.line((76, height - 172, 684, height - 172), fill=LINE, width=2)
    pen.text((76, height - 143), "CLAUDE CODE  +  CODEX", font=font(25, True), fill=INK)
    pen.text((76, height - 101), "Windows & macOS", font=font(25), fill=MUTED)
    canvas.alpha_composite(console, (WIDTH - console.width - 48, (height - console.height) // 2))
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
    height = max(1220, side.height + 330)
    canvas, pen = sheet(height, "03", "Small at every edge.",
                        "Dock to the screen. Keep sessions and usage within reach.")
    # A desktop frame, not an editor window: rails belong to the monitor edges.
    pen.rounded_rectangle((60, 278, WIDTH - 60, height - 60), radius=28,
                          fill="#e3e6dd", outline=LINE, width=2)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worker", nargs=3, metavar=("THEME", "ZOOM", "DIR"), help=argparse.SUPPRESS)
    parser.add_argument("--out", default=str(OUT), help="Output folder (default: docs/images)")
    args = parser.parse_args()
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
        themes = []
        for theme in THEMES:
            theme_parts = scratch / theme
            render_theme(theme, 1.45, theme_parts)
            themes.append(theme_parts)
        images = {
            "github-matrix-hero.png": compose_hero(parts),
            "github-matrix-work.png": compose_work(parts),
            "github-matrix-usage.png": compose_usage(parts),
            "github-matrix-tuck.png": compose_tuck(parts),
            "github-matrix-themes.png": compose_themes(themes),
        }
        for name, image in images.items():
            image.convert("RGB").save(out / name, optimize=True)
            print("%s  %dx%d" % (out / name, *image.size))


if __name__ == "__main__":
    main()

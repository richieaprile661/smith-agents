"""Render the README images from the widget's own renderer and sample sessions.

    py tools/render_readme_images.py

Writes docs/images/github-*.png. Render on Windows: the Claude theme draws
with Segoe UI, so a Mac render would not match the Windows screenshots.
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
THEMES = ("claude", "matrix", "eink")

# One set of sessions for every image, so counts and names always agree.
# Idle times are seconds since the session's last transcript write.
SESSIONS = [
    {"id": "readme-storefront", "name": "storefront", "provider": "codex",
     "model": "gpt-5.3-codex", "entrypoint": "codex-vscode", "state": "working", "idle": 4,
     "tail": [("cmd", "Bash pytest -q tests/checkout")],
     "last_request": "Add coupon support to checkout and cover it with tests.",
     "latest_message": "Coupons apply at checkout. Running the test suite now.",
     "context_tokens": 64200, "context_capacity": 200000,
     "_window_state": "background", "can_terminate": False},
    # In-widget Allow/Deny exists only for Claude Code in VS Code with the
    # optional launcher, so the session asking for approval is a VS Code one.
    {"id": "readme-api", "name": "api-service", "provider": "claude",
     "model": "claude-opus-5", "entrypoint": "claude-vscode", "state": "needs", "idle": 95,
     "tail": [("cmd", "Bash npm run migrate")],
     "permissions": [{"actionable": True, "request": {
         "tool_name": "Bash", "input": {"command": "npm run migrate"},
         "description": "Run the database migration"}}],
     "last_request": "Rename the orders table columns and migrate the data.",
     "latest_message": "The migration is ready. I need approval to run it.",
     "context_tokens": 412000, "context_capacity": 1000000,
     "_window_state": "background"},
    {"id": "readme-design", "name": "design-system", "provider": "claude",
     "model": "claude-sonnet-5", "entrypoint": "cli", "state": "done", "idle": 42,
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
    """Runs in a child process: the theme and zoom are fixed at import time."""
    from smith_agents import runtime
    backend = runtime.backend()
    backend.display_scale = lambda: (96, 1.0)  # the same pixels on any monitor
    from smith_agents import core, figure_actions, tucked
    from smith_agents.sample_data import demo_payload, demo_stats

    now = time.time()
    agents = sessions(now)
    metrics = core.build_metrics(demo_payload())
    front = metrics[0] if metrics else None
    settled = lambda agent: 20.0  # past the entrance action, in the resting pose
    header = figure_actions.frame_at(20.0)
    anchors = {}

    def save(name, image):
        image.save(Path(out_dir) / (name + ".png"))

    image, _boxes = core.render_console(metrics, None, demo_stats(), agents, now, "agents",
                                        figure_elapsed=settled, header_frame=header,
                                        provider="claude")
    save("console", image)

    # Scrolled the way a short console would be: the open session's details
    # fill the view and the last row sits below it.
    rows = core.sort_agents(agents)
    visible = core.BAR_H + core.TAB_H + core.FOOT_H + sum(
        core.agent_row_height(agent) for agent in rows if agent["id"] != "readme-design"
    ) + core.agent_drawer_height(next(a for a in rows if a["id"] == "readme-storefront"))
    image, _boxes = core.render_console(metrics, None, demo_stats(), agents, now, "agents",
                                        open_id="readme-storefront", figure_elapsed=settled,
                                        header_frame=header, provider="claude",
                                        max_height=visible)
    save("console-open", image)

    # What the labelled details image outlines, as (top, bottom) in image
    # pixels, taken from the same layout functions the renderer draws with.
    pad, y = core.SHADOW_PAD, core.BAR_H + core.TAB_H
    small = core.panel_line_height(core.FONT("book", 10))
    body = core.panel_line_height(core.FONT("book", 11))
    spans = {}
    for agent in rows:
        _titles, _x, context_y, track_y, state_y, activity_y, height = core.agent_row_layout(agent)
        spans[agent["id"]] = {"state": (pad + y + state_y, pad + y + state_y + small),
                              "activity": (pad + y + activity_y, pad + y + activity_y + body)}
        y += height
        if agent["id"] == "readme-storefront":
            blocks, foot, drawer_h = core.agent_drawer_layout(agent)
            for title, _lines, _kind, top, block_h in blocks:
                spans[agent["id"]][title] = (pad + y + top, pad + y + top + block_h)
            spans[agent["id"]]["open"] = (pad + y + foot + core.px(4), pad + y + foot + core.px(24))
            y += drawer_h
    anchors["spans"] = spans
    anchors["pad_x"] = core.PAD_X

    rows = tucked.AgentOrder().sync(rows)
    image, _boxes, layout = tucked.render(rows, metrics, front, now, side="top",
                                          max_width=core.px(920), max_height=core.px(760),
                                          selected="readme-api", details=False,
                                          figure_elapsed=settled, provider="claude")
    save("tuck-top", image)
    anchors["tuck-top"] = {"rail": layout.rail, "panel": layout.panel}

    anchors["shadow_pad"] = core.SHADOW_PAD
    anchors["console_w"] = core.CONSOLE_W
    save("wordmark", core.smith_wordmark(320, 120))
    (Path(out_dir) / "anchors.json").write_text(json.dumps(anchors), encoding="utf-8")


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


# --- composition ------------------------------------------------------------
# GitHub shows README images about 830-1000px wide. Canvases are 1760px, about
# twice that density, and the widget renders at zoom 2.5 so it appears a little
# larger than its real on-screen size and its text stays readable.
WIDTH = 1760
ZOOM = 2.5
THEME_ZOOM = 1.6  # three consoles have to share one row
PAPER = (22, 19, 15)
FG = (241, 236, 227)
MUTED = (163, 152, 138)
DIM = (107, 99, 87)
ACCENT = (217, 119, 87)
STATES = {"needs": (230, 181, 63), "working": (217, 119, 87), "done": (140, 189, 146)}


def mono(size, weight="Regular"):
    from PIL import ImageFont
    font = ImageFont.truetype(str(ROOT / "smith_agents" / "fonts" / "FiraCode.ttf"), size)
    for name in font.get_variation_names():
        name = name.decode() if isinstance(name, bytes) else name
        if name.replace(" ", "").lower() == weight.lower():
            font.set_variation_by_name(name)
            break
    return font


def tracked(pen, xy, text, font, fill, tracking):
    x, y = xy
    for char in text:
        pen.text((x, y), char, font=font, fill=fill)
        x += pen.textlength(char, font=font) + tracking


def tracked_width(pen, text, font, tracking):
    return sum(pen.textlength(char, font=font) for char in text) + tracking * (len(text) - 1)


def load(parts, name):
    from PIL import Image
    return Image.open(parts / (name + ".png")).convert("RGBA")


def compose_hero(parts, anchors):
    from PIL import Image, ImageDraw
    console = load(parts, "console")
    pad = anchors["shadow_pad"]
    canvas = Image.new("RGBA", (WIDTH, console.height), PAPER)
    canvas.alpha_composite(console, (WIDTH - console.width - 40, 0))
    pen = ImageDraw.Draw(canvas)

    wordmark = load(parts, "wordmark")
    headline, sub, small = mono(88, "SemiBold"), mono(34), mono(30)
    left, line = 96, 108
    block = wordmark.height + 72 + 2 * line + 30 + 2 * 50 + 50 + 36
    top = (console.height - block) // 2
    canvas.alpha_composite(wordmark, (left - 8, top))
    caps = mono(28, "Medium")
    word = "AGENTS"
    tracked(pen, (left + (wordmark.width - 8 - tracked_width(pen, word, caps, 12)) // 2,
                  top + wordmark.height + 4), word, caps, MUTED, 12)
    y = top + wordmark.height + 72
    for text in ("Your agents.", "At a glance."):
        pen.text((left, y), text, font=headline, fill=FG)
        y += line
    y += 30
    for text in ("Claude Code + Codex", "Windows + macOS"):
        pen.text((left, y), text, font=sub, fill=MUTED)
        y += 50
    y += 50
    x = left
    for state, label in (("needs", "Needs you"), ("working", "Working"), ("done", "Ready")):
        pen.ellipse((x, y + 9, x + 18, y + 27), fill=STATES[state])
        pen.text((x + 30, y), label, font=small, fill=FG)
        x += 30 + pen.textlength(label, font=small) + 48
    return canvas.convert("RGB")


def compose_work(parts, anchors):
    """The open details for one session, with what each reading tells you."""
    from PIL import Image, ImageDraw
    console = load(parts, "console-open")
    pad, width, pad_x = anchors["shadow_pad"], anchors["console_w"], anchors["pad_x"]
    spans = anchors["spans"]
    canvas = Image.new("RGBA", (WIDTH, console.height), PAPER)
    cx = 24
    canvas.alpha_composite(console, (cx, 0))
    pen = ImageDraw.Draw(canvas)
    store = spans["readme-storefront"]
    notes = [
        (spans["readme-api"]["state"], "Needs you", "An agent waiting on you goes to the top."),
        (store["activity"], "What it's running", "The actual command, as it works."),
        (store["Last request"], "What you asked", "Your last prompt, so you know the job."),
        (store["Latest message"], "What it said last", "Its latest reply, without the chat."),
        (store["open"], "Jump to it", "Open its terminal, chat, or window."),
    ]
    title, body = mono(36, "SemiBold"), mono(26)
    left, right = cx + pad + pad_x - 10, cx + pad + width - pad_x + 10
    text_x = right + 150
    last = -1000
    for (top, bottom), heading, detail in notes:
        top, bottom = top - 6, bottom + 4
        mid = (top + bottom) // 2
        label_y = max(mid, last + 150)  # keep neighbouring notes apart
        last = label_y
        pen.rounded_rectangle((left, top, right, bottom), radius=10, outline=ACCENT, width=3)
        pen.line(((right, mid), (right + 60, mid), (text_x - 28, label_y)), fill=ACCENT, width=3)
        pen.ellipse((text_x - 36, label_y - 8, text_x - 20, label_y + 8), fill=ACCENT)
        pen.text((text_x, label_y - 46), heading, font=title, fill=FG)
        pen.text((text_x, label_y + 6), detail, font=body, fill=MUTED)
    return canvas.convert("RGB")


def compose_tuck(parts, anchors):
    """A desktop with the strip tucked flush against the screen's top edge."""
    from PIL import Image, ImageDraw
    height = 1100
    canvas = Image.new("RGBA", (WIDTH, height), PAPER)
    screen = (64, 64, WIDTH - 64, height - 56)
    sw, sh = screen[2] - screen[0], screen[3] - screen[1]
    desk = Image.new("RGBA", (sw, sh), (30, 26, 22))
    pen = ImageDraw.Draw(desk)
    # A code editor stands in for whatever you are working on.
    win = (48, 132, sw - 48, sh - 40)
    pen.rounded_rectangle(win, radius=18, fill=(27, 24, 20), outline=(46, 41, 37), width=2)
    pen.line((win[0], win[1] + 52, win[2], win[1] + 52), fill=(46, 41, 37), width=2)
    for i, colour in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
        pen.ellipse((win[0] + 24 + i * 30, win[1] + 18, win[0] + 40 + i * 30, win[1] + 34),
                    fill=colour + (150,))
    pen.text((win[0] + 150, win[1] + 12), "checkout.py", font=mono(24), fill=MUTED)
    side = win[0] + 300
    pen.rectangle((win[0] + 2, win[1] + 54, side, win[3] - 2), fill=(23, 20, 17))
    for i, w in enumerate((150, 120, 170, 110, 140, 160, 100, 130, 150, 120, 90, 140)):
        indent = 0 if i in (0, 5, 9) else 28
        pen.rounded_rectangle((win[0] + 32 + indent, win[1] + 90 + i * 44,
                               win[0] + 32 + indent + w, win[1] + 104 + i * 44),
                              radius=7, fill=(52, 47, 41) if i != 2 else (84, 75, 65))
    code = [(0, [(90, DIM), (240, MUTED)]), (0, []), (0, [(110, ACCENT), (200, FG), (70, MUTED)]),
            (1, [(160, MUTED), (120, STATES["done"])]), (1, [(90, ACCENT), (260, FG)]),
            (2, [(200, MUTED), (140, STATES["done"])]), (2, [(120, FG), (90, MUTED)]),
            (1, [(80, ACCENT), (180, FG)]), (0, []), (0, [(110, ACCENT), (240, FG), (60, MUTED)]),
            (1, [(300, DIM)]), (1, [(150, MUTED), (190, FG)]), (2, [(90, ACCENT), (220, FG)]),
            (2, [(170, MUTED), (120, STATES["done"]), (80, MUTED)]), (1, [(90, ACCENT), (150, FG)]),
            (0, [])]
    for i, (depth, spans) in enumerate(code):
        y, x = win[1] + 96 + i * 44, side + 48 + depth * 44
        pen.text((side + 12, y - 8), str(i + 1).rjust(2), font=mono(22), fill=(70, 63, 55))
        for span, colour in spans:
            # Mixed into the editor background: the code is scenery, kept quiet.
            fill = tuple(round(a * 0.3 + b * 0.7) for a, b in zip(colour, (27, 24, 20)))
            pen.rounded_rectangle((x, y, x + span, y + 14), radius=7, fill=fill)
            x += span + 18
    tuck = load(parts, "tuck-top")
    rail = anchors["tuck-top"]["rail"]
    # The app places the rail's top edge on the screen's top edge.
    desk.alpha_composite(tuck, (sw - 150 - (rail[2] - rail[0]) - rail[0], -rail[1]))
    mask = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, sw - 1, sh - 1), radius=26, fill=255)
    canvas.paste(desk, screen[:2], mask)
    ImageDraw.Draw(canvas).rounded_rectangle((screen[0] - 3, screen[1] - 3, screen[2] + 2, screen[3] + 2),
                                             radius=29, outline=(61, 52, 43), width=3)
    return canvas.convert("RGB")


def compose_themes(themes):
    from PIL import Image, ImageDraw
    consoles = [load(parts, "console") for parts, _anchors in themes]
    pad = themes[0][1]["shadow_pad"]
    content = consoles[0].width - 2 * pad
    margin = 72
    gap = (WIDTH - 2 * margin - 3 * content) // 2
    label_h = 92
    height = label_h + max(c.height for c in consoles) - 2 * pad + 56
    canvas = Image.new("RGBA", (WIDTH, height), PAPER)
    pen = ImageDraw.Draw(canvas)
    font = mono(38, "SemiBold")
    for i, (console, name) in enumerate(zip(consoles, ("Claude", "The Matrix", "E-ink"))):
        x = margin + i * (content + gap)
        pen.text((x, 26), name, font=font, fill=FG)
        canvas.alpha_composite(console, (x - pad, label_h - pad))
    return canvas.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worker", nargs=3, metavar=("THEME", "ZOOM", "DIR"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--out", default=str(OUT), help="Output folder (default: docs/images)")
    args = parser.parse_args()
    if args.worker:
        theme, zoom, out_dir = args.worker
        render_parts(theme, float(zoom), out_dir)
        return
    if sys.platform != "win32":
        print("Note: render on Windows to match the published Claude theme font.")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        main_parts = scratch / "claude"
        render_theme("claude", ZOOM, main_parts)
        anchors = json.loads((main_parts / "anchors.json").read_text(encoding="utf-8"))
        themes = []
        for theme in THEMES:
            parts = scratch / ("theme-" + theme)
            render_theme(theme, THEME_ZOOM, parts)
            themes.append((parts, json.loads((parts / "anchors.json").read_text(encoding="utf-8"))))
        images = {
            "github-hero.png": compose_hero(main_parts, anchors),
            "github-work.png": compose_work(main_parts, anchors),
            "github-tuck.png": compose_tuck(main_parts, anchors),
            "github-themes.png": compose_themes(themes),
        }
        for name, image in images.items():
            image.save(out / name, optimize=True)
            print("%s  %dx%d" % (out / name, *image.size))


if __name__ == "__main__":
    main()

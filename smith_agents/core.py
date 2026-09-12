"""Shared Claude data, themes, Pillow rendering, and layout constants.

Native windowing, credential storage, and process operations belong to the
selected platform backend. Importing this module does not start the app.
"""
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from . import artwork, figure_actions
from .branding import APP_NAME, config_override
from .runtime import backend
from .context_bridge import read_capacity
from .permissions import pending_permissions

platform = backend()
_DPI, SCALE = platform.display_scale()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
USER_AGENT = "smith-agents/1.1 (%s)" % sys.platform
USAGE_HELP_URL = "https://support.claude.com/articles/12429409"

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"))
CREDENTIALS_PATH = os.path.join(CLAUDE_DIR, ".credentials.json")
PROJECTS_DIR = os.path.join(CLAUDE_DIR, "projects")
CONFIG_DIR = config_override() or platform.config_dir()
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
LOG_PATH = os.path.join(CONFIG_DIR, "widget.log")
CACHE_PATH = os.path.join(CONFIG_DIR, "last-usage.json")

DEFAULTS = {
    "dock": "top-right",
    "x": None,
    "y": None,
    "opacity": 1.0,
    "interval": 300,
    "visible": True,
    "margin": 14,
    "zoom": 1.0,
    "tucked": False,
    "tuck_side": "right",
    "tuck_x": None,
    "tuck_y": None,
    "theme": "claude",
    "console_open": True,
    "console_tab": "agents",
    "bar_mode": "worst",
    "reading_view": "used",
    "usage_provider": "claude",
    "demo_figures": False,
    # session ids whose closed row you have cleared away by hand
    "dismissed": [],
}


def _startup_zoom():
    """Every size below is baked at import time, so zoom has to be read before
    the app object exists. Changing it needs a restart."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            value = float(json.load(fh).get("zoom", DEFAULTS["zoom"]))
    except (OSError, ValueError, TypeError):
        value = DEFAULTS["zoom"]
    return max(0.6, min(2.5, value))


ZOOM = _startup_zoom()


def px(value):
    return int(round(value * SCALE * ZOOM))

# The console is drawn at the handoff's 320px; 1.0 is its true size and every
# step above it is an upscale, which is why they are offered rather than baked.
ZOOM_STEPS = (1.0, 1.15, 1.3, 1.5)

DOCKS = ["top-left", "top-center", "top-right",
         "bottom-left", "bottom-center", "bottom-right", "free"]

IDLE_REFRESH_MS = 1000
LIVE_REFRESH_MS = 70
ACTIVE_WINDOW_S = 90
MAX_BACKOFF_S = 900          # ceiling when the usage endpoint rate limits us


# --------------------------------------------------------------------------
# Design tokens - transcribed from design/widget-handoff-v3.html sections 1, 1b, 1c.
#
# A theme owns colour, radius, a handful of geometry overrides, the font, and
# six switches: meter texture, live indicator, scanline overlay, threshold
# style, label casing and tray shape. Everything else - the 232 x 66 and
# 232 x 36 footprints, the front-slot rule, the state machine and its 75/90
# thresholds, the copy, the 16px tray canvas, one moving part - is fixed by the
# handoff's theming contract and lives outside this table.
# --------------------------------------------------------------------------
FONT_DIR = os.path.join(SCRIPT_DIR, "fonts")

THEMES = {
    "claude": {
        "label": "Claude",
        "paper": "16130f",
        "bg_top": "26211c", "bg_bottom": "1a1613", "border": "3d342b", "track": "3b332a",
        "fg": "f1ece3", "muted": "a3988a", "dim": "6b6357",
        "ok": "8cbd92", "warn": "e6b53f", "warn_border": "7a5a1c",
        "crit": "e0483a", "crit_top": "d5493a", "crit_bottom": "b5321f",
        "crit_border": "ee7563", "crit_fg": "fff6f0", "crit_sub": "ffd9cd",
        "crit_track": "9a2a1c", "crit_divider": "e8806f",
        "stale_top": "211d19", "stale_bottom": "181513",
        "stale_border": "2e2925", "stale_track": "2a2521",
        "accent": "d97757", "numeral_ok": "e08a68",
        # the agents panel's state triad: needs you / working / finished /
        # closed. Named per theme so no panel ends up three shades of one hue.
        "states": {"needs": "e6b53f", "working": "d97757", "done": "8cbd92",
                   "closed": "6b6357"},
        "rule": "2e2925", "plan_border": "5a3d30",
        "radius": 12, "gap": 14, "front_w": 76,
        "meter_h": 4, "row_gap": 7, "meter_label_w": 28,
        "size_label": 9, "numeral_tracking": -0.04,
        "panel_w": 252, "panel_cols": (62, 26, 78),
        "font": {"kind": "system", "book": ["segoeui.ttf"],
                 "semi": ["seguisb.ttf", "segoeuib.ttf"], "bold": ["segoeuib.ttf"]},
        "meter": "solid", "live": "arc", "scanlines": False, "invert": False,
        "upper": False, "glow": None, "tray": "disc",
        "shadow": {"alpha": 115, "tint": (0, 0, 0)},
        "crit_shadow": {"alpha": 128, "tint": (120, 20, 10)},
        "stale_shadow": {"alpha": 115, "tint": (0, 0, 0)},
    },
    "matrix": {
        "label": "The Matrix",
        "paper": "050f07",
        "bg_top": "071a0c", "bg_bottom": "030a05", "border": "125a26", "track": "0a2e14",
        "fg": "c8ffd4", "muted": "3f9a54", "dim": "2b6e3c",
        "ok": "00ff41", "warn": "ffb000", "warn_border": "7a5200",
        "crit": "ff2d3a", "crit_top": "ff2d3a", "crit_bottom": "ff2d3a",
        "crit_border": "ff8a93", "crit_fg": "000000", "crit_sub": "2b0007",
        "crit_track": "c2172a", "crit_divider": "8a0a15",
        "stale_top": "030805", "stale_bottom": "030805",
        "stale_border": "0b2e14", "stale_track": "071a0c",
        "accent": "00ff41", "numeral_ok": "00ff41",
        # Matrix names its own triad rather than reusing ok/accent, which are
        # the same green - a working and a finished row read identically.
        "states": {"needs": "ffe23d", "working": "ffba66", "done": "00ff41",
                   "closed": "2b6e3c"},
        "rule": "0b2e14", "plan_border": "125a26",
        "radius": 0, "gap": 8, "front_w": 96,
        "meter_h": 5, "row_gap": 5, "meter_label_w": 24,
        "size_label": 8, "numeral_tracking": -0.02,
        "panel_w": 296, "panel_cols": (68, 26, 94),
        "font": {"kind": "variable", "file": "FiraCode.ttf",
                 "book": ("Regular",), "semi": ("SemiBold", "Medium"), "bold": ("Bold",)},
        "meter": "cells", "live": "cursor", "scanlines": True, "invert": False,
        "upper": False, "glow": "00ff41", "tray": "squares",
        "shadow": {"alpha": 153, "tint": (0, 0, 0), "halo": ("00ff41", 26)},
        "crit_shadow": {"alpha": 90, "tint": (255, 45, 58), "halo": ("ff2d3a", 102)},
        "stale_shadow": {"alpha": 153, "tint": (0, 0, 0)},
    },
    "eink": {
        # Four ink levels only: K100 141413 / D66 5c5b57 / L33 adaba6 / P00 efeee9
        "label": "E-ink",
        "paper": "efeee9",
        "bg_top": "efeee9", "bg_bottom": "efeee9", "border": "141413", "track": "adaba6",
        "fg": "141413", "muted": "5c5b57", "dim": "5c5b57",
        "ok": "141413", "warn": "141413", "warn_border": "141413",
        "crit": "141413", "crit_top": "141413", "crit_bottom": "141413",
        "crit_border": "141413", "crit_fg": "efeee9", "crit_sub": "adaba6",
        "crit_track": "5c5b57", "crit_divider": "5c5b57",
        "stale_top": "efeee9", "stale_bottom": "efeee9",
        "stale_border": "adaba6", "stale_track": "adaba6",
        "accent": "141413", "numeral_ok": "141413",
        "states": {"needs": "a8341f", "working": "141413", "done": "2f6144",
                   "closed": "5c5b57"},
        "rule": "141413", "plan_border": "141413",
        "radius": 0, "gap": 8, "front_w": 92,
        "meter_h": 5, "row_gap": 5, "meter_label_w": 30,
        "size_label": 8, "numeral_tracking": -0.03,
        "panel_w": 296, "panel_cols": (74, 26, 94),
        "font": {"kind": "variable", "file": "SpaceGrotesk.ttf",
                 "book": ("Regular",), "semi": ("Medium", "SemiBold"), "bold": ("Bold",)},
        "meter": "dither", "live": "blink", "scanlines": False, "invert": True,
        "upper": True, "glow": None, "tray": "ring",
        "shadow": None, "crit_shadow": None, "stale_shadow": None,
        "ink": {"k100": "141413", "d66": "5c5b57", "l33": "adaba6", "p00": "efeee9"},
    },
}


def _startup_theme():
    """Like zoom, the theme is baked into the constants below at import time,
    so it has to be read before the app object exists. Changing it relaunches."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            name = json.load(fh).get("theme", DEFAULTS["theme"])
    except (OSError, ValueError, TypeError):
        name = DEFAULTS["theme"]
    return name if name in THEMES else DEFAULTS["theme"]


THEME_NAME = _startup_theme()
T = THEMES[THEME_NAME]


def _rgb(value, alpha=255):
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)


def _tok(key, alpha=255):
    return _rgb(T[key], alpha)


BG_TOP = _tok("bg_top")
BG_BOTTOM = _tok("bg_bottom")
BORDER = _tok("border")
TRACK = _tok("track")
FG = _tok("fg")
MUTED = _tok("muted")
DIM = _tok("dim")

OK = _tok("ok")
WARN = _tok("warn")
WARN_BORDER = _tok("warn_border")
CRIT = _tok("crit")
CRIT_TOP = _tok("crit_top")
CRIT_BOTTOM = _tok("crit_bottom")
CRIT_BORDER = _tok("crit_border")
CRIT_FG = _tok("crit_fg")
CRIT_SUB = _tok("crit_sub")
CRIT_TRACK = _tok("crit_track")
CRIT_DIVIDER = _tok("crit_divider")

STALE_TOP = _tok("stale_top")
STALE_BOTTOM = _tok("stale_bottom")
STALE_BORDER = _tok("stale_border")
STALE_TRACK = _tok("stale_track")

ACCENT = _tok("accent")
NUMERAL_OK = _tok("numeral_ok")
PLAN_BORDER = _tok("plan_border")
PANEL_RULE = _tok("rule")

INK = T.get("ink") or {}
INVERT = T["invert"]
UPPER = T["upper"]
METER_STYLE = T["meter"]
LIVE_STYLE = T["live"]
SCANLINES = T["scanlines"]
GLOW = _rgb(T["glow"]) if T["glow"] else None
TRAY_STYLE = T["tray"]

# --- geometry -------------------------------------------------------------
# The two footprints are contract-fixed and never themed.
BAR_W = px(232)
BAR_H = px(66)
ERROR_H = px(36)

RADIUS = px(T["radius"])
PAD_L = px(16)
PAD_R = px(14)
GAP = px(T["gap"])
FRONT_W = px(T["front_w"])
DIVIDER_H = px(38)
METER_H = px(T["meter_h"])
METER_GAP = px(5)
METER_LABEL_W = px(T["meter_label_w"])
METER_VALUE_W = px(20)
ROW_GAP = px(T["row_gap"])
ARC = px(10)
ARC_W = px(2)
CURSOR_W = px(7)
CURSOR_H = px(14)
BLINK = px(5)
BLINK_INSET = px(6)

# Tuck button. It lives in the strip above the meter stack - the only part
# of the top-right corner the first meter row's value does not claim.
TUCK_BTN_W = px(16)
TUCK_BTN_H = px(14)

TUCK_STEP = 0.16          # slide progress per animation frame
TUCK_FRAME_MS = 16

SHADOW_PAD = px(32)
SHADOW_DY = px(8)
SHADOW_SIGMA = px(12)

PANEL_W = px(T["panel_w"])
PANEL_PAD_X = px(14)
PANEL_PAD_TOP = px(11)
PANEL_PAD_BOTTOM = px(10)
PANEL_COL_LABEL = px(T["panel_cols"][0])
PANEL_COL_PCT = px(T["panel_cols"][1])
PANEL_COL_RESET = px(T["panel_cols"][2])
PANEL_COL_GAP = px(8)
PANEL_ROW_GAP = px(7)

TRAY_R = 26                  # on the 64px master, never DPI scaled
TRAY_NOTCH_W = 7
TRAY_CORE_R = 8



def _system_font(names, size):
    for candidate in platform.font_candidates(names):
        try:
            return ImageFont.truetype(candidate, px(size))
        except OSError:
            continue
    return ImageFont.load_default(size=px(size))



def _variable_font(path, size, wanted):
    """Fira Code and Space Grotesk ship as variable fonts whose default
    instance is Light, so the weight always has to be named explicitly.
    Space Grotesk has no SemiBold - the theme table asks for Medium there."""
    try:
        font = ImageFont.truetype(path, px(size))
    except OSError:
        return _system_font(["segoeui.ttf"], size)
    try:
        available = [n.decode() if isinstance(n, bytes) else n
                     for n in font.get_variation_names()]
        for want in wanted:
            for name in available:
                if name.replace(" ", "").lower() == want.replace(" ", "").lower():
                    font.set_variation_by_name(name)
                    return font
    except (OSError, AttributeError):
        pass
    return font


_FONT_CACHE = {}


def FONT(weight, size):
    """A face at one weight and size. Loading a variable font means reading the
    file and naming an instance, so the panel's dozen faces are built once."""
    key = (weight, size)
    hit = _FONT_CACHE.get(key)
    if hit is None:
        spec = T["font"]
        if spec["kind"] == "system":
            hit = _system_font(spec[weight], size)
        else:
            hit = _variable_font(os.path.join(FONT_DIR, spec["file"]), size,
                                 spec[weight])
        _FONT_CACHE[key] = hit
    return hit


F_NUMERAL = FONT("bold", 28)
F_PCT = FONT("semi", 12)
F_SUB = FONT("book", 9)
F_LABEL = FONT("book", T["size_label"])
F_VALUE = FONT("bold", 10)
F_ERROR = FONT("bold", 11)
F_PANEL_TITLE = FONT("bold", 11)
F_PANEL_PLAN = FONT("bold", 8)
F_PANEL_LABEL = FONT("book", 9)
F_PANEL_PCT = FONT("bold", 10)
F_PANEL_RESET = FONT("book", 9)
F_PANEL_FOOT = FONT("book", 9)
F_PANEL_FOOT_B = FONT("bold", 9)
F_PANEL_HINT = FONT("book", 8)

NUMERAL_TRACKING = T["numeral_tracking"] * px(28)

_MEASURE = ImageDraw.Draw(Image.new("RGB", (4, 4)))


def text_w(text, font):
    return int(round(_MEASURE.textlength(text, font=font)))


def tracked_w(text, font, tracking):
    if not text:
        return 0
    return int(round(sum(_MEASURE.textlength(ch, font=font) for ch in text)
                     + tracking * (len(text) - 1)))


def draw_tracked(pen, xy, text, font, fill, tracking):
    x, y = xy
    for char in text:
        pen.text((x, y), char, font=font, fill=fill)
        x += _MEASURE.textlength(char, font=font) + tracking


_ELIDE_CACHE = {}


def elide(text, font, max_width):
    """Preview text on one line and trim to fit, ending in an ellipsis.

    Binary search rather than a character-at-a-time walk: measuring is the
    expensive part. Even so, a panel repainting eight times a second measures
    the same handful of strings over and over, so the answers are cached."""
    # Commands and permission descriptions can contain newlines. Pillow's
    # textlength rejects them; only flatten the preview, never the tool input.
    text = " ↵ ".join(text.splitlines()).replace("\t", "    ")
    key = (text, id(font), max_width)
    hit = _ELIDE_CACHE.get(key)
    if hit is not None:
        return hit
    if text_w(text, font) <= max_width:
        _ELIDE_CACHE[key] = text
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if text_w(text[:mid] + "…", font) <= max_width:
            low = mid
        else:
            high = mid - 1
    out = text[:low] + "…"
    if len(_ELIDE_CACHE) > 2000:
        _ELIDE_CACHE.clear()
    _ELIDE_CACHE[key] = out
    return out


def ascent(font):
    try:
        return font.getmetrics()[0]
    except AttributeError:
        return font.size


# --------------------------------------------------------------------------
# Per-pixel-alpha window. Gives the design its soft shadow and genuinely
# antialiased corners, which a colour-keyed window cannot do.
class UsageError(Exception):
    """Fetching usage failed. ``kind`` drives what the chip shows."""

    def __init__(self, kind, message, retry_after=0):
        super().__init__(message)
        self.kind = kind            # "auth" | "ratelimit" | "network" | "other"
        self.retry_after = retry_after


def read_oauth():
    try:
        data = platform.read_credentials(CREDENTIALS_PATH)
    except (OSError, ValueError) as exc:
        raise UsageError("auth", "Run Claude Code and sign in to connect usage.") from exc
    oauth = data.get("claudeAiOauth") or {} if isinstance(data, dict) else {}
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        raise UsageError("auth", "Run Claude Code and sign in to connect usage.")
    return oauth



def fetch_usage():
    """Return (payload, oauth). Raises UsageError."""
    oauth = read_oauth()
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": "Bearer " + oauth["accessToken"],
            "anthropic-beta": OAUTH_BETA,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response), oauth
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise UsageError("auth", "token rejected (%s) - run claude to refresh" % exc.code)
        if exc.code == 429:
            # The endpoint is shared with Claude Code's own /usage polling and is
            # rate limited tightly. Back off rather than hammering it.
            try:
                retry = int(exc.headers.get("Retry-After") or 0)
            except (TypeError, ValueError):
                retry = 0
            raise UsageError("ratelimit", "rate limited by the usage endpoint", retry)
        raise UsageError("network", "HTTP %s from usage endpoint" % exc.code)
    except urllib.error.URLError as exc:
        raise UsageError("network", "offline: %s" % exc.reason)
    except (ValueError, OSError) as exc:
        raise UsageError("other", str(exc))


def save_cache(payload, plan):
    """Keep the last good response so a restart does not spend a request."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": time.time(), "plan": plan, "payload": payload}, fh)
    except (OSError, TypeError, ValueError):
        pass


def load_cache():
    """Return (payload, plan, fetched_at) or (None, None, 0)."""
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("payload"), data.get("plan"), float(data.get("fetched_at") or 0)
    except (OSError, ValueError, TypeError):
        return None, None, 0


def _metric(key, label, pct, severity, resets_at, active=False, detail=None):
    return {
        "key": key,
        "label": label,
        "pct": float(pct or 0),
        "severity": severity or "normal",
        "resets_at": resets_at,
        "active": bool(active),
        "detail": detail or label,
    }


def build_metrics(payload):
    """Pick the limits worth showing, newest schema first."""
    limits = payload.get("limits") or []
    by_kind = {}
    for limit in limits:
        by_kind.setdefault(limit.get("kind"), []).append(limit)
    metrics = []

    session = by_kind.get("session")
    if session:
        row = session[0]
        metrics.append(_metric("session", "5h", row.get("percent"), row.get("severity"),
                               row.get("resets_at"), row.get("is_active"), "Session \u00b7 5h"))
    elif payload.get("five_hour"):
        row = payload["five_hour"]
        metrics.append(_metric("session", "5h", row.get("utilization"), None,
                               row.get("resets_at"), False, "Session \u00b7 5h"))

    weekly = by_kind.get("weekly_all")
    if weekly:
        row = weekly[0]
        metrics.append(_metric("weekly", "week", row.get("percent"), row.get("severity"),
                               row.get("resets_at"), row.get("is_active"), "Week \u00b7 all"))
    elif payload.get("seven_day"):
        row = payload["seven_day"]
        metrics.append(_metric("weekly", "week", row.get("utilization"), None,
                               row.get("resets_at"), False, "Week \u00b7 all"))

    scoped = by_kind.get("weekly_scoped") or []
    if scoped:
        row = max(scoped, key=lambda item: item.get("percent") or 0)
        scope = row.get("scope") or {}
        model = (scope.get("model") or {}).get("display_name")
        name = model or scope.get("surface") or "scoped"
        metrics.append(_metric("scoped", name[:7], row.get("percent"), row.get("severity"),
                               row.get("resets_at"), row.get("is_active"),
                               "Week \u00b7 %s" % name))
    return metrics


def build_spend(payload):
    spend = payload.get("spend") or {}
    if not spend.get("enabled"):
        return None
    used = spend.get("used") or {}
    exponent = used.get("exponent")
    if exponent is None:
        exponent = 2
    amount = (used.get("amount_minor") or 0) / float(10 ** exponent)
    if amount <= 0:
        return None                       # handoff: credits hidden when zero
    # Fira Code and Space Grotesk both lack \u20aa, so the shekel is spelt
    symbol = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3",
              "ILS": "ILS "}.get(
        used.get("currency", "USD"), "")
    return {"text": "%s%s" % (symbol, format(amount, ",.2f")), "amount": amount}


MODEL_NAMES = [
    ("claude-fable-5-1", "Fable 5.1"),
    ("claude-fable", "Fable"),
    ("claude-opus-5", "Opus 5"),
    ("claude-opus", "Opus"),
    ("claude-sonnet-5", "Sonnet 5"),
    ("claude-sonnet", "Sonnet"),
    ("claude-haiku-4-5", "Haiku 4.5"),
    ("claude-haiku", "Haiku"),
]


def pretty_model(model_id):
    for prefix, name in MODEL_NAMES:
        if model_id.startswith(prefix):
            return name
    return model_id.replace("claude-", "")[:12]


def format_tokens(count):
    if count >= 1_000_000_000:
        return "%.1fB" % (count / 1_000_000_000.0)
    if count >= 1_000_000:
        return "%.1fM" % (count / 1_000_000.0)
    if count >= 1_000:
        return "%dk" % (count / 1_000.0)
    return str(count)


def _encode_path(path):
    """Claude Code names a project dir after its path with every separator and
    the drive colon turned into a dash: C:\\Users\\me -> C--Users-me."""
    return path.replace(":", "-").replace("\\", "-").replace("/", "-")


_ENCODED_HOME = _encode_path(HOME)


def pretty_project(name):
    """C--Users-me-notes -> notes, C--tmp -> tmp, the home dir itself -> ~.
    The drive letter keeps whatever case the path was typed in, so the home
    match is case-insensitive."""
    lowered, home = name.lower(), _ENCODED_HOME.lower()
    if lowered == home:
        return "~"
    if lowered.startswith(home + "-"):
        return name[len(_ENCODED_HOME) + 1:].strip("-") or "~"
    if len(name) > 3 and name[1:3] == "--":
        return name[3:].strip("-") or name      # another drive: D--work -> work
    return name.strip("-") or name


# Parsing every transcript on every poll is wasteful - only the session being
# written to actually changes, so results are memoised on (size, mtime, window).
STATS_DAYS = 14
_STATS_CACHE = {}


def _scan_stats(path, cutoff):
    """One transcript's contribution to the stats pane: turns and tokens per
    local day, tool calls, and the sessions it holds."""
    days = {}
    tools = 0
    sessions = set()
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return days, tools, sessions
    with handle:
        for line in handle:
            if '"usage"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            stamp = record.get("timestamp")
            if not usage or not stamp:
                continue
            try:
                moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            moment = moment.astimezone()
            if moment < cutoff:
                continue
            model_id = message.get("model") or ""
            if not model_id or model_id.startswith("<"):
                continue                  # <synthetic> records carry no usage
            bucket = days.setdefault(moment.date(), {"turns": 0, "tokens": 0})
            bucket["turns"] += 1
            # cache reads are the same context billed again, not new work -
            # counting them reports billions of tokens and a quarter of a
            # million per turn, which says nothing about the fortnight
            bucket["tokens"] += ((usage.get("output_tokens", 0) or 0)
                                 + (usage.get("input_tokens", 0) or 0)
                                 + (usage.get("cache_creation_input_tokens", 0) or 0))
            content = message.get("content")
            if isinstance(content, list):
                tools += sum(1 for item in content
                             if isinstance(item, dict) and item.get("type") == "tool_use")
            if record.get("sessionId"):
                sessions.add(record["sessionId"])
    return days, tools, sessions


def build_stats(days=STATS_DAYS):
    """The console's stats pane, from the local transcripts.

    Four of the six numbers are in the files outright. A turn's wall-clock
    duration and the permission prompts a session answered are not recorded
    anywhere, so those two cells carry what can actually be measured: tokens
    per turn, and how many projects were touched."""
    start = (datetime.now().astimezone().replace(hour=0, minute=0, second=0,
                                                 microsecond=0)
             - timedelta(days=days - 1))
    cutoff = start.timestamp()
    per_day = {}
    tools = 0
    sessions = set()
    projects = set()

    try:
        entries = list(os.scandir(PROJECTS_DIR))
    except OSError:
        entries = []
    for project in entries:
        if not project.is_dir():
            continue
        try:
            files = list(os.scandir(project.path))
        except OSError:
            continue
        touched = False
        for entry in files:
            if not entry.name.endswith(".jsonl"):
                continue
            try:
                info = entry.stat()
            except OSError:
                continue
            if info.st_mtime < cutoff:
                continue
            # A finished transcript never changes, so its numbers are kept
            # against its size and mtime - a fortnight of files is far too much
            # to re-read on a poll.
            key = (entry.path, info.st_size, info.st_mtime, cutoff)
            hit = _STATS_CACHE.get(key)
            if hit is None:
                hit = _scan_stats(entry.path, start)
                if len(_STATS_CACHE) > 600:
                    _STATS_CACHE.clear()
                _STATS_CACHE[key] = hit
            found, count, ids = hit
            for date, bucket in found.items():
                into = per_day.setdefault(date, {"turns": 0, "tokens": 0})
                into["turns"] += bucket["turns"]
                into["tokens"] += bucket["tokens"]
            tools += count
            sessions |= ids
            touched = touched or bool(found)
        if touched:
            projects.add(project.name)

    turns = sum(day["turns"] for day in per_day.values())
    tokens = sum(day["tokens"] for day in per_day.values())
    spark = []
    for offset in range(days):
        date = (start + timedelta(days=offset)).date()
        spark.append(per_day.get(date, {}).get("turns", 0))
    return {
        "sessions": len(sessions),
        "turns": turns,
        "tokens": tokens,
        "tools": tools,
        "per_turn": int(tokens / turns) if turns else 0,
        "projects": len(projects),
        "spark": spark,
        "days": days,
    }


def active_projects(window=ACTIVE_WINDOW_S):
    """Project dirs whose transcript was written to in the last ``window`` seconds."""
    now = time.time()
    found = []
    try:
        entries = list(os.scandir(PROJECTS_DIR))
    except OSError:
        return found
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            for item in os.scandir(entry.path):
                if not item.name.endswith(".jsonl"):
                    continue
                if now - item.stat().st_mtime < window:
                    found.append(entry.name)
                    break
        except OSError:
            continue
    return found


# --------------------------------------------------------------------------
# agents - one row per live Claude Code session
# --------------------------------------------------------------------------
SESSIONS_DIR = os.path.join(CLAUDE_DIR, "sessions")
IDE_DIR = os.path.join(CLAUDE_DIR, "ide")

# Transcripts reach tens of MB, so state is read from the tail, never by
# parsing the file.
AGENT_TAIL_BYTES = 64 * 1024
# Mid-turn but silent for this long reads as "parked on a prompt" rather than
# "producing tokens". It is a heuristic, not a permission-prompt signal.
AGENT_IDLE_S = 90
# Alive but untouched this long: still burning a process, almost certainly
# abandoned, and the row you would want to terminate.
AGENT_STALE_S = 1800

_STILL_ACTIVE = 259
_SYNCHRONIZE = 0x00100000
_QUERY_LIMITED_INFORMATION = 0x1000


def _proc_started(pid):
    return platform.process_started(pid)



def _pid_alive(pid, started=None):
    return platform.pid_alive(pid, started)



def _agent_transcript(cwd, session_id):
    path = os.path.join(PROJECTS_DIR, _encode_path(cwd), session_id + ".jsonl")
    return path if os.path.exists(path) else None


def _last_stop_reason(path):
    """The final ``stop_reason`` in a transcript, read from the tail alone.
    ``end_turn`` means the turn finished; anything else means it is still in
    flight."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > AGENT_TAIL_BYTES:
                handle.seek(size - AGENT_TAIL_BYTES)
                handle.readline()               # drop the partial first line
            blob = handle.read()
    except OSError:
        return None
    marker = b'"stop_reason":"'
    reason = None
    for line in blob.splitlines():
        at = line.rfind(marker)
        if at == -1:
            continue
        rest = line[at + len(marker):]
        end = rest.find(b'"')
        if end != -1:
            reason = rest[:end].decode("ascii", "replace")
    return reason


def _list_claude_agents(idle_after=AGENT_IDLE_S):
    """Every Claude Code session with a file in ~/.claude/sessions.

    state is one of ``working`` (mid turn), ``needs`` (mid turn but quiet,
    so probably waiting on the user), ``done`` (ended its turn) and
    ``closed`` (the process is gone)."""
    agents = []
    try:
        entries = list(os.scandir(SESSIONS_DIR))
    except OSError:
        return agents

    now = time.time()
    for entry in entries:
        if not entry.name.endswith(".json"):
            continue
        try:
            with open(entry.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue

        pid = data.get("pid") or 0
        try:
            started = platform.session_process_start(data, pid)
        except (TypeError, ValueError):
            started = 0
        cwd = data.get("cwd") or ""
        session_id = data.get("sessionId") or ""
        path = _agent_transcript(cwd, session_id) if cwd and session_id else None
        reason = _last_stop_reason(path) if path else None
        try:
            idle = now - os.path.getmtime(path) if path else None
        except OSError:
            idle = None

        if not _pid_alive(pid, started):
            state = "closed"
        elif reason == "end_turn":
            state = "done"
        elif idle is not None and idle > idle_after:
            state = "needs"
        else:
            state = "working"

        agents.append({
            "pid": pid,
            "started_at": started,
            "id": session_id,
            "name": data.get("name") or pretty_project(_encode_path(cwd)) or "claude",
            "cwd": cwd,
            "entrypoint": data.get("entrypoint"),
            "state": state,
            "since": (data.get("startedAt") or 0) / 1000.0,
            "idle": idle,
        })

    # A session file outlives its process, so a terminated session would sit in
    # the list forever. Keep a closed row only while it is recent enough to be
    # the one you just closed - long-dead ones are noise.
    agents = [a for a in agents
              if a["state"] != "closed"
              or (a["idle"] if a["idle"] is not None else 1e9) < AGENT_STALE_S]

    # Most recently active first. Start time is the wrong key: two sessions on
    # the same folder are told apart by which one is actually being typed in,
    # and that is the one whose transcript was written to last.
    agents.sort(key=lambda a: (a["state"] == "closed",
                               a["idle"] if a["idle"] is not None else 1e9))

    # Which window a session is being typed in is not recorded anywhere, so
    # claiming one global "this is you" would be a guess. What the data does
    # support is narrower and is the case that actually matters: when several
    # sessions share a folder, the one written to most recently is the live
    # one, and the stale ones are not.
    seen = set()
    for agent in agents:
        folder = (agent.get("cwd") or "").lower()
        idle = agent["idle"] if agent["idle"] is not None else 1e9
        fresh = agent["state"] != "closed" and idle < AGENT_IDLE_S
        agent["active"] = fresh and folder not in seen
        # still holding a process but untouched for a long time: the thing you
        # would actually want to close
        agent["stale"] = agent["state"] != "closed" and idle > AGENT_STALE_S
        if fresh:
            seen.add(folder)

    # slot each session's running subagents directly beneath it
    ordered = []
    for agent in agents:
        ordered.append(agent)
        ordered.extend(list_subagents(agent, idle_after))
    return ordered


def list_agents(idle_after=AGENT_IDLE_S):
    from . import codex_sessions
    from .activity import visible_helpers
    agents = _list_claude_agents(idle_after)
    for agent in agents:
        agent["provider"] = "claude"
    try:
        codex = codex_sessions.list_agents()
        starts = {}
        for agent in codex:
            pid = agent["pid"]
            if pid not in starts:
                starts[pid] = platform.process_started(pid)
            agent["started_at"] = starts[pid]
        agents.extend(codex)
    except Exception as error:
        # One provider must not hide the other provider's sessions.
        log_line("Codex session scan: %s" % type(error).__name__)
    return visible_helpers(agents, time.time())


def list_subagents(parent, idle_after=AGENT_IDLE_S):
    """Keep helpers with their live parent; explicit events supersede history."""
    from pathlib import Path
    from .activity import claude_transcript, apply_activity, merge_activity
    from .claude_activity import read as read_activity
    folder = os.path.join(PROJECTS_DIR, _encode_path(parent.get("cwd", "")),
                          parent.get("id", ""), "subagents")
    if parent["state"] == "closed":
        return []
    now = time.time()
    reported = read_activity(os.path.join(CLAUDE_DIR, "widget-context"), parent, now)
    paths = {}
    try:
        for entry in os.scandir(folder):
            if entry.name.startswith("agent-") and entry.name.endswith(".jsonl"):
                paths[entry.name[6:-6]] = entry.path
    except OSError:
        pass
    found = []
    for key in list(dict.fromkeys([*reported, *paths]))[:256]:
        path = paths.get(key)
        data, modified = {}, now
        try:
            if path:
                data, modified = claude_transcript(Path(path))
        except OSError:
            data["status"] = "unknown"
        live = reported.get(key)
        if live:
            data = merge_activity(data, live)
        elif data.get("status") == "running" and now - modified > idle_after:
            # A file alone cannot distinguish a long tool from a disconnected
            # observer. Retain the row, but label its current activity unknown.
            data.update(status="unknown")
        data.setdefault("status", "unknown")
        row = {
            "pid": parent["pid"],       # it lives inside the parent process
            "started_at": parent.get("started_at"),
            "id": "agent-" + key,
            "name": data.get("name") or (data.get("assignment") or "")[:120] or key[:12],
            "cwd": parent.get("cwd", ""),
            "state": "working",
            "since": data.get("started_at"),
            "idle": max(0, now - max(modified, data.get("state_at", 0))),
            "sub": True,
            "parent": "agent-" + data["parent_task"].removeprefix("agent-") if data.get("parent_task") else parent["id"],
            "root_parent": parent["id"],
            "entrypoint": parent.get("entrypoint"),
            "transcript": path,
        }
        found.append(apply_activity(row, data, now))
    found.sort(key=lambda a: a["idle"])
    return found


def agent_elapsed(agent):
    """Time since the transcript was updated, as ``2m 14s``."""
    idle = agent.get("idle")
    if idle is None:
        return ""
    idle = max(0, int(idle))
    if idle < 60:
        return "%ds" % idle
    if idle < 3600:
        return "%dm %02ds" % (idle // 60, idle % 60)
    return "%dh %02dm" % (idle // 3600, (idle % 3600) // 60)


def _tail_records(path, nbytes=AGENT_TAIL_BYTES * 3):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > nbytes:
                handle.seek(size - nbytes)
                handle.readline()
            blob = handle.read()
    except OSError:
        return []
    records = []
    for line in blob.splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def _blocks(record):
    """Flatten one transcript record into (kind, text) pairs."""
    message = record.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return [("text", content)]
    found = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "text":
                found.append(("text", item.get("text") or ""))
            elif kind == "tool_use":
                arg = item.get("input") or {}
                # a shell command is the interesting part; otherwise the path
                detail = arg.get("command") or arg.get("file_path") or \
                    arg.get("pattern") or arg.get("prompt") or ""
                found.append(("cmd", ("%s %s" % (item.get("name") or "", detail)).strip()))
            elif kind == "tool_result":
                body = item.get("content")
                if isinstance(body, list):
                    body = " ".join(str(b.get("text", "")) for b in body
                                    if isinstance(b, dict))
                found.append(("out", str(body or "")))
    return found


def agent_context_tokens(records):
    """Latest request's input context, including cached input, never a sum
    across turns. Matches Claude Code's input-only status-line formula.
    A compaction boundary invalidates the preceding request's reading.
    Window capacity is not in transcripts, so do not infer a percentage.
    """
    fields = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    def token_count(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
            metadata = record.get("compactMetadata") or {}
            value = metadata.get("postTokens") if isinstance(metadata, dict) else None
            return value if token_count(value) else None
        if record.get("type") != "assistant" or record.get("isApiErrorMessage"):
            continue
        message = record.get("message")
        if not isinstance(message, dict) or message.get("model") == "<synthetic>":
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict) or "input_tokens" not in usage:
            continue
        values = [usage.get(field, 0) for field in fields]
        if all(token_count(value) for value in values):
            return sum(values)
    return None


def agent_details(records):
    """Real conversation text from the bounded recent history, excluding tools
    and harness messages. Missing history stays unknown instead of becoming
    tool output or an invented task description.
    """
    details = {"last_request": None, "latest_message": None, "last_tool": None}
    for record in reversed(records):
        if not isinstance(record, dict) or record.get("isMeta") or record.get("isCompactSummary"):
            continue
        role = record.get("type")
        message = record.get("message")
        if role not in ("user", "assistant") or not isinstance(message, dict):
            continue
        if record.get("isApiErrorMessage") or message.get("model") == "<synthetic>":
            continue
        blocks = _blocks(record)
        if role == "assistant" and details["last_tool"] is None:
            details["last_tool"] = next((text[:2000] for kind, text in reversed(blocks) if kind == "cmd"), None)
        text = " ".join(value for kind, value in blocks if kind == "text")
        # Local command echoes, notifications, and system reminders are not
        # requests written by the user. Tool-result blocks never enter text.
        if role == "user":
            text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S)
            if text.lstrip().startswith(("<local-command", "<command-name>",
                                       "<task-notification>", "[Request interrupted")):
                continue
        text = " ".join(text.split())
        key = "last_request" if role == "user" else "latest_message"
        if text and details[key] is None:
            details[key] = text[:2000]
    return details


def agent_model_source(agent, include_provider=True):
    model = agent.get("model") or "Model unknown"
    match = re.fullmatch(r"claude-(opus|sonnet|haiku)-(\d+(?:-\d{1,2})?)(?:-\d{8})?", model)
    if match:
        model = match[1].title() + " " + match[2].replace("-", ".")
    source = "Subagent" if agent.get("sub") else {
        "claude-vscode": "VS Code", "cli": "Terminal", "codex-cli": "Terminal",
        "codex-vscode": "VS Code"}.get(agent.get("entrypoint"), "Claude Code")
    provider = {"codex": "Codex · ", "claude": "Claude · "}.get(agent.get("provider"), "")
    return (provider if include_provider else "") + model + " · " + source


def agent_status(agent):
    permissions = agent.get("permissions") or []
    if permissions:
        return "Approval needed" if permissions[0].get("actionable") else "Answer in " + provider_name(agent)
    if agent.get("status_detail") and agent.get("state") != "closed":
        return agent["status_detail"]
    if agent.get("sub") and agent.get("activity"):
        from .activity import current_tools
        data = agent["activity"]
        if data.get("status") == "completed":
            return "Completed"
        tools = current_tools(data)
        if tools:
            return "Running tool" if len(tools) == 1 else "Running %d tools" % len(tools)
    return {"working": "Working", "needs": "Quiet · check chat",
            "done": "Ready", "closed": "Session closed"}.get(agent.get("state"), "Unknown")


def agent_activity_stamp(agent):
    from .activity import current_tools
    data = agent.get("activity") or {}
    tools = current_tools(data)
    if tools and tools[-1].get("started_at"):
        return agent_elapsed({"idle": time.time() - tools[-1]["started_at"]}) + " in tool"
    if data.get("ended_at") and data.get("started_at"):
        return agent_elapsed({"idle": data["ended_at"] - data["started_at"]}) + " total"
    elapsed = agent_elapsed(agent)
    if not elapsed:
        return "Activity unknown"
    if agent.get("idle", 0) < 5 and agent.get("state") == "working":
        return "Active now"
    return ("Updated " if agent.get("state") == "working" else "Idle ") + elapsed


def agent_activity(agent):
    permissions = agent.get("permissions") or []
    if permissions:
        request = permissions[0]["request"]
        inputs = request.get("input") or {}
        detail = inputs.get("command") or inputs.get("file_path") or request.get("description") or "Review request"
        return request["tool_name"] + " · " + str(detail)
    data = agent.get("activity")
    if data:
        from .activity import current_tools
        if data.get("status") in ("completed", "failed", "stopped"):
            result = data.get("result") or agent.get("latest_message")
            if result:
                return "Result · " + result
        tools = current_tools(data)
        if tools:
            return "Now · " + tools[-1]["label"]
        if data.get("status") == "running" and data.get("summary"):
            return "Progress · " + data["summary"]
        last = data.get("last_tool") or data.get("reported_tool")
        if last:
            return ("Last observed · " if data.get("status") == "unknown" else "Last tool · ") + last
    if agent.get("state") in ("done", "closed") and agent.get("latest_message"):
        return "Last reply · " + agent["latest_message"]
    command = agent.get("last_tool") or next((text for kind, text in reversed(agent.get("tail") or [])
                                              if kind == "cmd"), None)
    if command:
        verb, _, arg = command.partition(" ")
        return verb + (" · " + arg if arg else "")
    if agent.get("latest_message"):
        return "Last reply · " + agent["latest_message"]
    return "No recent activity recorded"


def agent_work_counts(agent):
    data = agent.get("activity") or {}
    count = data.get("reported_tool_count", data.get("tool_count"))
    parts = ["%d tool calls" % count] if count is not None else []
    total = data.get("total_tokens")
    if total is not None:
        parts.append(compact_tokens(total) + " total tokens")
    return " · ".join(parts) or "Usage unavailable"


def agent_tail(agent, limit=7, records=None):
    """Recent activity for one session as terminal lines: ``cmd`` echoes the
    command, ``out`` is its output, ``text`` is prose, ``prompt`` is the
    waiting cursor."""
    if records is None:
        path = agent.get("transcript") or _agent_transcript(agent.get("cwd", ""), agent.get("id", ""))
        records = _tail_records(path) if path else []
    lines = []
    if records:
        for record in records:
            for kind, text in _blocks(record):
                flat = " ".join(str(text).split())
                if not flat:
                    continue
                # a window fits well under 100 characters; keeping whole tool
                # results here would hand the text measurer a 5000-char string
                lines.append((kind, flat[:160]))
    lines = lines[-limit:]
    if agent["state"] == "done":
        lines.append(("prompt", "Ended turn."))
    elif agent["state"] == "needs":
        lines.append(("prompt", "Waiting on you — check this window."))
    elif agent["state"] == "closed":
        lines.append(("prompt", "Session closed."))
    else:
        lines.append(("prompt", "█"))
    return lines


def terminate_agent(agent):
    """Resolve the selected session again before stopping its own process."""
    if agent.get("can_terminate") is False or agent.get("provider") == "codex":
        return False
    if agent.get("sub") or not agent.get("id") or not agent.get("started_at"):
        return False
    current = list_agents()
    matches = [row for row in current if not row.get("sub")
               and row.get("id") == agent["id"] and row.get("pid") == agent.get("pid")
               and row.get("started_at") == agent["started_at"]
               and row.get("state") != "closed"]
    if len(matches) != 1:
        return False
    selected = matches[0]
    for other in current:
        if other is selected or other.get("state") == "closed":
            continue
        if (other.get("pid") == selected["pid"]
                or (other.get("sub") and other.get("parent") == selected["id"])):
            raise PermissionError("This session shares its process with other agents. "
                                  "Ending it would stop them too. Stop the specific agent "
                                  "inside Claude instead; nothing was stopped.")
    return platform.terminate_agent(selected)



def ide_windows():
    """port -> lock payload for every VS Code window that has one. The pid in
    these files is shared by every window of one VS Code install, so it names
    the process, not the window."""
    found = {}
    try:
        entries = list(os.scandir(IDE_DIR))
    except OSError:
        return found
    for entry in entries:
        if not entry.name.endswith(".lock"):
            continue
        try:
            with open(entry.path, encoding="utf-8") as handle:
                found[entry.name[:-5]] = json.load(handle)
        except (OSError, ValueError):
            continue
    return found


def raise_agent_window(agent):
    return platform.raise_agent_window(agent)


def hide_agent_window(agent):
    return platform.hide_agent_window(agent)



ORDER = {"session": 0, "weekly": 1, "scoped": 2}


def session_metric(metrics):
    """The five hour session limit, which is what the tray reflects.

    The bar leads with whichever limit is worst, because that is the one that
    will stop you. The tray answers a narrower question - how much of the
    window you are working in is left - so a weekly limit at 92% does not put
    a red figure in the notification area for the next four days."""
    for metric in metrics or []:
        if metric["key"] == "session":
            return metric
    return None


def choose_front(metrics):
    """Front slot takes the tightest limit; severity flags outrank raw percent.
    Ties fall back to 5h, then week, then model."""
    if not metrics:
        return None, []
    weight = {"critical": 2, "warning": 1}

    def rank(metric):
        return (weight.get(metric["severity"], 0), metric["pct"],
                -ORDER.get(metric["key"], 9))

    front = max(metrics, key=rank)
    rest = [m for m in metrics if m is not front]
    rest.sort(key=lambda m: ORDER.get(m["key"], 9))
    return front, rest


def severity_of(pct, severity):
    if severity == "critical" or pct >= 90:
        return "crit"
    if severity == "warning" or pct >= 75:
        return "warn"
    return "ok"


def meter_color(state):
    return {"crit": CRIT, "warn": WARN}.get(state, OK)


def _reset_parts(iso):
    if not iso:
        return None, None
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None, None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = int((moment - datetime.now(timezone.utc)).total_seconds())
    # The API returns sub-minute offsets; round so 14:19:59 doesn't read as 14:19.
    local = (moment + timedelta(seconds=30)).astimezone().replace(second=0, microsecond=0)
    return delta, local


def vertical_gradient(size, top, bottom):
    width, height = size
    strip = Image.new("RGBA", (1, height))
    pixels = strip.load()
    for y in range(height):
        t = y / float(max(1, height - 1))
        pixels[0, y] = tuple(int(round(top[i] + (bottom[i] - top[i]) * t)) for i in range(4))
    return strip.resize((width, height), Image.NEAREST)


def rounded_mask(size, radius, supersample=4):
    width, height = size
    mask = Image.new("L", (width * supersample, height * supersample), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, width * supersample - 1, height * supersample - 1],
        radius=radius * supersample, fill=255)
    return mask.resize((width, height), Image.LANCZOS)


def chip_base(size, top, bottom, border, radius=RADIUS):
    """A rounded, vertically graded panel with a 1px stroke, on transparency."""
    mask = rounded_mask(size, radius)
    chip = vertical_gradient(size, top, bottom)
    chip.putalpha(mask)
    stroke = Image.new("RGBA", size, (0, 0, 0, 0))
    supersample = 4
    line = Image.new("RGBA", (size[0] * supersample, size[1] * supersample), (0, 0, 0, 0))
    ImageDraw.Draw(line).rounded_rectangle(
        [0, 0, size[0] * supersample - 1, size[1] * supersample - 1],
        radius=radius * supersample, outline=border, width=supersample)
    stroke = line.resize(size, Image.LANCZOS)
    return Image.alpha_composite(chip, stroke)


def shadow_layer(chip, spec="shadow"):
    """The padded drop shadow on its own, without the chip composited onto it.

    Split out of with_shadow so a caller that repaints the same silhouette many
    times - the agents stage, five times a second - can blur once and reuse."""
    settings = T.get(spec) if isinstance(spec, str) else spec
    width, height = chip.size
    canvas = Image.new("RGBA", (width + SHADOW_PAD * 2, height + SHADOW_PAD * 2), (0, 0, 0, 0))
    if settings:
        alpha = chip.split()[3]
        silhouette = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        silhouette.paste(Image.new("RGBA", chip.size,
                                   tuple(settings["tint"]) + (settings["alpha"],)),
                         (SHADOW_PAD, SHADOW_PAD + SHADOW_DY), alpha)
        canvas = Image.alpha_composite(
            canvas, silhouette.filter(ImageFilter.GaussianBlur(SHADOW_SIGMA)))
        halo = settings.get("halo")
        if halo:                       # Matrix: phosphor bleed around the chip
            colour, strength = halo
            glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            glow.paste(Image.new("RGBA", chip.size, _rgb(colour, strength)),
                       (SHADOW_PAD, SHADOW_PAD), alpha)
            canvas = Image.alpha_composite(
                canvas, glow.filter(ImageFilter.GaussianBlur(SHADOW_SIGMA * 1.8)))
    return canvas


_SHADOW_CACHE = {}


def with_shadow(chip, spec="shadow"):
    """Pad the chip by its themed drop shadow. E-ink passes None - a paper card
    casts no shadow, and the 1px border carries the edge."""
    settings = T.get(spec) if isinstance(spec, str) else spec
    key = (chip.size, chip.getchannel('A').tobytes(), json.dumps(settings, sort_keys=True),
           SHADOW_PAD, SHADOW_DY, SHADOW_SIGMA)
    shadow = _SHADOW_CACHE.get(key)
    if shadow is None:
        shadow = shadow_layer(chip, spec)
        if len(_SHADOW_CACHE) >= 4:
            _SHADOW_CACHE.clear()
        _SHADOW_CACHE[key] = shadow
    canvas = shadow.copy()
    canvas.alpha_composite(chip, (SHADOW_PAD, SHADOW_PAD))
    return canvas


def render_error(message, amber=False):
    size = (BAR_W, ERROR_H)
    chip = chip_base(size, BG_TOP, BG_BOTTOM, BORDER)
    pen = ImageDraw.Draw(chip)
    dot = px(8)
    cy = ERROR_H // 2
    pen.ellipse([PAD_L, cy - dot // 2, PAD_L + dot, cy + dot // 2],
                fill=WARN if amber else CRIT)
    text_x = PAD_L + dot + px(9)
    pen.text((text_x, cy - ascent(F_ERROR) // 2 - px(1)), message, font=F_ERROR, fill=FG)
    return with_shadow(chip)


# --------------------------------------------------------------------------
# the figure strips
# --------------------------------------------------------------------------
STRIP_DIR = os.path.join(SCRIPT_DIR, "assets", "strips")
# The app's own mark: the figure as a transparent silhouette, and the .ico
# that carries it at every size Windows asks for.
MARK_PATH = os.path.join(SCRIPT_DIR, "assets", "figure_mark.png")
ICON_PATH = os.path.join(SCRIPT_DIR, "assets", "smith-agents.ico")

# --------------------------------------------------------------------------
# the agent console - transcribed from design/handoff_agent_console
#
# One 320px shell. Collapsed it is a bar: a figure, the session percentage, a
# meta line and three state counts. Expanded it is a segmented tab bar -
# Usage, Stats, Agents - over a single flat ruled sheet.
# --------------------------------------------------------------------------
# [frame w, strip h, body top, body bottom, body left, body right]. Measured
# from each strip's own alpha by design/figure_v2/build_all_strips.py, which
# prints these lines - the strips are drawn from traced paths, so the numbers
# come out of the build rather than being eyeballed.
FIG = {
    "pc_complete":       (88,  36,  1, 35, 0,  87),
    "fishing_complete":  (88,  44,  2, 43, 0,  87),
    "sleeping_complete": (72,  42, 12, 41, 0,  70),
    "scooter_complete":  (118, 50,  2, 49, 0, 117),
    "cooking_complete":  (62,  59, 16, 58, 0,  61),
    "selfie":            (53,  46,  1, 45, 0,  52),
}

# Use the typist's 88px scene as a shared size reference. Compact poses keep
# the same scale instead of growing to fill the entire cell; wider scenes
# can still shrink to fit. The original outlines and proportions stay intact.
FIG_REFERENCE_W = 88
AGENT_FRAMES = 6
# Entrance and idle motion share a 20 FPS clock.
AGENT_FRAME_MS = round(1000 / figure_actions.FPS)

# The widget balances approved variants across sessions and keeps each choice
# until its state changes. Stateless rendering uses a stable session seed.
STATE_STRIPS = artwork.STATE_FIGURES
STATE_STRIP = {state: names[0] for state, names in STATE_STRIPS.items()}
STATE_MOVES = ("working", "needs", "done", "closed")
STATE_RANK = {"needs": 0, "working": 1, "done": 2, "closed": 3}

CONSOLE_W = px(320)
PAD_X = px(12)
# The strips are drawn 88px wide with a two pixel pen. Squeezed into the
# handoff's 40x24 cell they lose the pen entirely, so the cell is sized to the
# artwork instead and they are drawn at their own scale.
CELL_W = px(88)
CELL_H = px(52)
BAR_H = px(88)
TAB_H = px(28)
ROW_H = px(108)  # minimum; long names and font metrics can grow a row
ROW_FIGURE_W = px(57)
ROW_FIGURE_H = px(44)
ROW_NAME_GAP = px(8)
FOOT_H = px(27)
KILL_W = px(16)
GAP = px(8)
# Tucked, this much of the console stays on screen: the group over the
# reading, and the caret pointing back at the rest of it.
PEEK_W = px(108)

DRAWER_LINES = 6

TABS = ("usage", "stats", "agents")
TAB_LABEL = {"usage": "Usage", "stats": "Stats", "agents": "Agents"}


def _ink(pct):
    """Ink at ``pct`` percent over the console's paper.

    The handoff builds every tone out of two values - paper and ink - at a
    lower mix, so nothing introduces a third hue. Its ladder is text 100,
    muted 78, faint 62, ghost 52, border 20, hair 13, hairSoft 8; surfaces are
    raised 5, hover 9, selected 12, sunk 3, track 15."""
    f = max(0.0, min(1.0, pct / 100.0))
    ink, paper = _tok("fg"), _tok("paper")
    return tuple(int(round(c * f + b * (1 - f)))
                 for c, b in zip(ink[:3], paper[:3])) + (255,)


def _tint(colour, pct):
    """Any colour at ``pct`` percent over the paper - the needs-you row's wash
    and the meter tracks."""
    f = max(0.0, min(1.0, pct / 100.0))
    paper = _tok("paper")
    return tuple(int(round(c * f + b * (1 - f)))
                 for c, b in zip(colour[:3], paper[:3])) + (255,)


def usage_tone(pct):
    """The colour a reading takes at its threshold: calm, warning past 50,
    critical past 80.

    E-ink names its working state the same ink as its body text, so the middle
    tier had no step of its own - 28% and 67% both came out solid black. There
    it drops to the mid ink instead, which is the theme's own way of saying
    'louder' without a hue."""
    if pct >= 80:
        return state_colour("needs")
    if pct >= 50:
        warm = state_colour("working")
        return warm if warm != _tok("fg") else _ink(62)
    return _tok("fg")


def state_colour(state):
    """The colour a state is drawn in. Each theme names its own triad, so the
    Matrix console is not three shades of the same green.

    Closed is the exception: the handoff calls for ink at 55%, which vanished
    against the paper, so it sits a little brighter than that."""
    if state == "closed":
        # Not crit. Red put a finished session in the same hue as a working
        # one and made the calmest row in the list the loudest; this is the
        # tone its own title already uses, and it holds against the paper.
        return _ink(62)
    table = T.get("states") or {}
    return _rgb(table.get(state, T["muted"]))


def MONO(weight, size):
    """The handoff sets commands, timers, percentages and transcripts in Fira
    Code in all three themes; only Matrix already has it as its body face."""
    key = ("mono", weight, size)
    hit = _FONT_CACHE.get(key)
    if hit is None:
        names = {"book": ("Regular",), "semi": ("SemiBold", "Medium"),
                 "bold": ("Bold",)}[weight]
        hit = _variable_font(os.path.join(FONT_DIR, "FiraCode.ttf"), size, names)
        _FONT_CACHE[key] = hit
    return hit


def caps(text):
    """The console's keys, tabs and labels are uppercase in every theme, not
    only in the two whose body copy is."""
    return text.upper()


def themed_caps(text):
    """Controls follow the theme: the handoff sets Matrix and E-ink all-caps
    and leaves Claude in sentence case."""
    return text.upper() if T.get("upper") or THEME_NAME == "matrix" else text


def figure_scale(strip, cell_w, cell_h):
    """Fit the original artwork without stretching or enlarging compact poses."""
    frame_w, strip_h, top, bottom, left, right = FIG[strip]
    body_h = bottom - top + 1
    body_w = right - left + 1
    scale = min(SCALE * ZOOM, float(cell_w) / FIG_REFERENCE_W,
                float(cell_w) / body_w, float(cell_h) / body_h)
    return scale, frame_w, strip_h, bottom


_FIGURE_CACHE = {}
# Bounded display-size frames, including the entrance and quiet idle cycle.
_FIGURE_CACHE_LIMIT = 2048


def agent_figure(strip, frame, ink, cell_w=None, cell_h=None, fade=False):
    """One frame of a strip, inked and placed in a fixed cell.

    The handoff plays these as a CSS mask so one asset serves every theme; the
    Pillow equivalent is to keep the drawing's alpha and repaint the colour.
    The cell is a window exactly one frame wide, centred and pinned to its
    bottom edge, so every figure lands on the same baseline in the same spot
    whatever its artboard."""
    cell_w = CELL_W if cell_w is None else cell_w
    cell_h = CELL_H if cell_h is None else cell_h
    approved = artwork.contains(strip)
    if approved:
        frame = figure_actions.canonical_frame(frame)
    key = (strip, frame, ink, cell_w, cell_h, fade, SCALE, ZOOM, bool(T.get("glow")))
    hit = _FIGURE_CACHE.get(key)
    if hit is not None:
        return hit
    if approved:
        cell = artwork.render(strip, ink, cell_w, cell_h, fade=fade, frame=frame)
        if T.get("glow"):
            if ink == state_colour("working"):
                # Orange's fine strokes need more coverage beside neon green.
                # Lift partial coverage without spreading the contour, changing
                # its baseline, or altering the approved source/action frames.
                alpha = cell.getchannel("A")
                cell.putalpha(alpha.point(lambda v: round(v + 0.6*v*(1-v/255))))
            halo = cell.filter(ImageFilter.GaussianBlur(px(2)))
            halo.alpha_composite(cell)
            cell = halo
        if len(_FIGURE_CACHE) >= _FIGURE_CACHE_LIMIT:
            _FIGURE_CACHE.clear()
        _FIGURE_CACHE[key] = cell
        return cell
    if strip not in FIG:
        return None
    try:
        sheet = Image.open(os.path.join(STRIP_DIR, strip + ".png")).convert("RGBA")
    except OSError:
        return None

    scale, frame_w, strip_h, bottom = figure_scale(strip, cell_w, cell_h)
    frame = max(0, min(AGENT_FRAMES - 1, frame))
    cut = sheet.crop((frame * frame_w, 0, (frame + 1) * frame_w, strip_h))
    size = (max(1, int(round(frame_w * scale))), max(1, int(round(strip_h * scale))))

    # Resample coverage directly. Expanding the source strokes at small scales
    # filled in tight loops on 1x Windows screens but not on Retina displays.
    alpha = cut.getchannel("A").resize(size, Image.LANCZOS)
    if fade:
        # The handoff fades a closed figure to half, but its ink is already a
        # dim tone, and the two multiply until the drawing is gone. Fade less
        # and let the colour carry the "this one is over".
        alpha = alpha.point(lambda v: int(v * 0.78))
    inked = Image.new("RGBA", size, ink)
    inked.putalpha(alpha)

    cell = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 0))
    # Copy RGBA coverage once. Using inked as a mask here squares its alpha,
    # which breaks thin antialiased outlines. Paste also clips empty margins.
    cell.paste(inked, ((cell_w - size[0]) // 2,
                       int(round(cell_h - (bottom + 1) * scale))))
    if T.get("glow"):
        # Matrix draws its figures with a drop-shadow glow
        halo = cell.filter(ImageFilter.GaussianBlur(px(2)))
        glow = Image.new("RGBA", cell.size, (0, 0, 0, 0))
        glow.alpha_composite(halo)
        glow.alpha_composite(cell)
        cell = glow
    if len(_FIGURE_CACHE) >= _FIGURE_CACHE_LIMIT:
        _FIGURE_CACHE.clear()
    _FIGURE_CACHE[key] = cell
    return cell


def agent_style(agent):
    """Keep the approved pose stable; retain the tempo for legacy strips."""
    seed = zlib.crc32((agent.get("id") or agent.get("name") or "").encode("utf-8"))
    if agent.get('sub'):
        return artwork.helper_pose(agent), 4.8
    variants = STATE_STRIPS.get(agent.get("state"), STATE_STRIPS["working"])
    strip = agent.get('_figure_pose') if agent.get('_figure_state') == agent.get('state') else None
    if strip not in variants:
        strip = variants[seed % len(variants)]
    return strip, 4.8 + ((seed >> 7) % 7) * 0.2


def agent_phase(agent, elapsed):
    """Pose and frame, using time since the figure's first visible paint."""
    strip, dur = agent_style(agent)
    if artwork.contains(strip):
        return strip, figure_actions.frame_at(elapsed)
    if agent.get("state") not in STATE_MOVES:
        return strip, 0
    return strip, int(elapsed / dur * AGENT_FRAMES) % AGENT_FRAMES


def row_figure(agent, now, cell_w=None, cell_h=None):
    """The figure for one row: pose from the state, ink from the state."""
    strip, frame = agent_phase(agent, now)
    return agent_figure(strip, frame, state_colour(agent.get("state")),
                        cell_w, cell_h, fade=agent.get("state") == "closed")


_BRANCH_CACHE = {}


def _git_head(cwd):
    """Where a folder's HEAD actually lives.

    A session is often opened in a subdirectory, which has no .git of its own,
    and in a worktree .git is a file naming the real directory rather than
    being one. Reading cwd/.git/HEAD blind found neither."""
    here = os.path.abspath(cwd)
    while True:
        dot = os.path.join(here, ".git")
        if os.path.isdir(dot):
            return os.path.join(dot, "HEAD")
        if os.path.isfile(dot):
            with open(dot, encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("gitdir:"):
                        target = line.split(":", 1)[1].strip()
                        if not os.path.isabs(target):
                            target = os.path.join(here, target)
                        return os.path.join(target, "HEAD")
            break
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return os.path.join(cwd, ".git", "HEAD")


def git_branch(cwd):
    """The checked-out branch, straight from .git/HEAD - the row's second line
    says which branch a session is on, which is what tells two sessions in the
    same folder apart."""
    if not cwd:
        return ""
    now = time.time()
    hit = _BRANCH_CACHE.get(cwd)
    if hit and now - hit[0] < 10.0:
        return hit[1]
    name = ""
    try:
        with open(_git_head(cwd), encoding="utf-8") as handle:
            head = handle.read().strip()
        if head.startswith("ref:"):
            name = head.removeprefix("ref: refs/heads/")
        elif len(head) == 40:
            name = head[:7]              # detached head
    except OSError:
        name = ""
    _BRANCH_CACHE[cwd] = (now, name)
    return name


def agent_action(agent):
    """Row line one: the verb and its argument, from the session's last tool
    call - ``Bash`` / ``pytest -q tests/``."""
    state = agent.get("state")
    permissions = agent.get("permissions") or []
    if permissions:
        request = permissions[0]["request"]
        if not permissions[0].get("actionable"):
            return "Answer in " + provider_name(agent), request["tool_name"]
        inputs = request.get("input") or {}
        return "Allow " + request["tool_name"] + "?", str(inputs.get("command") or inputs.get("file_path") or request.get("description") or "Review request")
    if state == "closed":
        return "Session closed", ""
    if state == "done":
        return "Ended turn", ""
    for kind, text in reversed(agent.get("tail") or []):
        if kind == "cmd":
            verb, _, arg = text.partition(" ")
            return verb or "Working", arg
    return ("Waiting on you", "") if state == "needs" else ("Working", "")


def demo_agents(all_figures=False):
    """Sample sessions, optionally expanded to show every approved figure."""
    now = time.time()
    rows = [
        ("needs", "widget-build", 62, "Allow", "rm -rf dist"),
        ("working", "agents-panel", 12, "Bash", "pytest -q tests/"),
        ("done", "copy-pass", 185, "Ended turn", ""),
        ("closed", "scratch", 900, "Session closed", ""),
    ]
    out = []
    for state, branch, idle, verb, arg in rows:
        out.append({
            "id": "demo-" + state, "pid": 0, "name": branch, "cwd": "~/Projects/smith-agents",
            "state": state, "idle": idle, "since": now - idle - 3600,
            "model": "claude-opus-4-6", "entrypoint": "claude-vscode",
            "last_request": "Review the latest changes and check for regressions.",
            "latest_message": "Review complete. All checks passed." if state == "done" else "Checking the changes and their test coverage.",
            "branch": branch, "active": state == "working", "stale": False,
            "tail": [("cmd", (verb + " " + arg).strip()), ("prompt", "")],
            "context_tokens": {"needs": 84200, "working": 126400, "done": 31800,
                               "closed": None}[state],
            "context_capacity": {"needs": 1000000, "working": 200000,
                                 "done": 200000, "closed": None}[state],
        })
    if all_figures:
        # Shared Ready/closed variants should still appear exactly once in the
        # gallery. Show closed's poses first, then Ready's additional cooking.
        examples = sorted(out, key=lambda row: row['state'] == 'done')
        out = []
        seen = set()
        for example in examples:
            variants = STATE_STRIPS[example["state"]]
            for pose in variants:
                if pose in seen:
                    continue
                # Select an ordinary session ID that maps to this pose, so the
                # preview exercises the same renderer and phase as real rows.
                for suffix in range(1000):
                    candidate = dict(example, id="demo-%s-%d" % (pose, suffix))
                    if agent_style(candidate)[0] == pose:
                        candidate["name"] = "Figure " + pose.removeprefix("approved_")
                        out.append(candidate)
                        seen.add(pose)
                        break
                else:
                    raise RuntimeError("Could not select demo figure " + pose)
    return out


def decorate_agents(agents):
    """Read each session's transcript once per scan and hang the results on the
    row. Rows repaint eight times a second; tailing a 15MB transcript at that
    rate is what made the whole desktop crawl."""
    for agent in agents:
        if agent.get("provider") == "codex":
            agent["branch"] = git_branch(agent.get("cwd", ""))
            continue  # Codex readings already came from its own parser.
        path = agent.get("transcript") or _agent_transcript(agent.get("cwd", ""), agent.get("id", ""))
        records = _tail_records(path) if path else []
        agent["tail"] = agent_tail(agent, limit=DRAWER_LINES, records=records)
        agent["context_tokens"] = agent_context_tokens(records)
        agent.update(agent_details(records))
        model = next((record["message"].get("model") for record in reversed(records)
                      if isinstance(record, dict) and record.get("type") == "assistant"
                      and not record.get("isApiErrorMessage") and isinstance(record.get("message"), dict)
                      and record["message"].get("model") not in (None, "<synthetic>")), None)
        agent["model"] = model
        agent["context_capacity"] = read_capacity(os.path.join(CLAUDE_DIR, "widget-context"), agent, model)
        agent["permissions"] = pending_permissions(os.path.join(CLAUDE_DIR, "widget-context"), agent)
        if agent["permissions"]:
            agent["state"] = "needs"
        if agent.get("activity"):
            from .activity import apply_activity
            agent["model"] = agent.get("model") or agent["activity"].get("model")
            apply_activity(agent, agent["activity"], time.time())
        agent["branch"] = git_branch(agent.get("cwd", ""))
    return agents


def sort_agents(agents):
    """Needs you, then working, then finished, then closed. Subagents stay
    attached to the session that spawned them rather than being sorted into a
    group of their own - they are work inside it, not a peer."""
    tops = [a for a in agents if not a.get("sub")]
    subs = {}
    for agent in agents:
        if agent.get("sub"):
            subs.setdefault(agent.get("parent"), []).append(agent)
    tops.sort(key=lambda a: (STATE_RANK.get(a["state"], 9),
                             a.get("idle") if a.get("idle") is not None else 1e9))
    out = []
    seen = set()
    def append(agent):
        key = agent.get("id")
        if key in seen:
            return
        seen.add(key)
        out.append(agent)
        for child in subs.get(key, []):
            append(child)
    for agent in tops:
        append(agent)
    return out


def _chevron(pen, x, y, colour, open_):
    """The caret. Down when the shell is open, right when shut - drawn rather
    than set, because Pillow cannot rotate a glyph."""
    arm = px(4)
    width = max(1, px(1))
    if open_:
        pen.line([(x - arm, y - arm // 2), (x, y + arm // 2)], fill=colour, width=width)
        pen.line([(x, y + arm // 2), (x + arm, y - arm // 2)], fill=colour, width=width)
    else:
        pen.line([(x - arm // 2, y - arm), (x + arm // 2, y)], fill=colour, width=width)
        pen.line([(x + arm // 2, y), (x - arm // 2, y + arm)], fill=colour, width=width)


def _arrow(pen, x, y, colour):
    """The link's north-east arrow. Segoe UI has no glyph for U+2197 and the
    theme fonts fall back to a box, so it is drawn."""
    arm = px(6)
    width = max(1, px(1))
    pen.line([(x, y + arm), (x + arm, y)], fill=colour, width=width)
    pen.line([(x + arm - px(3), y), (x + arm, y)], fill=colour, width=width)
    pen.line([(x + arm, y), (x + arm, y + px(3))], fill=colour, width=width)


def _cross(pen, x, y, size, colour):
    width = max(1, px(1))
    pen.line([(x, y), (x + size, y + size)], fill=colour, width=width)
    pen.line([(x + size, y), (x, y + size)], fill=colour, width=width)


def _link(pen, x, y, text, font, colour, underline=False):
    """A text button. The handoff makes every action a link rather than a pill,
    with an invisible hit pad around it so the target is about 22px tall
    without reading as a button. Returns the pad."""
    width = text_w(text, font)
    pen.text((x, y), text, font=font, fill=colour)
    if underline:
        base = y + ascent(font) + px(3)
        pen.line([(x, base), (x + width, base)], fill=colour, width=max(1, px(1)))
    pad_x, pad_y = px(5), px(5)
    return (x - pad_x, y - pad_y, x + width + pad_x, y + ascent(font) + pad_y)


_BASE_CACHE = {}


def console_base(size):
    """The empty shell: flat paper, a hairline border, the theme's radius.

    It is the same picture every frame and only its height changes, so building
    it is cached against the size - the corner mask alone was most of the paint
    at eight frames a second."""
    key = (size, THEME_NAME)
    hit = _BASE_CACHE.get(key)
    if hit is None:
        chip = Image.new("RGBA", size, (0, 0, 0, 0))
        pen = ImageDraw.Draw(chip)
        radius = px(8) if T["radius"] else 0
        pen.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius,
                              fill=_tok("paper"), outline=_ink(20),
                              width=max(1, px(1)))
        if len(_BASE_CACHE) > 12:
            _BASE_CACHE.clear()
        _BASE_CACHE[key] = chip
        hit = chip
    return hit.copy()


# The shell is rounded and hairlined at its outer pixel, so everything drawn
# inside it has to stay off that pixel. EDGE is the first column that belongs
# to the content; RULE_R is the last. A rule or a band drawn from 0 to
# CONSOLE_W paints over the border instead of stopping at it, which is what
# made the edges look chewed - a dotted line down each side and four square
# corners where the arc should be.
EDGE = px(1)
RULE_R = CONSOLE_W - px(2)
INNER_RADIUS = max(0, px(8) - px(1)) if T["radius"] else 0


def rule(pen, y, colour):
    """A horizontal rule that stops at the border rather than crossing it."""
    pen.line([(EDGE, y), (RULE_R, y)], fill=colour)


def band(pen, top, bottom, colour, corners=None):
    """A full-width fill inside the shell. `corners` rounds the pair that meets
    the shell's own corner, so the arc survives the fill."""
    if corners and INNER_RADIUS:
        pen.rounded_rectangle([EDGE, top, RULE_R, bottom], radius=INNER_RADIUS,
                              fill=colour, corners=corners)
    else:
        pen.rectangle([EDGE, top, RULE_R, bottom], fill=colour)


def reset_stamp(iso):
    """When a limit resets, as the console prints it: a clock time today,
    otherwise the weekday. _reset_parts hands back a datetime, not a string."""
    delta, local = _reset_parts(iso)
    if local is None:
        return ""
    if delta is not None and delta < 20 * 3600:
        return local.strftime("%H:%M")
    return local.strftime("%a")


# The bar leads with one reading, and clicking the number walks through them.
# `worst` is whichever limit is highest - the one that will actually stop you -
# and after it come the limits themselves, in the order the console lists them.
BAR_MODES = ("worst", "session", "weekly", "scoped")
READING_VIEWS = ("used", "remaining", "reset")


def header_reading_value(metric, view):
    """A quota's used/remaining share, or its local reset time/day."""
    if view == "reset":
        return reset_stamp(metric.get("resets_at")) or "—"
    pct = metric["pct"]
    if view == "remaining":
        pct = max(0, 100 - pct)
    return "%d%%" % round(pct)


def bar_reading(metrics, front, mode):
    """The metric the bar is leading with."""
    if mode == "worst":
        shown = front
        for m in metrics or []:
            if shown is None or m["pct"] > shown["pct"]:
                shown = m
        return shown
    for m in metrics or []:
        if m["key"] == mode:
            return m
    return front


def next_bar_mode(metrics, mode):
    """The next reading that exists, and looks different from the last.

    A plan without a scoped limit skips it rather than parking the bar on an
    empty mode, and whichever limit is currently the worst is skipped too - it
    is already on screen under its own name, so stopping on it again would read
    as a click that did nothing."""
    keys = {m["key"] for m in metrics or []}
    worst = bar_reading(metrics, None, "worst")
    lead = worst["key"] if worst else None
    modes = ["worst", *(m["key"] for m in metrics or [])]
    order = [m for m in modes
             if m == "worst" or (m in keys and m != lead)]
    if mode not in order:
        return order[0] if order else "worst"
    return order[(order.index(mode) + 1) % len(order)]


def peek_base(side, height=None):
    """The sliver's paper: rounded only on the side facing the desktop, so it
    reads as something parked half off the screen."""
    height = BAR_H if height is None else height
    up = 4
    layer = Image.new("RGBA", (PEEK_W * up, height * up), (0, 0, 0, 0))
    radius = px(8) * up if T["radius"] else 0
    corners = ((True, False, False, True) if side == "right"
               else (False, True, True, False))
    ImageDraw.Draw(layer).rounded_rectangle(
        [0, 0, PEEK_W * up - 1, height * up - 1], radius=radius,
        fill=_tok("paper"), outline=_ink(20), width=max(1, px(1)) * up,
        corners=corners)
    return layer.resize((PEEK_W, height), Image.LANCZOS)


def render_peek(counts, metrics, front, now, side="right", mode="worst", header_frame=None):
    """What is left against the screen edge while the console is tucked.

    The group over the reading. The rows, the meta line and the counts are all
    off screen, so the sliver keeps the two things that make you look: who is
    running - the group takes the colour of the most urgent state present - and
    how close the leading limit is. The caret points back at the console, and
    clicking the number walks the readings, exactly as it does on the bar -
    anywhere else brings the console back."""
    lead = None
    for state in ("needs", "working", "done", "closed"):
        if counts.get(state):
            lead = state
            break
    chip = peek_base(side)
    pen = ImageDraw.Draw(chip)

    # the caret takes the edge the console went out by; the rest is the content
    caret_room = px(14)
    left = px(4) if side == "right" else caret_room
    right = PEEK_W - caret_room if side == "right" else PEEK_W - px(4)
    mid = (left + right) // 2

    cell = px(72)
    figure = agent_figure(artwork.HEADER, figure_actions.frame_at(now) if header_frame is None else header_frame,
                          state_colour(lead) if lead else _ink(52), cell, px(34))
    if figure is not None:
        chip.alpha_composite(figure, (mid - cell // 2, px(4)))

    shown = bar_reading(metrics, front, mode)
    pct = shown["pct"] if shown else 0
    tone = usage_tone(pct)
    big, small = FONT("bold", 14), FONT("bold", 8)
    name_font = FONT("bold", 9)
    value = "%d" % round(pct)
    try:
        adv = big.getbbox(value)[2]     # the advance carries a right bearing
    except AttributeError:
        adv = text_w(value, big)
    # the limit's name rides beside the number: at this width the reading is
    # only worth walking through if the sliver says which one you are on
    name = caps(shown["label"]) if shown else ""
    tracking = 0.0
    name_w = tracked_w(name, name_font, tracking) if name else 0
    unit_w = text_w("%", small)
    x = mid - (adv + unit_w + (px(5) + name_w if name else 0)) // 2
    top = px(42)
    pen.text((x, top), value, font=big, fill=tone)
    pen.text((x + adv, top + ascent(big) - ascent(small)), "%", font=small,
             fill=tone)
    if name:
        draw_tracked(pen, (x + adv + unit_w + px(5),
                           top + ascent(big) - ascent(name_font)),
                     name, name_font, _ink(62), tracking)

    # pointing the way the console will come back from, so it is drawn here
    # rather than through _chevron, which only knows open and shut
    arm = px(4)
    half = arm // 2
    step = -half if side == "right" else half
    cx = PEEK_W - px(9) if side == "right" else px(9)
    cy = BAR_H // 2
    width = max(1, px(1))
    pen.line([(cx - step, cy - arm), (cx + step, cy)], fill=_ink(78), width=width)
    pen.line([(cx + step, cy), (cx - step, cy + arm)], fill=_ink(78), width=width)

    # the number's half of the sliver walks the readings; the figure, the
    # caret and the paper around them bring the console back
    boxes = [("reading", left, px(38), right, BAR_H, None),
             ("peek", 0, 0, PEEK_W, BAR_H, None)]
    return with_shadow(chip), boxes


def render_header_numbers(pen, metrics, front, counts, mode, notice, reading_view="used"):
    """Align usage readings and agent counts in two fixed groups."""
    label_font = FONT("bold", 9)
    value_font = FONT("semi", 17)
    label_y, value_y = px(36), px(50)
    usage_right = px(138)
    shown = bar_reading(metrics, front, mode)
    if shown is not None:
        readings = list(metrics or [shown])
        if shown not in readings[:3]:
            readings = [shown] + [m for m in readings if m is not shown]
        column = (usage_right - PAD_X) // 3
        for index, metric in enumerate(readings[:3]):
            x = PAD_X + index * column
            room = column - px(5)
            pen.text((x, label_y), elide(caps(metric.get("header_label") or metric["label"]), label_font, room),
                     font=label_font, fill=_tok("fg") if metric is shown else _ink(62))
            value = header_reading_value(metric, reading_view)
            font = value_font
            for size in (16, 15, 14, 13, 12, 11):
                if text_w(value, font) <= room:
                    break
                font = FONT("semi", size)
            pen.text((x, value_y + ascent(value_font) - ascent(font)), value,
                     font=font, fill=usage_tone(metric["pct"]))
        hint = {"used": "Used · click →",
                "remaining": "Remaining · click →",
                "reset": "Resets · click →"}.get(reading_view, "Used")
        pen.text((PAD_X, px(74)), hint, font=FONT("book", 8), fill=_ink(62))
    else:
        kind, message = notice if notice else ("", "Usage offline")
        pen.text((PAD_X, label_y), caps("usage"), font=label_font, fill=_ink(62))
        pen.text((PAD_X, value_y), "--", font=value_font, fill=_ink(45))
        message_font = FONT("book", 9)
        pen.text((PAD_X + px(24), value_y + ascent(value_font) - ascent(message_font)),
                 elide(message, message_font, usage_right - PAD_X - px(28)),
                 font=message_font, fill=_tok("crit") if kind == "auth" else _ink(62))

    divider_x = px(142)
    pen.line([(divider_x, px(11)), (divider_x, BAR_H - px(11))],
             fill=_ink(13), width=max(1, px(0.5)))
    agents_left, agents_width = px(151), px(66)
    title = caps("agents")
    pen.text((agents_left + (agents_width - text_w(title, label_font)) // 2, px(24)),
             title, font=label_font, fill=_ink(62))
    colours = {"working": ("ff5f57", "e0443e"),
               "needs": ("ffbd2e", "dea123"),
               "done": ("28c840", "1aab29")}
    count_font = FONT("semi", 10)
    for index, state in enumerate(("working", "needs", "done")):
        cx = agents_left + (index * 2 + 1) * agents_width // 6
        count = counts.get(state, 0)
        fill, outline = (tuple(_rgb(value) for value in colours[state])
                         if count else (_ink(20), _ink(30)))
        pen.ellipse((cx - px(6), px(43), cx + px(6) - 1, px(55) - 1),
                    fill=fill, outline=outline, width=max(1, px(0.5)))
        text = str(count) if count < 100 else "99+"
        pen.text((cx - text_w(text, count_font) // 2, px(60)), text,
                 font=count_font, fill=_ink(78) if count else _ink(45))
    return shown is not None


@lru_cache(maxsize=16)
def smith_wordmark(width, height, monochrome_ink=None):
    """Keep the approved lettering and spacing identical on both platforms."""
    with Image.open(os.path.join(SCRIPT_DIR, "assets", "smith-wordmark-mask.png")) as source:
        mask = source.convert("L").resize((width, height), Image.Resampling.LANCZOS)
    logo = Image.new("RGBA", (width, height))
    if monochrome_ink is None:
        unit = width / 64
        for radius, colour, strength in ((3, (69, 217, 0), .25),
                                          (1.5, (98, 255, 0), .45),
                                          (.5, (139, 255, 54), .6)):
            halo = Image.new("RGBA", logo.size, colour + (0,))
            halo.putalpha(mask.filter(ImageFilter.GaussianBlur(radius * unit)).point(
                lambda value: round(value * strength)))
            logo.alpha_composite(halo)
    lettering = Image.new("RGBA", logo.size, monochrome_ink or (255, 254, 240, 255))
    lettering.putalpha(mask)
    logo.alpha_composite(lettering)
    return logo


def render_console_bar(chip, pen, metrics, front, spend, counts, now,
                       expanded, mode="worst", notice=None, header_frame=None, provider=None,
                       reading_view="used"):
    """Show usage columns, agent counts, figure, and fold/tuck controls.

    Clicking the usage group cycles used, remaining, and reset. Both platforms
    use the same layout; fonts and geometry follow the selected theme/scale.
    """
    boxes = []
    if expanded:
        band(pen, EDGE, BAR_H, _ink(5), corners=(True, True, False, False))

    # the figure takes the colour of the most urgent state present, so the
    # folded bar still says whether anything is waiting on you
    lead = None
    for state in ("needs", "working", "done", "closed"):
        if counts.get(state):
            lead = state
            break
    # with nothing running there is nothing to be urgent about, so the figure
    # goes quiet rather than taking the last state's colour
    lead_ink = state_colour(lead) if lead else _ink(52)
    # a narrower cell than the rows use: the bar has one figure and two lines of
    # numbers to fit, and the numbers are what it is for
    BAR_CELL = px(64)
    fig_x = CONSOLE_W - PAD_X - px(14) - BAR_CELL
    figure = agent_figure(artwork.HEADER, figure_actions.frame_at(now) if header_frame is None else header_frame,
                          lead_ink, BAR_CELL, CELL_H)
    if figure is not None:
        chip.alpha_composite(figure, (fig_x, (BAR_H - CELL_H) // 2 - px(4)))

    # The existing Agents tab supplies the second line of the name.
    # Keep the selfie at its full size and use the space beneath its baseline.
    logo = smith_wordmark(px(64), px(22), _tok("fg") if THEME_NAME == "eink" else None)
    logo_x = (CONSOLE_W // 3) * 2 + (CONSOLE_W // 3 - logo.width) // 2
    chip.alpha_composite(logo, (logo_x, BAR_H - logo.height))

    caret_x = CONSOLE_W - PAD_X - px(10)
    _chevron(pen, caret_x + px(5), BAR_H // 2,
             _tok("fg") if expanded else _ink(78), expanded)

    # the tuck line: a single stroke in the corner above the caret. The caret
    # folds the console, this puts it away against the screen edge.
    tuck_x = caret_x + px(5)
    tuck_y = px(9)
    pen.line([(tuck_x - px(4), tuck_y), (tuck_x + px(4), tuck_y)],
             fill=_ink(52), width=max(1, px(1)))

    has_reading = render_header_numbers(pen, metrics, front, counts, mode, notice, reading_view)
    if provider:
        boxes += render_provider_switch(chip, pen, provider)
    boxes.append(("tuck", tuck_x - px(9), 0, tuck_x + px(9), tuck_y + px(8), None))
    if has_reading:
        boxes.append(("reading", PAD_X, px(33), px(138), BAR_H - px(3), None))
    boxes.append(("bar", 0, 0, CONSOLE_W, BAR_H, None))
    return boxes


def render_tabs(pen, tab, needs):
    """Three equal columns. The active tab is ink with a 2px ink underline; the
    others sit at 78%."""
    boxes = []
    rule(pen, BAR_H, _ink(13))
    font = FONT("bold", 10)
    column = CONSOLE_W // 3
    for index, key in enumerate(TABS):
        label = TAB_LABEL[key]
        if key == "agents" and needs:
            label += " · %d" % needs
        label = caps(label)
        width = tracked_w(label, font, px(10) * 0.07)
        x = index * column + (column - width) // 2
        on = key == tab
        draw_tracked(pen, (x, BAR_H + px(8)), label, font,
                     _tok("fg") if on else _ink(78), px(10) * 0.07)
        if on:
            pen.rectangle([max(EDGE, index * column), BAR_H + TAB_H - px(2),
                           min(RULE_R, (index + 1) * column),
                           BAR_H + TAB_H - px(1)], fill=_tok("fg"))
        boxes.append(("tab:" + key, index * column, BAR_H,
                      (index + 1) * column, BAR_H + TAB_H, None))
    return boxes


USAGE_ROW_H = px(14)
USAGE_GAP = px(7)


def usage_height(metrics):
    rows = max(1, len(metrics))
    row_h = px(40) if any(m.get("provider") == "codex" for m in metrics) else USAGE_ROW_H
    return (px(10) + rows * row_h + (rows - 1) * USAGE_GAP
            + USAGE_GAP + px(6) + px(17) + px(11))


def render_usage(pen, metrics, spend, stats, y):
    """Session / Week / Opus: a 9px key, a 4px track, the percentage in mono
    and its reset. Fill and figure recolour at the handoff's thresholds."""
    rule(pen, y, _ink(13))
    key_font = FONT("bold", 9)
    pct_font = MONO("bold", 10)
    reset_font = MONO("book", 9)
    tracking = px(9) * 0.07

    key_w = px(52)
    pct_w = px(30)
    reset_w = px(40)
    track_x = PAD_X + key_w + USAGE_GAP
    track_w = (CONSOLE_W - PAD_X - reset_w - USAGE_GAP - pct_w - USAGE_GAP
               - track_x)

    row_y = y + px(10)
    if not metrics:
        pen.text((PAD_X, row_y + px(5)), "No usage reading available", font=FONT("book", 11), fill=_ink(62))
        row_y += USAGE_ROW_H + USAGE_GAP
    for metric in metrics or []:
        pct = max(0.0, min(100.0, metric["pct"]))
        colour = usage_tone(pct)
        is_codex = metric.get("provider") == "codex"
        row_h = px(40) if is_codex else USAGE_ROW_H
        mid = row_y + (px(27) if is_codex else row_h // 2)
        # the chip abbreviated these to fit 232px; the console has the room for
        # the name the handoff prints - Session, Week, and the scoped model
        name = {"session": "Session", "weekly": "Week"}.get(metric["key"],
                                                            metric["label"])
        if is_codex:
            pen.text((PAD_X, row_y + px(2)), elide(metric["detail"], FONT("book", 10), CONSOLE_W-2*PAD_X),
                     font=FONT("book", 10), fill=_ink(78))
        else:
            draw_tracked(pen, (PAD_X, mid - ascent(key_font) // 2),
                         caps(name), key_font, _ink(62), tracking)
        row_track_x = PAD_X if is_codex else track_x
        row_track_w = track_w + (track_x-PAD_X if is_codex else 0)
        bar_y = mid - px(2)
        pen.rectangle([row_track_x, bar_y, row_track_x + row_track_w, bar_y + px(4)],
                      fill=_ink(15))
        if pct > 0:
            pen.rectangle([row_track_x, bar_y,
                           row_track_x + int(row_track_w * pct / 100.0), bar_y + px(4)],
                          fill=colour)
        text = "%d%%" % round(pct)
        pen.text((track_x + track_w + USAGE_GAP + pct_w - text_w(text, pct_font),
                  mid - ascent(pct_font) // 2), text, font=pct_font, fill=colour)
        stamp = reset_stamp(metric.get("resets_at"))
        pen.text((CONSOLE_W - PAD_X - text_w(stamp, reset_font),
                  mid - ascent(reset_font) // 2), stamp, font=reset_font,
                 fill=_ink(62))
        row_y += row_h + USAGE_GAP

    row_y += USAGE_GAP - USAGE_GAP
    pen.line([(PAD_X, row_y), (CONSOLE_W - PAD_X, row_y)], fill=_ink(8))
    cost_font = MONO("bold", 13)
    note_font = FONT("book", 10)
    cost = spend["text"] if spend else "—"
    pen.text((PAD_X, row_y + px(6)), cost, font=cost_font, fill=_tok("fg"))
    note = []
    tokens = stats.get("tokens")
    note.append("%s tokens" % (format_tokens(tokens) if tokens is not None else "—"))
    if stats.get("provider") == "codex":
        note.append("%dd" % stats.get("days", 14))
    else:
        note.append("{:,} turns".format(stats.get("turns", 0)))
    text = " · ".join(note)
    right = CONSOLE_W - PAD_X
    left = PAD_X + text_w(cost, cost_font) + GAP
    pen.text((right - min(text_w(text, note_font), right - left),
              row_y + px(6) + ascent(cost_font) - ascent(note_font)),
             elide(text, note_font, right - left), font=note_font, fill=_ink(62))
    return []


STAT_ROW_H = px(31)
SPARK_H = px(24)


def stats_height():
    return (px(10) + STAT_ROW_H * 2 + px(8) + px(7) + px(7) + px(12) + px(5)
            + SPARK_H + px(11))


def render_stats(pen, stats, y):
    """A 3x2 grid of mono values over their keys, then a fortnight of turns as
    a sparkline with the peak bar in full ink."""
    rule(pen, y, _ink(13))
    val_font = MONO("bold", 14)
    key_font = FONT("bold", 9)
    tracking = px(9) * 0.07

    # Two of the handoff's six cells have no source in the transcripts - a
    # turn's wall-clock length and the prompts a session answered are not
    # recorded - so they carry what can be measured instead.
    cells = [
        ("Sessions", "%d" % stats.get("sessions", 0)),
        ("Turns", "{:,}".format(stats.get("turns", 0))),
        ("Tokens", format_tokens(stats.get("tokens") or 0)),
        ("Tool calls", "{:,}".format(stats.get("tools", 0))),
        ("Per turn", format_tokens(stats.get("per_turn", 0))),
        ("Projects", "%d" % stats.get("projects", 0)),
    ]
    if stats.get("provider") == "codex":
        def token_value(key):
            value = stats.get(key)
            return compact_tokens(value) if value is not None else "—"
        def days_value(key):
            value = stats.get(key)
            return "%dd" % value if value is not None else "—"
        seconds = stats.get("longest_turn_seconds")
        elapsed = ("%dm %02ds" % (seconds // 60, seconds % 60)) if seconds is not None else "—"
        cells = [("Tokens · %dd" % stats.get("days", 14), token_value("tokens")),
                 ("Lifetime", token_value("lifetime_tokens")), ("Peak day", token_value("peak_daily_tokens")),
                 ("Longest turn", elapsed), ("Streak", days_value("current_streak_days")),
                 ("Best streak", days_value("longest_streak_days"))]
    column = (CONSOLE_W - PAD_X * 2 - px(12)) // 3
    top = y + px(10)
    for index, (key, value) in enumerate(cells):
        cx = PAD_X + (index % 3) * (column + px(6))
        cy = top + (index // 3) * (STAT_ROW_H + px(8))
        pen.text((cx, cy), value, font=val_font, fill=_tok("fg"))
        draw_tracked(pen, (cx, cy + ascent(val_font) + px(4)), caps(key),
                     key_font, _ink(62), tracking)

    spark_y = top + STAT_ROW_H * 2 + px(8) + px(7)
    pen.line([(PAD_X, spark_y), (CONSOLE_W - PAD_X, spark_y)], fill=_ink(8))
    draw_tracked(pen, (PAD_X, spark_y + px(7)),
                 caps(("Tokens per day · %dd UTC" if stats.get("provider") == "codex" else "Turns per day · %dd") % stats.get("days", 14)),
                 key_font, _ink(62), tracking)

    values = stats.get("spark") or [0]
    if stats.get("spark_known") is False:
        pen.text((PAD_X, spark_y + px(25)), "Daily history unavailable", font=FONT("book", 10), fill=_ink(52))
        return []
    peak = max(values) or 1
    base = spark_y + px(7) + px(12) + px(5) + SPARK_H
    width = CONSOLE_W - PAD_X * 2
    step = width / float(len(values))
    bar_w = max(1, int(step) - px(2))
    for index, value in enumerate(values):
        height = max(px(2), int(round(value / float(peak) * SPARK_H)))
        x = PAD_X + int(index * step)
        pen.rectangle([x, base - height, x + bar_w, base],
                      fill=_tok("fg") if value == peak else _ink(50))
    return []


def compact_tokens(tokens):
    if tokens >= 1000000:
        return ("%.2f" % (tokens / 1000000)).rstrip("0").rstrip(".") + "M"
    if tokens >= 1000:
        return ("%.1f" % (tokens / 1000)).rstrip("0").rstrip(".") + "k"
    return str(tokens)


def panel_line_height(font):
    return sum(font.getmetrics())


@lru_cache(maxsize=512)
def panel_wrap(text, font, width, limit=None):
    """Wrap prose and unbroken paths to actual glyph widths, with a visible
    ellipsis when a bounded preview ends. Omit limit to show the full label.
    """
    rest = " ".join(str(text or "").split())
    if limit is not None:
        rest = rest[:2000]
    lines = []
    while rest and (limit is None or len(lines) < limit):
        if limit is not None and len(lines) == limit - 1:
            lines.append(elide(rest, font, width))
            break
        if text_w(rest, font) <= width:
            lines.append(rest)
            break
        low, high = 1, len(rest)
        while low < high:
            mid = (low + high + 1) // 2
            if text_w(rest[:mid], font) <= width:
                low = mid
            else:
                high = mid - 1
        cut = rest.rfind(" ", 0, low + 1)
        if limit is None:
            cut = max(cut, *(rest.rfind(separator, 0, low) + 1
                             for separator in ('-', '_', '/')))
        if cut <= low // 2:
            cut = low
        lines.append(rest[:cut])
        rest = rest[cut:].lstrip()
    return lines or ["—"]


def agent_row_layout(agent):
    title_font, small = FONT("semi", 12), FONT("book", 10)
    text_x = PAD_X + ROW_FIGURE_W + ROW_NAME_GAP + (px(10) if agent.get("sub") else 0)
    titles = panel_wrap(agent.get("name") or "Claude session", title_font,
                        CONSOLE_W - PAD_X - px(17) - text_x, 2)
    identity_h = max(ROW_FIGURE_H, len(titles) * panel_line_height(title_font)
                     + px(3) + panel_line_height(FONT("semi", 10)))
    context_y = px(8) + identity_h + px(6) + panel_line_height(small) + px(4)
    track_y = context_y + panel_line_height(small) + px(2)
    state_y = track_y + px(3) + px(6)
    activity_y = state_y + panel_line_height(small) + px(2)
    height = max(ROW_H, activity_y + panel_line_height(FONT("book", 11)) + px(7))
    if agent.get("sub") and agent.get("activity"):
        lines = panel_wrap(agent_activity(agent), FONT("book", 11), CONSOLE_W - PAD_X * 2, 2)
        height += (len(lines)-1) * panel_line_height(FONT("book", 11)) + panel_line_height(small) + px(7)
    if (agent.get("permissions") or [{}])[0].get("actionable"):
        height += panel_line_height(FONT("semi", 10)) + px(8)
    return titles, text_x, context_y, track_y, state_y, activity_y, height


def agent_row_height(agent):
    return agent_row_layout(agent)[-1]


def provider_name(agent):
    return "Codex" if agent.get("provider") == "codex" else "Claude"


@lru_cache(maxsize=24)
def _provider_logo(provider, size):
    if provider not in ("claude", "codex"):
        return None
    try:
        with Image.open(os.path.join(SCRIPT_DIR, "assets", "providers", provider + ".png")) as source:
            return source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    except OSError:
        return None


def draw_provider_badge(chip, agent, left, top):
    """Overlay a top-left badge without changing the figure's cell or scale."""
    provider = agent.get("provider") or ("codex" if str(agent.get("entrypoint", "")).startswith("codex-") else "claude")
    logo = _provider_logo(provider, px(16))
    if logo is None:
        return False
    chip.alpha_composite(logo, (left, top))
    return True


def render_provider_switch(chip, pen, provider):
    boxes = []
    top, height, width = px(8), px(22), px(63)
    for index, name in enumerate(("claude", "codex")):
        x = PAD_X + index * width
        if name == provider:
            pen.rounded_rectangle((x, top, x+width-px(3), top+height), radius=px(3), fill=_ink(12))
        logo = _provider_logo(name, px(18))
        if logo:
            chip.alpha_composite(logo, (x+px(2), top+px(2)))
        pen.text((x+px(23), top+px(6)), name.title(), font=FONT("semi", 8), fill=_ink(88 if name == provider else 52))
        boxes.append(("provider:"+name, x, top, x+width-px(3), top+height, None))
    return boxes


def agent_window_action(agent):
    kind = 'hide' if agent.get('_window_open') else 'open'
    target = ('window' if agent.get('provider') == 'codex' else
              'chat' if agent.get('entrypoint') == 'claude-vscode' else 'terminal')
    if agent.get('sub'):
        target = 'parent window'
    return kind, ('Hide ' if kind == 'hide' else 'Open ') + target


def agent_window_label(agent):
    sub = agent.get('sub')
    role = 'Subagent' if sub else 'Main agent'
    if agent.get('state') == 'closed':
        return role + ' · Session closed'
    window = 'Parent window' if sub else 'Window'
    state = {'front': 'in front', 'background': 'in background',
             'hidden': 'hidden'}.get(agent.get('_window_state'), 'unknown')
    return role + ' · ' + window + ' ' + state


def _window_action_icon(pen, x, y, kind, colour):
    if kind == 'hide':
        pen.line((x, y + px(5), x + px(7), y + px(5)), fill=colour, width=max(1, px(1)))
    else:
        _arrow(pen, x, y, colour)


def render_row(pen, chip, agent, y, now, open_, confirm, figure_elapsed=None, draw_figure=True):
    """Compact identity, then full-width context, state/time, and activity."""
    boxes = []
    titles, text_x, context_y, track_y, state_y, activity_y, height = agent_row_layout(agent)
    small, title_font = FONT("book", 10), FONT("semi", 12)
    right = CONSOLE_W - PAD_X
    colour = state_colour(agent.get("state"))
    rule(pen, y, _ink(13))
    if agent.get("state") == "needs" or open_:
        band(pen, y + px(1), y + height, _tint(colour, 6) if agent.get("state") == "needs" else _ink(5))
        pen.rectangle([EDGE, y + px(1), EDGE + px(2) - 1, y + height], fill=colour)
    figure = (row_figure(agent, now if figure_elapsed is None else figure_elapsed,
                         ROW_FIGURE_W, ROW_FIGURE_H) if draw_figure else None)
    if figure is not None:
        chip.alpha_composite(figure, (PAD_X, y + px(8)))
    has_badge = draw_provider_badge(chip, agent, PAD_X - px(6), y + px(2))
    if confirm:
        pen.text((text_x, y + px(14)), "End this session?", font=title_font, fill=_tok("fg"))
        pen.text((PAD_X, y + state_y), "This stops the running " + provider_name(agent) + " session.", font=small, fill=_ink(78))
        for kind, label, x, tone in (("yes", "End session", PAD_X, _tok("crit")),
                                     ("no", "Cancel", right - text_w("Cancel", small), _ink(78))):
            box = _link(pen, x, y + activity_y, label, small, tone, True)
            boxes.append((kind,) + box + (agent,))
        return boxes
    model_font = FONT("semi", 10)
    title_block_h = len(titles) * panel_line_height(title_font) + px(3) + panel_line_height(model_font)
    ty = y + px(8) + max(0, (ROW_FIGURE_H - title_block_h) // 2)
    if agent.get("sub"):
        pen.line([(text_x - px(7), ty), (text_x - px(7), ty + px(8)),
                  (text_x - px(2), ty + px(8))], fill=_ink(52), width=max(1, px(1)))
    for line in titles:
        pen.text((text_x, ty), line, font=title_font, fill=_ink(62) if agent.get("state") == "closed" else _tok("fg"))
        ty += panel_line_height(title_font)
    pen.text((text_x, ty + px(3)), elide(agent_model_source(agent, include_provider=not has_badge), model_font, right - px(17) - text_x),
             font=model_font, fill=_ink(88))
    _chevron(pen, right - px(5), y + px(25), _ink(62), open_)

    window_y = y + context_y - panel_line_height(small) - px(4)
    pen.text((PAD_X, window_y), elide(agent_window_label(agent), small, right - PAD_X),
             font=small, fill=_ink(78))

    tokens, capacity = agent.get("context_tokens"), agent.get("context_capacity")
    pct = tokens / capacity * 100 if capacity and tokens is not None else None
    label = "Context" + ("  %.1f%%" % pct if pct is not None else "")
    value = (compact_tokens(tokens) if tokens is not None else "—") + " / " + (compact_tokens(capacity) if capacity else "?")
    value_x = right - text_w(value, small)
    pen.text((PAD_X, y + context_y), elide(label, small, value_x - PAD_X - px(8)), font=small, fill=_ink(62))
    pen.text((value_x, y + context_y), value, font=small, fill=_ink(78))
    pen.rounded_rectangle((PAD_X, y + track_y, right, y + track_y + px(3)), radius=px(1), fill=_ink(13))
    if pct is not None and pct > 0:
        tone = _tok("crit") if pct >= 90 else state_colour("needs") if pct >= 75 else colour
        pen.rectangle((PAD_X, y + track_y, PAD_X + max(1, round((right - PAD_X) * min(pct, 100) / 100)),
                       y + track_y + px(3)), fill=tone)
    stamp = agent_activity_stamp(agent)
    stamp_x = right - text_w(stamp, small)
    pen.text((stamp_x, y + state_y), stamp, font=small, fill=_ink(62))
    pen.ellipse((PAD_X, y + state_y + px(4), PAD_X + px(5), y + state_y + px(9)), fill=colour)
    status = elide(agent_status(agent), small, stamp_x - PAD_X - px(18))
    if (agent.get("permissions") or [{}])[0].get("actionable"):
        box = _link(pen, PAD_X + px(10), y + state_y, status, small, colour, True)
        boxes.append(("permission",) + box + (agent,))
    else:
        pen.text((PAD_X + px(10), y + state_y), status, font=small, fill=colour)
    body = FONT("book", 11)
    link_font = FONT("semi", 10)
    action_kind, open_label = agent_window_action(agent)
    rich = agent.get("sub") and agent.get("activity")
    activity_lines = panel_wrap(agent_activity(agent), body, right - PAD_X, 2) if rich else []
    counts_y = y + activity_y + len(activity_lines) * panel_line_height(body) + px(4)
    open_x = right - px(12) - text_w(open_label, link_font)
    open_y = (counts_y if rich else y + activity_y) + ascent(body) - ascent(link_font)
    box = _link(pen, open_x, open_y, open_label, link_font, _tok("fg"), True)
    _window_action_icon(pen, right - px(7), open_y + px(1), action_kind, _tok("fg"))
    # Actions must be tested before the enclosing row's expand/collapse box.
    boxes.append((action_kind, box[0], box[1], right + px(4), box[3], agent))
    if rich:
        for index, line in enumerate(activity_lines):
            pen.text((PAD_X, y + activity_y + index * panel_line_height(body)), line, font=body, fill=_ink(88))
        pen.text((PAD_X, counts_y), elide(agent_work_counts(agent), small, open_x - PAD_X - px(8)), font=small, fill=_ink(62))
    else:
        pen.text((PAD_X, y + activity_y),
                 elide(agent_activity(agent), body, open_x - PAD_X - px(12)), font=body, fill=_ink(78))
    if (agent.get("permissions") or [{}])[0].get("actionable"):
        permission_y = (counts_y + panel_line_height(small) if rich else y + activity_y + panel_line_height(body)) + px(7)
        for kind, label, x in (("permission", "Allow…", PAD_X),
                               ("permission-deny", "Deny", right - text_w("Deny", link_font))):
            box = _link(pen, x, permission_y, label, link_font, colour, True)
            boxes.append((kind,) + box + (agent,))
    boxes.append(("row", 0, y, CONSOLE_W, y + height, agent))
    return boxes


def agent_detail_label_width():
    # Size the key column from the actual theme/system font, not a guessed
    # width that truncates short labels on another platform.
    font = FONT("bold", 9)
    return math.ceil(max(text_w(caps(title), font) for title in
                         ("Last request", "Git branch", "Project folder"))) + px(8)


def agent_drawer_layout(agent):
    """Measure the same blocks that are drawn; missing/short text wastes no rows."""
    body, label = FONT("book", 11), FONT("bold", 9)
    width = CONSOLE_W - PAD_X * 2
    blocks, top = [], px(8)
    permissions = agent.get("permissions") or []
    fields = []
    if permissions:
        request = permissions[0]["request"]
        fields.append(("Permission requested · " + request["tool_name"],
                       json.dumps(request.get("input"), ensure_ascii=False, indent=2), 3, "permission"))
    fields.extend([
        ("Last request", agent.get("last_request") or "Not in recent history", 3, "inline"),
        ("timing", "", 1, "timing"),
        ("Git branch", agent.get("branch") or "Not available", 2, "inline"),
        ("Project folder", agent.get("cwd") or "Not available", 3, "inline"),
        ("Latest message", agent.get("latest_message") or "Not in recent history", 3, "text"),
    ])
    if agent.get("sub") and agent.get("activity"):
        data = agent["activity"]
        fields = fields[:1] if permissions else []
        fields.extend([
            ("Assigned task", agent.get("assigned_task") or "Assignment unavailable", 5, "text"),
            ("timing", "", 1, "timing"),
            ("Work", agent_work_counts(agent), 2, "text"),
            ("Final reply" if data.get("status") in ("completed", "failed", "stopped") else "Latest message",
             data.get("result") or agent.get("latest_message") or "No message recorded yet", 6, "text"),
        ])
        if data.get("last_output"):
            fields.append(("Tool output", (data.get("output_tool") or "Tool") + "\n" + data["last_output"], 12, "output"))
        elif data.get("last_tool"):
            fields.append(("Tool activity", agent_activity(agent), 4, "text"))
    for title, value, limit, kind in fields:
        inset = px(8) if kind == "permission" else 0
        value_width = width - inset * 2 - (agent_detail_label_width() if kind == "inline" else 0)
        if kind == "output":
            command, _, value = value.partition("\n")
            heading = panel_wrap(command, body, value_width, 2)
            output_lines = []
            for line in value.splitlines():
                output_lines.extend(panel_wrap(line or " ", body, value_width, limit))
            lines = heading + output_lines[-(limit-len(heading)):]
        else:
            lines = panel_wrap(value, body, value_width, limit)
        height = len(lines) * panel_line_height(body)
        if kind != "inline":
            height += panel_line_height(label) + px(4)
        blocks.append((title, lines, kind, top, height + inset * 2))
        top += height + inset * 2 + px(8)
    # A separate permission action line leaves Open chat available as well.
    footer_h = px(55) if permissions and permissions[0].get("actionable") else px(30)
    return blocks, top, top + footer_h


def agent_drawer_height(agent):
    return agent_drawer_layout(agent)[-1]


def render_drawer(pen, agent, y, now=None):
    blocks, foot, height = agent_drawer_layout(agent)
    body, label = FONT("book", 11), FONT("bold", 9)
    right = CONSOLE_W - PAD_X
    boxes = []
    rule(pen, y, _ink(8))
    band(pen, y + px(1), y + height, _ink(3))
    for title, lines, kind, top, block_h in blocks:
        ty, left = y + top, PAD_X
        if kind == "permission":
            pen.rounded_rectangle((left, ty, right, ty + block_h), radius=px(5),
                                  fill=_tint(state_colour("needs"), 5), outline=_tint(state_colour("needs"), 25))
            ty += px(8)
            left += px(8)
        if kind == "timing":
            since = agent.get("since")
            ended = (agent.get("activity") or {}).get("ended_at")
            age = agent_elapsed({"idle": max(0, (ended or (now if now is not None else time.time())) - since)}) if since else "Unknown"
            idle = agent_elapsed(agent)
            last = ("Just now" if agent.get("idle", 999) is not None and agent.get("idle", 999) < 5 else idle + " ago") if idle else "Unknown"
            for x, heading, value in ((left, "Task time" if agent.get("sub") else "Session age", age), (CONSOLE_W // 2 + px(8), "Last activity", last)):
                pen.text((x, ty), caps(heading), font=label, fill=_ink(52))
                pen.text((x, ty + panel_line_height(label) + px(4)), value, font=body, fill=_ink(78))
            continue
        if kind == "inline":
            pen.text((left, ty + ascent(body) - ascent(label)),
                     elide(caps(title), label, agent_detail_label_width() - px(8)), font=label, fill=_ink(52))
            left += agent_detail_label_width()
        else:
            pen.text((left, ty), elide(caps(title), label, right - left - (px(8) if kind == "permission" else 0)),
                     font=label, fill=_ink(52))
            ty += panel_line_height(label) + px(4)
        for line in lines:
            pen.text((left, ty), line, font=body, fill=_ink(78))
            ty += panel_line_height(body)
    fy = y + foot
    pen.line((PAD_X, fy - px(2), right, fy - px(2)), fill=_ink(13))
    permissions = agent.get("permissions") or []
    link_font = FONT("semi", 11)
    if permissions and permissions[0].get("actionable"):
        for kind, text, x in (("permission", "Review & allow…", PAD_X),
                              ("permission-deny", "Deny", right - text_w("Deny", link_font))):
            box = _link(pen, x, fy + px(6), text, link_font, state_colour("needs"), True)
            boxes.append((kind,) + box + (agent,))
        fy += px(25)
    action_kind, open_label = agent_window_action(agent)
    box = _link(pen, PAD_X, fy + px(6), open_label, link_font, _tok("fg"), True)
    _window_action_icon(pen, box[2] + px(3), fy + px(7), action_kind, _tok("fg"))
    boxes.append((action_kind, box[0], box[1], box[2] + px(11), box[3], agent))
    if not agent.get("sub") and (agent.get("state") == "closed" or agent.get("can_terminate", True)):
        text = "Dismiss" if agent.get("state") == "closed" else "End session…"
        box = _link(pen, right - text_w(text, link_font), fy + px(6), text, link_font, _ink(62))
        boxes.append(("kill",) + box + (agent,))
    elif not agent.get("sub"):
        text = "End in " + provider_name(agent)
        pen.text((right - text_w(text, link_font), fy + px(6)), text, font=link_font, fill=_ink(52))
    return boxes


def agents_height(agents, open_id):
    if not agents:
        return ROW_H + FOOT_H
    return sum(agent_row_height(a) + (agent_drawer_height(a) if a.get("id") == open_id else 0)
               for a in agents) + FOOT_H


def render_console(metrics, spend, stats, agents, now, tab="agents",
                   open_id=None, confirm_id=None, expanded=True,
                   bar_mode="worst", notice=None, max_height=None, scroll=0,
                   figure_elapsed=None, header_frame=None, provider=None, data_notice=None,
                   reading_view="used"):
    """The whole shell: the bar, and when it is open the tabs and one pane."""
    front = metrics[0] if metrics else None
    rows = sort_agents(agents)
    counts = {}
    for agent in rows:
        if not agent.get("sub"):
            counts[agent["state"]] = counts.get(agent["state"], 0) + 1

    height = BAR_H
    if expanded:
        height += TAB_H
        if tab == "usage":
            height += usage_height(metrics)
        elif tab == "stats":
            height += stats_height()
        else:
            height += agents_height(rows, open_id)
        if data_notice and tab in ("usage", "stats"):
            height += px(26)

    overflow = expanded and tab == "agents" and max_height is not None and height > max_height
    visible_top, visible_bottom = BAR_H + TAB_H, height - FOOT_H
    if overflow:
        viewport_height = max(visible_top + FOOT_H + px(40), int(max_height))
        offset = max(0, min(int(scroll), height - viewport_height))
        visible_top += offset
        visible_bottom = viewport_height - FOOT_H + offset
    chip = console_base((CONSOLE_W, height))
    pen = ImageDraw.Draw(chip)
    boxes = render_console_bar(chip, pen, metrics, front, spend, counts, now,
                               expanded, bar_mode, notice, header_frame, provider, reading_view)
    if not expanded:
        return with_shadow(chip), boxes

    boxes += render_tabs(pen, tab, counts.get("needs", 0))
    y = BAR_H + TAB_H
    if data_notice and tab in ("usage", "stats"):
        pen.text((PAD_X, y+px(6)), elide(data_notice, FONT("book", 10), CONSOLE_W-2*PAD_X),
                 font=FONT("book", 10), fill=_ink(62))
        y += px(26)
    if tab == "usage":
        boxes += render_usage(pen, metrics, spend, stats, y)
    elif tab == "stats":
        boxes += render_stats(pen, stats, y)
    else:
        if not rows:
            rule(pen, y, _ink(13))
            empty_font = FONT("book", 10)
            pen.text((PAD_X, y + (ROW_H - ascent(empty_font)) // 2),
                     "No agents running.", font=empty_font, fill=_ink(52))
            y += ROW_H
        for agent in rows:
            row_height = agent_row_height(agent)
            if y < visible_bottom and y + row_height > visible_top:
                figure_visible = y + px(8) < visible_bottom and y + px(8) + ROW_FIGURE_H > visible_top
                elapsed = figure_elapsed(agent) if figure_visible and figure_elapsed else None
                boxes += render_row(pen, chip, agent, y, now,
                                    agent.get("id") == open_id,
                                    confirm_id is not None and agent.get("id") == confirm_id,
                                    figure_elapsed=elapsed, draw_figure=figure_visible)
            y += row_height
            if agent.get("id") == open_id:
                drawer_height = agent_drawer_height(agent)
                if y < visible_bottom and y + drawer_height > visible_top:
                    boxes += render_drawer(pen, agent, y, now)
                y += drawer_height
        rule(pen, y, _ink(13))
        band(pen, y + px(1), y + FOOT_H - px(2), _ink(5),
             corners=(False, False, True, True))
        live = sum(1 for a in rows if not a.get("sub"))
        foot_font = FONT("book", 10)
        pen.text((PAD_X, y + px(7)),
                 "%d session%s" % (live, "" if live == 1 else "s"),
                 font=foot_font, fill=_ink(62))
        # closed rows linger for half an hour so you can see the one you just
        # ended. This is how you stop looking at the rest of them.
        shut = sum(1 for a in rows
                   if a.get("state") == "closed" and not a.get("sub"))
        if shut:
            label = "Clear %d closed" % shut
            link = _link(pen, CONSOLE_W - PAD_X - (px(65) if overflow else 0) - text_w(label, foot_font),
                         y + px(7), label, foot_font, _ink(78), True)
            boxes.append(("clear",) + link + (None,))

    if overflow:
        chip, boxes = fit_agent_viewport(chip, boxes, max_height, scroll)
    return with_shadow(chip), boxes


def fit_agent_viewport(chip, boxes, max_height, scroll):
    """Keep the header/footer fixed; clipped actions must never be clickable.
    Wheel gestures and footer buttons share this viewport on both platforms.
    """
    top = BAR_H + TAB_H
    height = max(top + FOOT_H + px(40), int(max_height))
    bottom = height - FOOT_H
    old_bottom = chip.height - FOOT_H
    offset = max(0, min(int(scroll), old_bottom - bottom))
    result = console_base((CONSOLE_W, height))
    result.paste(chip.crop((0, 0, CONSOLE_W, top)), (0, 0))
    result.paste(chip.crop((0, top + offset, CONSOLE_W, bottom + offset)), (0, top))
    result.paste(chip.crop((0, old_bottom, CONSOLE_W, chip.height)), (0, bottom))
    visible = []
    for kind, x0, y0, x1, y1, agent in boxes:
        if y1 <= top:
            visible.append((kind, x0, y0, x1, y1, agent))
        elif y0 >= old_bottom:
            visible.append((kind, x0, y0 - old_bottom + bottom, x1, y1 - old_bottom + bottom, agent))
        else:
            y0, y1 = y0 - offset, y1 - offset
            if kind == "row":
                y0, y1 = max(top, y0), min(bottom, y1)
            if top <= y0 < y1 <= bottom:
                visible.append((kind, x0, y0, x1, y1, agent))
    pen = ImageDraw.Draw(result)
    for kind, text, x, enabled in (("scroll-up", "Up", CONSOLE_W - PAD_X - px(52), offset > 0),
                                  ("scroll-down", "Down", CONSOLE_W - PAD_X - px(25), offset < old_bottom - bottom)):
        box = _link(pen, x, bottom + px(7), text, FONT("book", 9), _ink(78) if enabled else _ink(30))
        if enabled:
            visible.insert(0, (kind,) + box + (None,))
    # A narrow thumb indicates position without consuming a text column.
    thumb_h = max(px(15), round((bottom - top) ** 2 / (old_bottom - top)))
    thumb_y = top + round((bottom - top - thumb_h) * offset / (old_bottom - bottom))
    pen.rounded_rectangle((CONSOLE_W - px(4), thumb_y, CONSOLE_W - px(2), thumb_y + thumb_h),
                          radius=max(1, px(1)), fill=_ink(30))
    return result, visible


def _rect_trace(pen, box, fraction, colour, width):
    """Walk a rectangle's perimeter clockwise from the top-left for `fraction`
    of its length. The Matrix tray's notch is a dashed square, not an arc."""
    x0, y0, x1, y1 = box
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    lengths = [abs(corners[i + 1][0] - corners[i][0]) + abs(corners[i + 1][1] - corners[i][1])
               for i in range(4)]
    remaining = sum(lengths) * max(0.0, min(1.0, fraction))
    for i in range(4):
        if remaining <= 0:
            break
        run = min(remaining, lengths[i])
        (ax, ay), (bx, by) = corners[i], corners[i + 1]
        t = run / lengths[i] if lengths[i] else 0
        pen.line([ax, ay, ax + (bx - ax) * t, ay + (by - ay) * t],
                 fill=colour, width=int(width))
        remaining -= run


_MARK_CACHE = {}


def tray_size():
    return platform.tray_size()



def render_tray(front, live, error):
    """Render the approved silhouette at the native tray size, with smooth edges.

    AppKit uses the alpha as a light/dark template. Windows keeps the white
    silhouette; the existing critical-usage alert still turns it red.
    """
    size = tray_size()
    pct = front["pct"] if front else 0.0
    alarm = bool(error and not front) or severity_of(
        pct, front["severity"] if front else "normal") == "crit"
    hit = _MARK_CACHE.get((size, alarm))
    if hit is not None:
        return hit.copy()
    icon = artwork.tray_icon(size, meter_color("crit") if alarm else "white")
    if len(_MARK_CACHE) > 6:
        _MARK_CACHE.clear()
    _MARK_CACHE[(size, alarm)] = icon
    return icon.copy()


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

def log_line(text):
    """Append one line to the widget's log.

    Under pythonw there is no stderr, so a traceback goes nowhere and the
    window simply disappears with nothing to look at afterwards. Every start,
    every clean exit and every crash lands here instead."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 256 * 1024:
            with open(LOG_PATH, encoding="utf-8", errors="replace") as fh:
                tail = fh.read()[-64 * 1024:]
            with open(LOG_PATH, "w", encoding="utf-8") as fh:
                fh.write(tail)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except OSError:
        pass

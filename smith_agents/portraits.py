"""Animated code portraits for the Matrix theme's session figures.

A Pillow port of the approved browser reference
(output/imagegen/generated-pixel-code/animate.js and the native widget mockup).
The artwork stays stationary; motion comes from a multiplicative light mask of
descending columns. Each portrait is then brightened by 1.25 and screened over
the widget, as the mockup's CSS ``filter:brightness(1.25)`` and
``mix-blend-mode:screen`` do.

Face assignment: every main session is Smith. Helpers draw from Brown, Jones,
Johnson, Jackson and Thompson, least used first among the helpers currently
listed, ties broken by a hash of the session id. A face stays with its session
through state changes, rescans and reordering, until the session leaves the
list. With more than five helpers the faces repeat, still least used first.
"""
import json
import math
import zlib
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent / "assets" / "portraits"
MAIN = "smith"
HELPERS = ("brown", "jones", "johnson", "jackson", "thompson")
NAMES = (MAIN,) + HELPERS

WORK = 768
COMPACT_MAX = 104
LOW = 192  # '#c0c0c0', the compact mask's resting light
BOOST = 1.55
FINAL_BRIGHTNESS = 1.25
MAX_DT = 0.1
SEED = 14731
# The reference seeds one generator per portrait and walks its 40 and 42 pixel
# views in that order, so the 42 pixel columns follow the 40 pixel ones.
SEED_ORDER = (40, 42)

PALETTE = {"green": None, "yellow": (255, 224, 64), "red": (255, 64, 64),
           "white": (255, 255, 255)}
ALIASES = {"gray": "white", "grey": "white"}
RATES = {"green": 0.1, "yellow": 1.0, "red": 2.5, "white": 0.0}
STATE_COLOR = {"done": "green", "needs": "yellow", "working": "red", "closed": "white"}


def colour(name):
    name = ALIASES.get(name, name)
    return name if name in PALETTE else "green"


def state_colour(state):
    return STATE_COLOR.get(state, "red")


def rate(state, reduced=False):
    return 0.0 if reduced else RATES[state_colour(state)]


@lru_cache(maxsize=1)
def manifest():
    with open(ROOT / "manifest.json", encoding="utf-8") as handle:
        return json.load(handle)


def default_face(agent):
    if not agent.get("sub"):
        return MAIN
    identity = agent.get("id") or agent.get("name") or ""
    return HELPERS[zlib.crc32(identity.encode("utf-8")) % len(HELPERS)]


def face(agent):
    name = agent.get("_portrait")
    return name if name in NAMES else default_face(agent)


class Assignments:
    """Stable faces: kept per session until it leaves the list."""
    def __init__(self):
        self.choices = {}

    def assign(self, agents):
        identities = {agent.get("id") or agent.get("name") or "" for agent in agents}
        self.choices = {key: name for key, name in self.choices.items() if key in identities}
        counts = {name: 0 for name in HELPERS}
        pending = []
        for agent in agents:
            identity = agent.get("id") or agent.get("name") or ""
            if not agent.get("sub"):
                self.choices[identity] = MAIN
            elif self.choices.get(identity) in HELPERS:
                counts[self.choices[identity]] += 1
            else:
                pending.append(identity)
        # Input order must not decide a new batch.
        for identity in sorted(set(pending)):
            least = min(counts.values())
            candidates = [name for name in HELPERS if counts[name] == least]
            name = candidates[zlib.crc32(identity.encode("utf-8")) % len(candidates)]
            self.choices[identity] = name
            counts[name] += 1
        for agent in agents:
            agent["_portrait"] = self.choices[agent.get("id") or agent.get("name") or ""]


class Clock:
    """Accumulated motion time per session, advanced at its state's rate.

    Time never resets on a state change, rescan or layout change; a frame gap
    counts for at most 0.1 seconds, so a portrait that was hidden or scrolled
    away resumes where it stopped instead of jumping."""
    def __init__(self):
        self.times = {}

    def advance(self, identity, rate_, now):
        elapsed, last = self.times.get(identity, (0.0, None))
        if last is not None and rate_ > 0:
            elapsed += min(max(0.0, now - last), MAX_DT) * rate_
        self.times[identity] = (elapsed, now)
        return elapsed

    def sync(self, identities):
        self.times = {key: value for key, value in self.times.items() if key in identities}


@lru_cache(maxsize=len(NAMES))
def _source(name):
    """The approved image on the reference's 768 working surface, and its crop."""
    with Image.open(ROOT / manifest()[name]["image"]) as image:
        work = image.convert("RGB").resize((WORK, WORK), Image.Resampling.BILINEAR)
    box = work.getchannel("G").point(lambda v: 255 if v > 40 else 0).getbbox()
    left, top, right, bottom = box[0], box[1], box[2]-1, box[3]-1
    extent = min(WORK, max(right-left+1, bottom-top+1) * 1.08)
    x = max(0, min(WORK-extent, (left+right-extent) / 2))
    y = max(0, min(WORK-extent, (top+bottom-extent) / 2))
    return work, (x, y, x+extent, y+extent)


@lru_cache(maxsize=48)
def _untinted(name, n, resolution):
    """Stationary, untinted artwork at display resolution: halve in stages,
    resize once, then lift compact sizes by 1.55. Never resampled per frame."""
    work, crop = _source(name)
    compact = n <= COMPACT_MAX
    base = work.resize((WORK, WORK), Image.Resampling.BILINEAR, box=crop) if compact else work
    while base.width > resolution * 2:
        half = -(-base.width // 2)
        base = base.resize((half, half), Image.Resampling.BOX)
    base = base.resize((resolution, resolution), Image.Resampling.BICUBIC)
    if compact:
        base = base.point([min(255, round(v * BOOST)) for v in range(256)] * 3)
    return base


@lru_cache(maxsize=96)
def base(name, n, resolution, tint):
    """Tinted from the immutable untinted base, so repeated colour changes
    cannot accumulate."""
    original = _untinted(name, n, resolution)
    rgb = PALETTE[colour(tint)]
    if rgb is None:
        return original
    r, g, b = original.split()
    peak = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return Image.merge("RGB", [peak.point([round(v * c / 255) for v in range(256)]) for c in rgb])


def _random(state):
    state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
    return state, state / 4294967296


def _pitch(n):
    return max(3, n / 18)


@lru_cache(maxsize=16)
def columns(n):
    """Column offsets, rates and tails, drawn from the reference's seed."""
    state = SEED
    sizes = [size for size in SEED_ORDER if size <= n] if n in SEED_ORDER else [n]
    result = ()
    for size in sizes:
        result = []
        for _ in range(math.ceil(size / _pitch(size))):
            state, offset = _random(state)
            state, speed = _random(state)
            state, tail = _random(state)
            state, _phase = _random(state)
            result.append((offset * size, 0.7 + speed * 0.6, size * (0.28 + tail * 0.2)))
    return tuple(result)


def _tip(n):
    return max(2, n * 0.06)


@lru_cache(maxsize=64)
def _trail(n, resolution, index):
    """One column's gradient in device pixels: low, 216 at 65%, white at 90%,
    back to low at the tip."""
    scale = resolution / n
    tail = columns(n)[index][2]
    length = tail + _tip(n)
    height = max(1, round(length * scale))
    stops = ((0, LOW), (0.65, 216), (0.9, 255), (1, LOW))
    values = []
    for row in range(height):
        t = (row + 0.5) / height
        for (t0, v0), (t1, v1) in zip(stops, stops[1:]):
            if t <= t1:
                values.append(round(v0 + (v1 - v0) * (t - t0) / (t1 - t0)))
                break
    strip = Image.new("L", (1, height))
    strip.putdata(values)
    return strip


def light(n, resolution, time):
    """The multiplicative light mask at ``time`` seconds of motion."""
    scale = resolution / n
    cols = columns(n)
    tip = _tip(n)
    mask = Image.new("L", (len(cols), resolution), LOW)
    for index, (offset, speed, tail) in enumerate(cols):
        cycle = n + tail
        head = (time * n * 0.24 * speed + offset) % cycle
        strip = _trail(n, resolution, index)
        for repeat in (-1, 0, 1):
            top = round((head + repeat * cycle - tail) * scale)
            if top < resolution and top + strip.height > 0:
                mask.paste(strip, (index, top))
    width = max(resolution, round(len(cols) * _pitch(n) * scale))
    return mask.resize((width, resolution), Image.Resampling.NEAREST).crop((0, 0, resolution, resolution))


_BRIGHT = [min(255, round(v * FINAL_BRIGHTNESS)) for v in range(256)] * 3
_FRAMES = OrderedDict()
_FRAME_LIMIT = 64


def frame(name, n, resolution, tint, time):
    """One composited portrait (RGB on black), ready to screen over the widget."""
    key = (name, n, resolution, colour(tint), round(time, 4))
    hit = _FRAMES.get(key)
    if hit is not None:
        _FRAMES.move_to_end(key)
        return hit
    tinted = base(name, n, resolution, colour(tint))
    lit = ImageChops.multiply(tinted, light(n, resolution, time).convert("RGB"))
    result = lit.point(_BRIGHT)
    _FRAMES[key] = result
    if len(_FRAMES) > _FRAME_LIMIT:
        _FRAMES.popitem(last=False)
    return result


def cell(agent, time, cell_w, cell_h, unit):
    """The portrait in a figure cell: square, centred, top aligned. ``unit`` is
    device pixels per logical pixel. Marked for screen blending."""
    side = min(cell_w, cell_h)
    n = max(1, round(side / unit))
    image = frame(face(agent), n, side, state_colour(agent.get("state")), time)
    result = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 0))
    result.paste(image, ((cell_w - side) // 2, 0))
    result.info["blend"] = "screen"
    return result


def composite(dest, figure, xy):
    """Screen a portrait cell over ``dest``; anything else is alpha-composited."""
    if figure.info.get("blend") != "screen":
        dest.alpha_composite(figure, xy)
        return
    x, y = xy
    box = (x, y, x + figure.width, y + figure.height)
    region = dest.crop(box)
    screened = ImageChops.screen(region.convert("RGB"), figure.convert("RGB"))
    screened.putalpha(region.getchannel("A"))
    dest.paste(screened, box)

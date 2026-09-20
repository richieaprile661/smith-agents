"""Render the approved source drawings without regenerating their contours.

The packaged sheets are byte-for-byte copies of the reviewed images. White
paper becomes transparent coverage at render time so the same drawing works
with each widget theme. Source rectangles retain the user's gallery numbers.
"""
import json
import re
import zlib
from functools import lru_cache
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).parent / "assets" / "approved"
with (ROOT / "manifest.json").open(encoding="utf-8") as handle:
    MANIFEST = json.load(handle)

STATE_FIGURES = {
    state: tuple("approved_%d" % number for number in numbers)
    for state, numbers in MANIFEST["state_figures"].items()
}
HEADER = "group_selfie"
SUBAGENT = "little_helper"
HELPER_FIGURES = ('helper_baby', 'helper_stroller', 'helper_sweeping', 'helper_watering_can')
SESSION_FIGURES = frozenset(pose for poses in STATE_FIGURES.values() for pose in poses) | frozenset(HELPER_FIGURES)


def helper_pose(agent):
    """Only classify current tool evidence, never infer work from a task title."""
    from .activity import current_tools
    data = agent.get('activity') or {}
    if agent.get('permissions'):
        return 'helper_baby'
    if agent.get('state') == 'closed' or data.get('loading') or data.get('status') in ('unknown', 'failed', 'stopped'):
        return 'helper_baby'
    if data.get('status') == 'completed':
        return 'helper_watering_can'
    if data.get('disconnected'):
        return 'helper_baby'
    if agent.get('state') == 'done':
        return 'helper_watering_can'
    if agent.get('state') == 'needs':
        return 'helper_baby'
    labels = [tool.get('label', '').lower() for tool in current_tools(data)]
    # Mixed concurrent work uses the general working pose. Specialized poses
    # describe the observed commands, not whether a review or test succeeded.
    def category(label):
        if re.match(r'(?:(?:functions|tools)\.)?(read|read_file|readfile|glob|grep|search|web_search)\b', label):
            return 'helper_stroller'
        command = re.sub(r'^(?:(?:functions|tools)\.)?(exec_command|bash|command|shell)\s+', '', label)
        if command == label:
            return 'helper_sweeping'
        if re.search(r'(?:^|[;&|]\s*)(?:(?:uv run|npx)\s+)?(?:python[\d.]* -m (?:pytest|unittest)|pytest|vitest|jest|cargo test|go test|npm (?:run )?test|pnpm (?:run )?test|dotnet test)\b', command):
            return 'helper_watering_can'
        if re.search(r'(?:^|[;&|]\s*)(?:git diff|git show|rg|cat|sed)\b', command):
            return 'helper_stroller'
        return 'helper_sweeping'
    categories = {category(label) for label in labels}
    return next(iter(categories)) if len(categories) == 1 else 'helper_sweeping'


class FigureAssignments:
    """Spread each state's figures across sessions without reshuffling rescans."""
    def __init__(self):
        self.choices = {}

    def assign(self, agents):
        identities = {agent.get('id') or agent.get('name') or '' for agent in agents}
        self.choices = {key: choice for key, choice in self.choices.items() if key in identities}
        counts = {}
        pending = []
        for agent in agents:
            identity = agent.get('id') or agent.get('name') or ''
            state = agent.get('state')
            variants = ((helper_pose(agent),) if agent.get('sub') else
                        STATE_FIGURES.get(state, STATE_FIGURES['working']))
            previous = self.choices.get(identity)
            if previous and previous[0] == state and previous[1] in variants:
                counts[state, previous[1]] = counts.get((state, previous[1]), 0) + 1
            else:
                pending.append((identity, state, variants, previous))
        # Input order and transcript idle times must not affect a new batch.
        for identity, state, variants, previous in sorted(pending, key=lambda row: row[0]):
            least = min(counts.get((state, pose), 0) for pose in variants)
            candidates = [pose for pose in variants if counts.get((state, pose), 0) == least]
            candidates = [pose for pose in candidates if not previous or pose != previous[1]] or candidates
            seed = zlib.crc32(identity.encode('utf-8'))
            pose = candidates[seed % len(candidates)]
            self.choices[identity] = (state, pose)
            counts[state, pose] = counts.get((state, pose), 0) + 1
        for agent in agents:
            identity = agent.get('id') or agent.get('name') or ''
            agent['_figure_state'], agent['_figure_pose'] = self.choices[identity]


def contains(name):
    return name.removeprefix("approved_") in MANIFEST["figures"]


@lru_cache(maxsize=4)
def _sheet(name):
    with Image.open(ROOT / name) as image:
        return image.convert("L")


@lru_cache(maxsize=32)
def alpha_mask(name):
    entry = MANIFEST["figures"][name.removeprefix("approved_")]
    if 'calibration' in entry:
        from . import matched_artwork
        return matched_artwork.alpha_mask(name)
    cut = _sheet(entry["source"]).crop(entry["rect"])
    # Discard only near-white paper noise, keeping partial edge coverage.
    alpha = cut.point(lambda v: max(0, round((247 - v) * 255 / 247)))
    bounds = alpha.getbbox()
    alpha = alpha.crop(bounds) if bounds else Image.new("L", (1, 1))
    width = entry.get('render_width')
    if width and alpha.width > width:
        alpha = alpha.resize((width, round(alpha.height * width / alpha.width)),
                             Image.Resampling.LANCZOS)
    return alpha


@lru_cache(maxsize=2)
def _session_geometry(helpers=False):
    """One character scale and baseline, with room for every action canvas.

    Character heights are calibrated on the person, excluding raised hands,
    props and scenery. A partly hidden character is calibrated by its head and
    torso proportions. Canvas bounds reserve space but never define body size.
    All measurements are fixed, so movement cannot rescale the drawing.
    """
    from . import figure_actions
    profiles = {}
    widest = top = bottom = 0.0
    names = {pose for poses in STATE_FIGURES.values() for pose in poses} | {SUBAGENT}
    if helpers:
        names = set(HELPER_FIGURES)
    for name in sorted(names):
        alpha = figure_actions.alpha_frame(name, 0)
        bounds = alpha.point(lambda value: 255 if value > 4 else 0).getbbox()
        character_h = MANIFEST['figures'][name.removeprefix('approved_')]['character_height']
        profiles[name] = character_h
        widest = max(widest, alpha.width / character_h)
        top = max(top, (bounds[3] - character_h) / character_h)
        bottom = max(bottom, (alpha.height - bounds[3]) / character_h)
    return profiles, widest, top, bottom


def _display_alpha(name, alpha, size):
    """Resample once, with firm stroke coverage and antialiased edges.

    At 1x, a downsampled fine stroke can contain no opaque pixels. A fixed
    coverage curve removes faint ringing and strengthens its core without
    spatial blur, dilation, or changes to the reviewed source masks.
    """
    alpha = alpha.resize(size, Image.Resampling.LANCZOS)
    if name not in SESSION_FIGURES:
        alpha = alpha.point(lambda v: v if v > 4 else 0)
    boost = MANIFEST['figures'][name.removeprefix('approved_')].get('coverage_boost', 0)
    if boost:
        alpha = alpha.point(lambda v: round(v + boost * v * (1-v/255)))
    if name in SESSION_FIGURES:
        return alpha.point(lambda v: max(0, min(255, round((v-16)*255/164))))
    return alpha


@lru_cache(maxsize=256)
def _session_placement(name, width, height):
    from . import figure_actions
    profiles, widest, top, bottom = _session_geometry(name in HELPER_FIGURES)
    body_h = profiles[name]
    visible_height = min(width / widest, height / (1 + top + bottom))
    if name in HELPER_FIGURES:
        # The simpler helper silhouette must not grow larger than the main
        # character merely because its props occupy less of the frame.
        _, main_width, main_top, main_bottom = _session_geometry()
        main_body = min(width / main_width, height / (1 + main_top + main_bottom))
        visible_height = min(visible_height, main_body * .9)
    scale = visible_height / body_h
    reference = figure_actions.alpha_frame(name, 0)
    # The helper pair has narrow standing bodies and finer source strokes.
    # Compensate only its display size; keep every other pose's calibration
    # and the common ground line unchanged.
    entry = MANIFEST['figures'][name.removeprefix('approved_')]
    scale = min(scale * entry.get('display_scale', 1),
                width / reference.width, height / reference.height)
    size = (max(1, round(reference.width * scale)), max(1, round(reference.height * scale)))
    reference = reference.resize(size, Image.Resampling.LANCZOS)
    bounds = reference.point(lambda value: value if value > 4 else 0).getbbox()
    # Anchor the actual resampled ground stroke, avoiding one-pixel baseline
    # differences from rounding source canvases of different dimensions.
    return size, round(height - visible_height * bottom) - bounds[3]


def session_body_height(width, height):
    """Fixed calibrated body scale, independent of props and animation frame."""
    density = min(width/56, height/40)
    return MANIFEST['matched_geometry']['logical_height']*density


def _session_alpha(name, alpha, width, height):
    # One crop and one transform for the whole animation. Measuring the
    # current frame here makes the character grow/shrink as props move.
    geometry = MANIFEST['session_drawing_geometry']
    crop = geometry['animation_bounds'][name]
    alpha = alpha.point(lambda v: v if v > geometry['alpha_cutoff'] else 0)
    alpha = alpha.crop(crop)
    body = MANIFEST['figures'][name.removeprefix('approved_')]['character_height']
    scale = session_body_height(width, height) / body
    size = (max(1, round((crop[2]-crop[0])*scale)),
            max(1, round((crop[3]-crop[1])*scale)))
    return _display_alpha(name, alpha, size)


def render(name, ink, width, height, fade=False, frame=0):
    # Actions use the shared source masks; defer the import to avoid a cycle.
    from . import figure_actions
    alpha = figure_actions.alpha_frame(name, figure_actions.canonical_frame(frame))
    if name in SESSION_FIGURES:
        alpha = _session_alpha(name, alpha, width, height)
        if fade:
            alpha = alpha.point(lambda v: round(v * .78))
        drawing = Image.new('RGBA', alpha.size, ink)
        drawing.putalpha(alpha)
        cell = Image.new('RGBA', (width, height))
        padding = max(1, round(min(width / 56, height / 42)))
        crop = MANIFEST['session_drawing_geometry']['animation_bounds'][name]
        body = MANIFEST['figures'][name.removeprefix('approved_')]['character_height']
        scale = session_body_height(width, height) / body
        baseline = MANIFEST['session_drawing_geometry']['baselines'][name]
        y = height-padding-round((baseline-crop[1])*scale)
        cell.paste(drawing, ((width-alpha.width)//2, y))
        return cell
    if name == HEADER:
        scale = min(width / alpha.width, height / alpha.height)
        size = (max(1, min(width, round(alpha.width * scale))),
                max(1, min(height, round(alpha.height * scale))))
        y = height - size[1]
    else:
        size, y = _session_placement(name, width, height)
    alpha = _display_alpha(name, alpha, size)
    if fade:
        alpha = alpha.point(lambda v: round(v * 0.78))
    drawing = Image.new("RGBA", size, ink)
    drawing.putalpha(alpha)
    cell = Image.new("RGBA", (width, height))
    cell.paste(drawing, ((width - size[0]) // 2, y))
    return cell


# The Matrix theme's tray mark: the approved solid white Smith face
# (output/smith-white-logos/02-solid-transparent.png).
MATRIX_TRAY = Path(__file__).resolve().parent / "assets" / "tray-matrix.png"


@lru_cache(maxsize=2)
def _tray_alpha(path=None):
    with Image.open(path or ROOT / MANIFEST["tray"]) as image:
        alpha = image.convert("RGBA").getchannel("A")
    # The approved silhouette has faint isolated pixels around its edge.
    # Ignore them when measuring its bounds, preserving the source itself.
    bounds = alpha.point(lambda v: 255 if v >= 128 else 0).getbbox()
    return alpha.crop(bounds) if bounds else Image.new("L", (1, 1))


@lru_cache(maxsize=8)
def face_icon(size, ink="white"):
    """The Matrix face as the widget's header logo, exactly as the artwork
    draws it, filling its square without the tray's padding."""
    alpha = _tray_alpha(MATRIX_TRAY)
    scale = min(size / alpha.width, size / alpha.height)
    target = (max(1, round(alpha.width * scale)), max(1, round(alpha.height * scale)))
    drawing = Image.new("RGBA", target, ink)
    drawing.putalpha(alpha.resize(target, Image.Resampling.LANCZOS))
    icon = Image.new("RGBA", (size, size))
    icon.paste(drawing, ((size - target[0]) // 2, size - target[1]))
    return icon


def tray_icon(size, ink="white", path=None):
    alpha = _tray_alpha(path)
    padding = max(1, round(size / 22))
    room = max(1, size - 2 * padding)
    scale = min(room / alpha.width, room / alpha.height)
    target = (max(1, round(alpha.width * scale)), max(1, round(alpha.height * scale)))
    alpha = alpha.resize(target, Image.Resampling.LANCZOS)
    drawing = Image.new("RGBA", target, ink)
    drawing.putalpha(alpha)
    icon = Image.new("RGBA", (size, size))
    icon.paste(drawing, ((size - target[0]) // 2, (size - target[1]) // 2))
    return icon

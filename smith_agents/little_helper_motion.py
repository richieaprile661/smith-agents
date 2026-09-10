"""Animate the approved helper's arm: reach, two head taps, return, quiet idle.

Only the arm moves substantially. The helper and ground stay in place, and
the original PNG remains untouched. Coordinates describe the approved PNG.
"""
import math
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

from . import artwork

SOURCE_ORIGIN = (72, 105)
SCALE = 400 / 1396
PAD = 24


def _point(x, y):
    return ((x - SOURCE_ORIGIN[0]) * SCALE + PAD,
            (y - SOURCE_ORIGIN[1]) * SCALE + PAD)


def _smooth(value):
    value = max(0, min(1, value))
    return value * value * (3 - 2 * value)


def pose(seconds):
    """Hand displacement in source pixels; settle fully before quiet idle."""
    keys = ((0, 0, 0), (.25, 0, 0), (.85, 105, -245),
            (1.10, 105, -202), (1.35, 105, -242), (1.60, 105, -202),
            (1.88, 105, -245), (2.8, 0, 0), (3.2, 0, 0))
    for left, right in zip(keys, keys[1:]):
        if seconds <= right[0]:
            amount = _smooth((seconds - left[0]) / (right[0] - left[0]))
            return tuple(a + (b - a) * amount for a, b in zip(left[1:], right[1:]))
    return 0, 0


@lru_cache(maxsize=1)
def _layers():
    original = artwork.alpha_mask(artwork.SUBAGENT)
    full = Image.new('L', (original.width + PAD * 2, original.height + PAD + 8))
    full.paste(original, (PAD, PAD))
    mask = Image.new('L', full.size)
    polygon = ((725, 443), (793, 488), (837, 505), (858, 487),
               (885, 477), (909, 481), (933, 496), (941, 522),
               (928, 544), (888, 551), (875, 583), (848, 591),
               (781, 580), (725, 564))
    ImageDraw.Draw(mask).polygon([_point(x, y) for x, y in polygon], fill=255)
    arm = ImageChops.multiply(full, mask)
    body = ImageChops.subtract(full, arm)
    # Reveal the short section of the helper's back hidden behind the hand.
    # It is painted underneath the original arm, never over either figure.
    ImageDraw.Draw(body).line([_point(914, 470), _point(909, 480), _point(895, 510),
                              _point(867, 550), _point(846, 590), _point(839, 610)],
                             fill=255, width=4, joint='curve')
    return full, body, arm


def _arm_frame(arm, dx, dy):
    """Shear the forearm continuously, keeping its attachment point fixed."""
    dx, dy = dx * SCALE, dy * SCALE
    anchor, wrist = _point(725, 0)[0], _point(860, 0)[0]
    span = wrist - anchor
    target_wrist = wrist + dx
    def inverse(x, y):
        if x <= anchor:
            return x, y
        if x >= target_wrist:
            return x - dx, y - dy
        source_x = (x + dx * anchor / span) / (1 + dx / span)
        return source_x, y - dy * (source_x - anchor) / span
    # Exact piecewise affine shear: mesh edges meet at the fixed elbow and
    # translated wrist. Extra subdivisions keep the moving join smooth.
    width, height = arm.size
    edges = sorted({0, width, round(anchor), round(target_wrist),
                    *range(round(anchor), round(target_wrist), 3)})
    mesh = [((x0, 0, x1, height), inverse(x0, 0) + inverse(x0, height)
             + inverse(x1, height) + inverse(x1, 0)) for x0, x1 in zip(edges, edges[1:])]
    return arm.transform(arm.size, Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)


@lru_cache(maxsize=128)
def alpha_frame(frame):
    from .figure_actions import ACTION_FRAMES, FPS, IDLE_FRAMES, _joint_warp, canonical_frame
    frame = canonical_frame(frame)
    original, body, arm = _layers()
    if frame < ACTION_FRAMES:
        dx, dy = pose(frame / FPS)
        if dx == dy == 0:
            return original.copy()
        return ImageChops.lighter(body, _arm_frame(arm, dx, dy))
    # A subpixel breath through the helper's upper body, never another tap.
    phase = 2 * math.pi * (frame - ACTION_FRAMES) / IDLE_FRAMES
    x, y = _point(1010, 430)
    return _joint_warp(original, [(x - PAD, y - PAD, 42, 57,
                                  .45 * math.sin(phase), .25 * math.sin(phase))])

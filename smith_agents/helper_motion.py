"""Animate the four approved helper poses without deforming their bodies.

Each local patch stays inside a prop. Everything outside those rectangles,
including the character and ground, is copied unchanged from the source mask.
"""
import math
from functools import lru_cache

from PIL import Image

from . import artwork

PAD = 24

# Rectangles use original sheet coordinates; dx/dy are maximum source pixels.
# Keep patches clear of adult contours and the ground stroke.
PROP_REGIONS = {
    'helper_baby': (((270, 180, 308, 220), 2, -2),
                    ((249, 221, 305, 267), 2, -3)),
    'helper_stroller': (((794, 233, 864, 314), 3, -2),),
    'helper_sweeping': (((805, 797, 889, 844), 7, 0),),
    'helper_watering_can': (((1300, 750, 1425, 828), 3, -4),),
}


def local_box(name, rectangle):
    ox, oy = artwork.MANIFEST['figures'][name]['motion_origin']
    left, top, right, bottom = rectangle
    return left-ox+PAD, top-oy+PAD, right-ox+PAD, bottom-oy+PAD


@lru_cache(maxsize=4)
def source_canvas(name):
    source = artwork.alpha_mask(name)
    canvas = Image.new('L', (source.width+PAD*2, source.height+PAD*2))
    canvas.paste(source, (PAD, PAD))
    return canvas


def _move_patch(patch, dx, dy):
    """Local motion tapers to zero at every edge of the prop patch."""
    width, height = patch.size

    def inverse(x, y):
        weight = math.sin(math.pi*x/width)**2 * math.sin(math.pi*y/height)**2
        return x-dx*weight, y-dy*weight

    mesh = []
    for row in range(12):
        y0, y1 = round(row*height/12), round((row+1)*height/12)
        for col in range(12):
            x0, x1 = round(col*width/12), round((col+1)*width/12)
            mesh.append(((x0,y0,x1,y1),
                         inverse(x0,y0)+inverse(x0,y1)+inverse(x1,y1)+inverse(x1,y0)))
    return patch.transform(patch.size, Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)


@lru_cache(maxsize=384)
def alpha_frame(name, frame):
    from .figure_actions import ACTION_FRAMES, IDLE_FRAMES
    source = source_canvas(name)
    active = frame < ACTION_FRAMES
    u = frame/ACTION_FRAMES if active else (frame-ACTION_FRAMES)/IDLE_FRAMES
    envelope = math.sin(math.pi*u)**2 if active else .10
    swing = math.sin(4*math.pi*u if active else 2*math.pi*u)*envelope
    if abs(swing) < 1e-12:
        return source
    result = source.copy()
    for rectangle, dx, dy in PROP_REGIONS[name]:
        box = local_box(name, rectangle)
        patch = _move_patch(source.crop(box), dx*swing, dy*swing)
        result.paste(patch, box)
    return result

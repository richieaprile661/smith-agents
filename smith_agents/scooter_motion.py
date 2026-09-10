"""Scooter entrance: plant the kicking foot, push, then coast.

The approved pixels follow a leg joint and a separate glide, while the ground
stays fixed. The full widget and both previews share this approved motion.
"""
import math
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

from . import artwork

ACTION_SECONDS = 3.2
FPS = 20
IDLE_STRENGTH = 1.0


def _smooth(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _pose(seconds):
    # Two deliberate pushes. Angles are measured at the kicking hip;
    # negative values lower the raised foot toward the ground.
    keys = ((0.0, 0, -30), (.30, 12, -30), (.65, -50, -27),
            (1.03, 15, -17), (1.30, 0, -12), (1.55, -50, -10),
            (1.95, 15, -3), (2.35, 0, -1), (3.2, 0, 0))
    for left, right in zip(keys, keys[1:]):
        if seconds <= right[0]:
            mix = _smooth((seconds-left[0]) / (right[0]-left[0]))
            return tuple(a + (b-a)*mix for a, b in zip(left[1:], right[1:]))
    return 0.0, 0.0


@lru_cache(maxsize=1)
def _layers():
    alpha = artwork.alpha_mask('approved_35')
    leg_mask = Image.new('L',alpha.size)
    ImageDraw.Draw(leg_mask).polygon(((0,120),(107,120),(132,142),
                                     (132,152),(112,178),(0,185)),fill=255)
    leg = ImageChops.multiply(alpha,leg_mask)
    body = ImageChops.subtract(alpha,leg)
    pen = ImageDraw.Draw(body)
    pen.rectangle((0,205,71,225),fill=0)
    pen.rectangle((256,205,331,225),fill=0)
    return body, leg


@lru_cache(maxsize=160)
def alpha_frame(frame):
    seconds = frame / FPS
    if seconds >= ACTION_SECONDS:
        idle = (frame-round(ACTION_SECONDS*FPS)) % 64 / 64
        angle = IDLE_STRENGTH * math.sin(2*math.pi*idle)**3
        travel = 0.0
    else:
        angle,travel = _pose(seconds)
    body, leg = _layers()
    pivot = (121,137)
    turned = leg.rotate(-angle,resample=Image.Resampling.BICUBIC,
                        center=pivot)
    reach = -28 * _smooth(max(0, -angle)/50)
    turned = turned.transform(turned.size,Image.Transform.AFFINE,
                              (1,0,-reach,0,1,0),Image.Resampling.BICUBIC)
    figure = ImageChops.lighter(body,turned)
    radians=math.radians(angle)
    def turn(point):
        x,y=point[0]-pivot[0],point[1]-pivot[1]
        return (pivot[0]+x*math.cos(radians)-y*math.sin(radians)+reach,
                pivot[1]+x*math.sin(radians)+y*math.cos(radians))
    if abs(angle) > .01:
        pen=ImageDraw.Draw(figure)
        # Bridge the two cut ends at the hip; the shin and foot themselves
        # remain rigid original pixels, even during the full downward swing.
        for point in ((104,120),(132,151)):
            target=turn(point)
            pen.line((point,target),fill=245,width=5)
            for x,y in (point,target):
                pen.ellipse((x-2,y-2,x+2,y+2),fill=245)
    figure=figure.transform(figure.size,Image.Transform.AFFINE,
                            (1,0,-travel,0,1,0),Image.Resampling.BICUBIC)
    floor=Image.new('L',figure.size)
    pen=ImageDraw.Draw(floor)
    pen.line((0,217,73+travel,215),fill=235,width=5)
    pen.line((254+travel,215,332,214),fill=235,width=5)
    return ImageChops.lighter(floor,figure)


def render(ink, width, height, elapsed):
    frame = max(0, round(elapsed*FPS))
    end = round(ACTION_SECONDS*FPS)
    if frame >= end:
        frame = end + (frame-end) % 64
    alpha = alpha_frame(frame)
    scale = min(width/alpha.width, height/alpha.height)
    size = (max(1,round(alpha.width*scale)), max(1,round(alpha.height*scale)))
    drawing = Image.new('RGBA',size,ink)
    drawing.putalpha(alpha.resize(size,Image.Resampling.LANCZOS))
    cell = Image.new('RGBA',(width,height))
    cell.paste(drawing,((width-size[0])//2,height-size[1]))
    return cell

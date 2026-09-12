"""Animate the selected Little Smith artwork with local joint movement.

Source PNGs are immutable. Every frame shares one padded canvas; the legless
body's lower contour stays fixed. Entrances settle into a restrained idle loop.
"""
import math
from functools import lru_cache

from PIL import Image, ImageDraw

from . import artwork

PAD = 24


def point(name, x, y):
    entry = artwork.MANIFEST['figures'][name]
    ox, oy = entry['motion_origin']
    scale = entry['motion_scale']
    return (x-ox)*scale+PAD, (y-oy)*scale+PAD


@lru_cache(maxsize=6)
def source_canvas(name):
    source = artwork.alpha_mask(name)
    canvas = Image.new('L', (source.width+PAD*2, source.height+PAD*2))
    canvas.paste(source, (PAD, PAD))
    return canvas


def _warp(name, source, fields):
    scale = artwork.MANIFEST['figures'][name]['motion_scale']
    local = [(*point(name, x, y), rx*scale, ry*scale, dx*scale, dy*scale)
             for x, y, rx, ry, dx, dy in fields]
    width, height = source.size
    # Keep the lower silhouette steady; the artwork has no floor or feet.
    fixed_bottom = height-PAD-18*scale
    def inverse(x, y):
        if y >= fixed_bottom:
            return x, y
        dx = dy = 0
        for cx, cy, rx, ry, vx, vy in local:
            weight = math.exp(-2*((x-cx)/rx)**4-2*((y-cy)/ry)**4)
            dx += vx*weight
            dy += vy*weight
        return x-dx, y-dy
    mesh = []
    for row in range(24):
        y0, y1 = round(row*height/24), round((row+1)*height/24)
        for col in range(28):
            x0, x1 = round(col*width/28), round((col+1)*width/28)
            mesh.append(((x0,y0,x1,y1), inverse(x0,y0)+inverse(x0,y1)+inverse(x1,y1)+inverse(x1,y0)))
    moved = source.transform(source.size, Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)
    moved.paste(source.crop((0, round(fixed_bottom), width, height)), (0, round(fixed_bottom)))
    return moved


@lru_cache(maxsize=384)
def alpha_frame(name, frame):
    from .figure_actions import ACTION_FRAMES, IDLE_FRAMES
    source = source_canvas(name)
    active = frame < ACTION_FRAMES
    u = frame/ACTION_FRAMES if active else (frame-ACTION_FRAMES)/IDLE_FRAMES
    envelope = math.sin(math.pi*u)**2 if active else .10
    swing = math.sin(4*math.pi*u if active else 2*math.pi*u)*envelope
    pulse = math.sin(2*math.pi*u)**2*envelope
    if name == 'helper_working':
        fields = [(301,394,47,23,2*swing,-7*swing)]
    elif name == 'helper_reviewing':
        fields = [(860,331,43,43,12*swing,-8*pulse), (816,388,40,29,6*swing,-3*pulse)]
    elif name == 'helper_testing':
        fields = [(1334,397,42,29,8*swing,-6*swing)]
        if active:
            # Reveal the original check stroke without redrawing its contours.
            source = source.copy()
            x0,y0 = point(name,1374,405)
            x1,y1 = point(name,1417,437)
            progress = min(1, max(0, (u-.1)/.55))
            if progress < 1:
                ImageDraw.Draw(source).rectangle((x0+(x1-x0)*progress,y0,x1,y1),fill=0)
    elif name == 'helper_needs':
        fields = [(366,738,42,58,12*swing,-5*pulse)]
    elif name == 'helper_finished':
        fields = [(934,821,81,45,12*pulse,-5*pulse), (872,840,50,30,8*pulse,-3*pulse)]
    else:
        fields = [(1398,834,61,34,3*pulse,-10*pulse)]
    return _warp(name,source,fields)

"""Prop-local animation on one fixed, calibrated body and scene per pose."""
import math
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

from . import artwork, matched_artwork

PAD = 24
# Source-sheet rectangles deliberately avoid the head and torso. Each patch
# tapers to zero displacement at its edges; the body and ground are immutable.
REGIONS = {
    'approved_3': (((79,165,128,209), 0, -9),),
    'approved_7': (((363,166,432,206), 5, 0),),
    'approved_8': (((433,294,524,353), -2, -4),),
    'approved_12': (((1170,184,1320,297), 0, 5),),
    'approved_14': (((1335,156,1390,195), 3, -4),),
    'approved_17': (((190,385,244,444), 7, 0),),
    'approved_20': (((384,407,438,451), 0, -7),),
    'approved_23': (),
    'approved_28': (((1067,376,1140,424), 4, 3),),
    'approved_29': (((1388,336,1442,400), -2, 5),),
    'approved_30': (((199,562,235,623), 4, -3),),
    'approved_34': (((518,618,552,675), 5, -3),),
    'approved_35': (((680,640,738,680), -4, 5),),
    'helper_baby': (((1128,646,1188,744), 2, -4),),
    'helper_stroller': (((504,848,548,906), 3, -3),),
    'helper_sweeping': (((788,902,836,930), 7, 0),),
    'helper_watering_can': (((1091,880,1168,918), 4, -3),),
}
LAPTOP_SCREEN = ((552,658),(613,658),(591,718),(531,718))


def point(name, x, y):
    x, y = matched_artwork.source_point(name, x, y)
    return x+PAD, y+PAD


@lru_cache(maxsize=17)
def regions(name):
    result = []
    for rectangle, dx, dy in REGIONS[name]:
        left, top, right, bottom = rectangle
        points = [point(name, x, y) for x in (left, (left+right)/2, right)
                  for y in (top, (top+bottom)/2, bottom)]
        box = (math.floor(min(p[0] for p in points)), math.floor(min(p[1] for p in points)),
               math.ceil(max(p[0] for p in points)), math.ceil(max(p[1] for p in points)))
        result.append((box, dx, dy))
    return tuple(result)


@lru_cache(maxsize=17)
def source_canvas(name):
    source = artwork.alpha_mask(name)
    canvas = Image.new('L', (source.width+2*PAD, source.height+2*PAD))
    canvas.paste(source, (PAD, PAD))
    return canvas


def _patch_motion(patch, dx, dy):
    width, height = patch.size
    def inverse(x, y):
        weight = math.sin(math.pi*x/width)**2 * math.sin(math.pi*y/height)**2
        return x-dx*weight, y-dy*weight
    mesh = []
    for row in range(12):
        top, bottom = round(row*height/12), round((row+1)*height/12)
        for col in range(12):
            left, right = round(col*width/12), round((col+1)*width/12)
            mesh.append(((left,top,right,bottom), inverse(left,top)+inverse(left,bottom)+
                         inverse(right,bottom)+inverse(right,top)))
    return patch.transform(patch.size, Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)


def screen_mask(size, padded=False):
    mask = Image.new('L', size)
    polygon = [point('approved_23', x, y) for x,y in LAPTOP_SCREEN]
    if not padded:
        polygon = [(x-PAD,y-PAD) for x,y in polygon]
    ImageDraw.Draw(mask).polygon(polygon,fill=255)
    return mask


def screen_point(u, v):
    """Position screen content inside the revised laptop's quadrilateral."""
    top_left, top_right, bottom_right, bottom_left = LAPTOP_SCREEN
    left = tuple(a+(b-a)*v for a,b in zip(top_left,bottom_left))
    right = tuple(a+(b-a)*v for a,b in zip(top_right,bottom_right))
    return point('approved_23', *(a+(b-a)*u for a,b in zip(left,right)))


@lru_cache(maxsize=384)
def alpha_frame(name, frame):
    from .figure_actions import ACTION_FRAMES, IDLE_FRAMES
    source = source_canvas(name)
    active = frame < ACTION_FRAMES
    u = frame/ACTION_FRAMES if active else (frame-ACTION_FRAMES)/IDLE_FRAMES
    envelope = math.sin(math.pi*u)**2 if active else .10
    swing = math.sin((4 if active else 2)*math.pi*u)*envelope
    result = source.copy()
    for box, dx, dy in regions(name):
        if abs(swing) > 1e-12:
            result.paste(_patch_motion(source.crop(box), dx*swing, dy*swing), box)
    if name == 'approved_23':
        overlay = Image.new('L', source.size)
        pen = ImageDraw.Draw(overlay)
        progress = min(1, frame/32) if active else 1
        for index in range(3):
            fraction = min(1,max(0,progress*3-index))
            a = screen_point(.12, .18+index*.23)
            b = screen_point(.12+fraction*(.7-index*.12), .18+index*.23)
            if fraction:
                pen.line((a,b), fill=230,width=2)
        a = screen_point(.78, .64)
        b = screen_point(.78, .85)
        pen.line((a,b),fill=round(230*math.sin(2*math.pi*u)**2),width=2)
        overlay = ImageChops.multiply(overlay, screen_mask(source.size, padded=True))
        result = ImageChops.lighter(result,overlay)
    entry = artwork.MANIFEST['figures'][name.removeprefix('approved_')]
    if entry.get('head_source'):
        # Nearby prop interpolation must not bleed even faint coverage into
        # the shared head/neck contour (notably the cradled baby's top edge).
        bottom = PAD+artwork.MANIFEST['matched_geometry']['head_height']+14
        result.paste(source.crop((0,0,source.width,bottom)), (0,0))
    return result

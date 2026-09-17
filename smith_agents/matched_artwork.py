"""Fixed head, torso and scene calibration for the matched figure atlas.

The source image is immutable. A pose's mesh is computed once from independently
recorded head/torso landmarks, before any animation. Props occupy the remaining
space; neither their shape nor their motion can choose the character's scale.
"""
from functools import lru_cache
import math

from PIL import Image, ImageChops


def _lerp(value, left, right, out_left, out_right):
    return out_left + (value-left)*(out_right-out_left)/(right-left)


def _piecewise(value, source, target):
    for index in range(len(source)-1):
        if value <= source[index+1]:
            return _lerp(value, source[index], source[index+1], target[index], target[index+1])
    return _lerp(value, source[-2], source[-1], target[-2], target[-1])


@lru_cache(maxsize=17)
def geometry(name):
    from . import artwork
    entry = artwork.MANIFEST['figures'][name.removeprefix('approved_')]
    spec = artwork.MANIFEST['matched_geometry']
    source = artwork._sheet(entry['source']).crop(entry['rect'])
    alpha = source.point(lambda value: max(0, round((247-value)*255/247)))
    alpha = alpha.point(lambda value: value if value > 16 else 0)
    alpha = alpha.crop(alpha.getbbox())
    calibration = entry['calibration']
    head = calibration['head']
    torso = calibration['torso']
    source_y = (head[1], head[3], torso[2], alpha.height)
    target_y = (0, spec['head_height'], spec['waist_y'], spec['height'])
    return alpha, head, torso, source_y, target_y


def _columns(name, target_y):
    from . import artwork
    alpha, head, torso, _, rows = geometry(name)
    spec = artwork.MANIFEST['matched_geometry']
    blend = min(1, max(0, (target_y-rows[1])/(rows[2]-rows[1])))
    left = head[0]*(1-blend) + torso[0]*blend
    right = head[2]*(1-blend) + torso[1]*blend
    center = (left+right)/2*spec['width']/alpha.width
    span = spec['head_width']*(1-blend) + spec['torso_width']*blend
    return (0, left, right, alpha.width), (0, center-span/2, center+span/2, spec['width'])


def source_point(name, x, y):
    """Map original sheet coordinates into the calibrated drawing."""
    from . import artwork
    entry = artwork.MANIFEST['figures'][name.removeprefix('approved_')]
    ox, oy = entry['calibration']['origin']
    _, _, _, source_y, target_y = geometry(name)
    ty = _piecewise(y-oy, source_y, target_y)
    source_x, target_x = _columns(name, ty)
    return _piecewise(x-ox, source_x, target_x), ty


def head_offset(name, reference):
    """Integer translation preserves the accepted head's exact contour pixels."""
    target = _columns(name, 0)[1]
    source = _columns(reference, 0)[1]
    return round((target[1]+target[2]-source[1]-source[2])/2)


def _neck_edges(mask, y):
    runs = []
    start = None
    for x in range(mask.width):
        ink = mask.getpixel((x, y)) > 100
        if ink and start is None:
            start = x
        elif not ink and start is not None:
            runs.append((start+x-1)/2)
            start = None
    if len(runs) < 2:
        raise ValueError(f'Expected two neck contours at row {y}, found {len(runs)}')
    # These four right-facing adults have their props to the right of the neck.
    return runs[:2]


def _shared_head(name, result, reference):
    from . import artwork
    donor = alpha_mask(reference)
    height = artwork.MANIFEST['matched_geometry']['head_height']+14
    offset = head_offset(name, reference)
    source_edges = _neck_edges(result, height)
    target_edges = [x+offset for x in _neck_edges(donor, height)]
    left = math.floor(min(source_edges[0],target_edges[0]))-8
    right = math.ceil(max(source_edges[1],target_edges[1]))+8
    # Join the existing shoulders to the accepted head over a short neck band.
    # The head is copied verbatim; torso, limbs, props and baseline stay fixed.
    end = height+14

    def inverse(x, y):
        blend = max(0, min(1, (end-y)/(end-height)))
        target = [a+(b-a)*blend for a,b in zip(source_edges,target_edges)]
        return _piecewise(x, (0,left,*target,right,result.width),
                          (0,left,*source_edges,right,result.width)), y

    mesh = []
    for y in range(height, end):
        for x in range(0, result.width, 3):
            right = min(result.width,x+3)
            mesh.append(((x,y,right,y+1), inverse(x,y)+inverse(x,y+1)+
                         inverse(right,y+1)+inverse(right,y)))
    neck = result.transform(result.size, Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)
    result.paste(neck.crop((left,height,right,end)), (left,height))
    result.paste(0, (0,0,result.width,height))
    result.paste(donor.crop((0,0,donor.width,height)), (offset,0))
    return result


@lru_cache(maxsize=17)
def alpha_mask(name):
    from . import artwork
    alpha, _, _, source_y, target_y = geometry(name)
    spec = artwork.MANIFEST['matched_geometry']
    width, height = spec['width'], spec['height']

    def inverse(x, y):
        source_x, target_x = _columns(name, y)
        return _piecewise(x, target_x, source_x), _piecewise(y, target_y, source_y)

    # Include landmark rows exactly; dense cells keep curved contour joins smooth.
    xs = sorted(set(range(0, width, 4)) | {width})
    ys = sorted(set(range(0, height, 3)) | set(target_y))
    mesh = []
    for top, bottom in zip(ys, ys[1:]):
        for left, right in zip(xs, xs[1:]):
            mesh.append(((left, top, right, bottom),
                         inverse(left, top)+inverse(left, bottom)+
                         inverse(right, bottom)+inverse(right, top)))
    result = alpha.transform((width, height), Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)
    entry = artwork.MANIFEST['figures'][name.removeprefix('approved_')]
    if reference := entry.get('head_source'):
        result = _shared_head(name, result, reference)
    # Register the ground endpoints to the common scene width. A rounded
    # endpoint can otherwise lose its last source column during interpolation.
    for edge, neighbor in ((0, 1), (width-1, width-2)):
        endpoint = ImageChops.lighter(result.crop((edge, 0, edge+1, height)),
                                      result.crop((neighbor, 0, neighbor+1, height)))
        result.paste(endpoint, (edge, 0))
    return result

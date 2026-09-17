"""Fixed head, torso and scene calibration for the matched figure atlas.

The source image is immutable. A pose's mesh is computed once from independently
recorded head/torso landmarks, before any animation. Props occupy the remaining
space; neither their shape nor their motion can choose the character's scale.
"""
from functools import lru_cache

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
    # Register the ground endpoints to the common scene width. A rounded
    # endpoint can otherwise lose its last source column during interpolation.
    for edge, neighbor in ((0, 1), (width-1, width-2)):
        endpoint = ImageChops.lighter(result.crop((edge, 0, edge+1, height)),
                                      result.crop((neighbor, 0, neighbor+1, height)))
        result.paste(endpoint, (edge, 0))
    return result

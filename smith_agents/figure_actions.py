"""Activity-specific entrances followed by small, continuous idle movements."""
import math
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

from . import artwork, figure_motion, scooter_motion

FPS = 20
ACTION_SECONDS = 3.2
ACTION_FRAMES = round(ACTION_SECONDS * FPS)
IDLE_FRAMES = 64
REPEAT_SECONDS = 60.0
PAD = 24
LABELS = {
    'approved_3': ('Ball', 'Two bounces, then a gentle rock'),
    'approved_7': ('Snail', 'Crawl forward, then gently sway'),
    'approved_8': ('Step over', 'Hop over the bump, then settle'),
    'approved_12': ('Signpost', 'Swing the arrow, then settle'),
    'approved_14': ('Cyclist', 'Pedal and roll forward, then coast'),
    'approved_17': ('Cloth', 'Shake out the cloth, then let it sway'),
    'approved_20': ('Pump', 'Two full pump strokes, then ease off'),
    'approved_23': ('Laptop', 'Type on the screen, then blink the cursor'),
    'approved_28': ('Watering', 'Tip the can and pour, then settle'),
    'approved_29': ('Shower', 'Flowing water and rising bubbles'),
    'approved_30': ('Cooking', 'Toss the pan, then let the steam drift'),
    'approved_34': ('Fishing', 'Cast the rod, then let the line sway'),
    'approved_35': ('Scooter', 'Two pushes, then a quiet idle'),
    'group_selfie': ('Selfie', 'A camera flash, then quiet star twinkles'),
    'little_helper': ('Little helper', 'Two gentle head taps, then quiet idle'),
    'helper_working': ('Working', 'Type at the laptop'),
    'helper_reviewing': ('Reviewing', 'Scan the page with a magnifying glass'),
    'helper_testing': ('Testing', 'Mark the test checklist'),
    'helper_needs': ('Needs you', 'Raise a hand and wave'),
    'helper_finished': ('Finished', 'Hand over the finished page'),
    'helper_unknown': ('Activity unknown', 'Turn an empty palm upward'),
}


class Timeline:
    """One entrance per session/pose, beginning on its first visible paint."""
    def __init__(self):
        self.starts = {}

    def elapsed(self, identity, pose, now):
        previous = self.starts.get(identity)
        if previous is None or previous[0] != pose or now < previous[1]:
            if len(self.starts) >= 512:
                self.starts.pop(next(iter(self.starts)))
            previous = self.starts[identity] = (pose, now)
        return max(0, now - previous[1])

    def replay(self, identity=None):
        if identity is None:
            self.starts.clear()
        else:
            self.starts.pop(identity, None)

    def sync(self, poses):
        """Forget departed sessions and changed states, even while hidden."""
        self.starts = {key: value for key, value in self.starts.items()
                       if key in poses and poses[key] == value[0]}


def frame_at(elapsed):
    frame = max(0, int(elapsed * FPS + 1e-7))
    return frame if frame < ACTION_FRAMES else ACTION_FRAMES + (frame-ACTION_FRAMES) % IDLE_FRAMES


def periodic_elapsed(elapsed):
    """One entrance each minute, with the existing quiet idle in between."""
    return max(0.0, elapsed) % REPEAT_SECONDS


def canonical_frame(frame):
    frame = max(0, int(frame))
    return frame if frame < ACTION_FRAMES else ACTION_FRAMES + (frame-ACTION_FRAMES) % IDLE_FRAMES


def _smooth(value):
    value = max(0, min(1, value))
    return value*value*(3-2*value)


def _canvas(alpha):
    result = Image.new('L', (alpha.width+2*PAD, alpha.height+PAD+8))
    result.paste(alpha, (PAD, PAD))
    return result


# Cutouts use the exact approved source coordinates. Closed masks separate
# moving props from the stationary drawing; pivots are in the same coordinates.
PARTS = {
    'approved_3': {'ball': ((25,107),(89,107),(89,159),(25,159))},
    'approved_7': {'snail': ((11,91),(100,91),(101,147),(11,147))},
    'approved_8': {'person': ((84,-4),(239,-4),(239,124),(209,157),(209,168),
                             (195,168),(127,168),(117,137),(84,117))},
    'approved_12': {'arrow': ((12,25),(91,25),(91,94),(55,94),(55,78),(12,78))},
    'approved_17': {'cloth': ((216,106),(294,111),(303,200),(190,205),(188,144))},
    'approved_28': {'can': ((144,75),(177,70),(214,101),(214,137),(154,147),(116,126),(122,102))},
    'approved_29': {'bubble_left': ((77,90),(101,90),(101,116),(77,116)),
                    'bubble_right': ((200,101),(222,101),(222,125),(200,125))},
    'approved_30': {'pan': ((175,90),(272,81),(276,126),(185,128)),
                    'steam': ((205,-5),(280,-5),(280,86),(205,86))},
    'approved_34': {'rod': ((140,106),(163,61),(207,19),(252,-4),(252,8),
                           (222,28),(182,69),(148,111)),
                    'line': ((240,9),(253,9),(260,158),(234,158))},
    'group_selfie': {
        'star0': ((470,-5),(525,-5),(525,56),(470,56)),
        'star1': ((364,44),(445,44),(445,124),(364,124)),
        'star2': ((512,44),(658,44),(658,159),(512,159)),
        'star3': ((430,100),(490,100),(490,158),(430,158)),
        'star4': ((647,102),(711,102),(711,168),(647,168)),
    },
}


@lru_cache(maxsize=14)
def _parts(name):
    original = artwork.alpha_mask(name)
    base = _canvas(original)
    parts = {}
    for key, polygon in PARTS.get(name, {}).items():
        mask = Image.new('L', original.size)
        ImageDraw.Draw(mask).polygon(polygon, fill=255)
        part = _canvas(ImageChops.multiply(original, mask))
        parts[key] = part
        base = ImageChops.subtract(base, part)
    if name == 'approved_3':
        # The ball touches the baseline. Separate only its circular outline;
        # the ground stays fixed instead of flying up with the cutout.
        ball = parts['ball']
        ImageDraw.Draw(ball).arc((29+PAD,107+PAD,86+PAD,162+PAD), 10, 170, fill=245, width=5)
        ImageDraw.Draw(base).rectangle((24+PAD,159+PAD,90+PAD,166+PAD),fill=0)
        _pen_line(ImageDraw.Draw(base),((24,163),(90,163)),width=5)
    if name == 'approved_8':
        _pen_line(ImageDraw.Draw(base), ((105,163),(295,163)), width=6)
    for part in parts.values():
        part.info['motion_bounds'] = part.getbbox()
    return base, parts


def _moved(part, angle=0, pivot=(0,0), dx=0, dy=0, opacity=1, scale=1):
    # Inverse affine mapping keeps the original contours rigid, including when
    # an object rotates and travels in the same frame.
    theta = math.radians(angle)
    c,s = math.cos(theta)/scale, math.sin(theta)/scale
    x,y = pivot[0]+PAD, pivot[1]+PAD
    if angle == dx == dy == 0 and scale == 1 and opacity == 1:
        return part
    bounds = part.info.get('motion_bounds') or part.getbbox()
    result = Image.new('L', part.size)
    if not bounds:
        return result
    # Transform only the prop's occupied rectangle. In particular, five tiny
    # selfie stars must not each resample the entire 970px group drawing.
    left,top,right,bottom = bounds
    points = [(x+dx+scale*(math.cos(theta)*(xx-x)-math.sin(theta)*(yy-y)),
               y+dy+scale*(math.sin(theta)*(xx-x)+math.cos(theta)*(yy-y)))
              for xx,yy in ((left,top),(right,top),(right,bottom),(left,bottom))]
    ox = max(0, math.floor(min(p[0] for p in points))-2)
    oy = max(0, math.floor(min(p[1] for p in points))-2)
    ex = min(part.width, math.ceil(max(p[0] for p in points))+2)
    ey = min(part.height, math.ceil(max(p[1] for p in points))+2)
    if ex <= ox or ey <= oy:
        return result
    cut = part.crop(bounds).transform((ex-ox,ey-oy), Image.Transform.AFFINE,
        (c,s,x-c*(x+dx)-s*(y+dy)+c*ox+s*oy-left,
         -s,c,y+s*(x+dx)-c*(y+dy)-s*ox+c*oy-top), Image.Resampling.BICUBIC)
    result.paste(cut,(ox,oy))
    if opacity < 1:
        result = result.point(lambda value: round(value*max(0,opacity)))
    return result


def _add(base, part, **motion):
    return ImageChops.lighter(base, _moved(part, **motion))


def _pen_line(pen, points, fill=235, width=4):
    pen.line([(x+PAD,y+PAD) for x,y in points],fill=fill,width=width)


def _joint_warp(alpha, fields):
    """Continuous joint bends with fixed surroundings (pump torso/pedalling)."""
    width,height = alpha.size
    def point(x,y):
        sx,sy=x-PAD,y-PAD
        dx=dy=0
        for cx,cy,rx,ry,vx,vy in fields:
            weight = math.exp(-2*((sx-cx)/rx)**4-2*((sy-cy)/ry)**4)
            dx += vx*weight
            dy += vy*weight
        return x-dx,y-dy
    mesh=[]
    for row in range(28):
        y0,y1=round(row*height/28),round((row+1)*height/28)
        for col in range(36):
            x0,x1=round(col*width/36),round((col+1)*width/36)
            mesh.append(((x0,y0,x1,y1),point(x0,y0)+point(x0,y1)+point(x1,y1)+point(x1,y0)))
    return alpha.transform(alpha.size,Image.Transform.MESH,mesh,Image.Resampling.BICUBIC)


def alpha_frame(name, frame):
    """Render a bounded entrance/idle frame; shared source masks stay immutable."""
    if name in artwork.HELPER_FIGURES:
        from . import helper_motion
        return helper_motion.alpha_frame(name, canonical_frame(frame))
    if name == 'approved_35':
        return scooter_motion.alpha_frame(frame)
    if name == artwork.SUBAGENT:
        from . import little_helper_motion
        return little_helper_motion.alpha_frame(frame)
    source = artwork.alpha_mask(name)
    active = frame < ACTION_FRAMES
    u = frame/ACTION_FRAMES if active else (frame-ACTION_FRAMES)/IDLE_FRAMES
    gain = 1 if active else .035
    window = _smooth(u/.10)*(1-_smooth((u-.78)/.22)) if active else 1
    swing = math.sin(4*math.pi*u if active else 2*math.pi*u)*window*gain
    pulse = math.sin(2*math.pi*u if active else math.pi*u)**2*window*gain
    base,parts = _parts(name)
    base=base.copy()
    pen=ImageDraw.Draw(base)

    if name == 'approved_3':
        return _add(base,parts['ball'],dx=4*swing,dy=-43*pulse)
    if name == 'approved_7':
        travel=-12*(1-_smooth(u)) if active else 0
        return _add(base,parts['snail'],dx=travel,angle=7*swing,pivot=(65,142))
    if name == 'approved_8':
        travel=-28*(1-_smooth(u)) if active else 0
        lift=25*math.sin(math.pi*u)**2 if active else .6*math.sin(math.pi*u)**2
        return _add(base,parts['person'],dx=travel,dy=-lift,angle=-4*swing,pivot=(163,157))
    if name == 'approved_12':
        # The post meets the same pivot while the sign swings around it.
        _pen_line(pen,((40,75),(40,81)),width=5)
        return _add(base,parts['arrow'],angle=28*swing,pivot=(40,75))
    if name == 'approved_17':
        return _add(base,parts['cloth'],angle=18*swing,pivot=(213,115))
    if name == 'approved_20':
        return _joint_warp(base,((108,50,64,72,4*pulse,22*pulse),))
    if name == 'approved_14':
        angle=4*math.pi*_smooth(u) if active else 0
        base=_joint_warp(base,((115,143,22,39,10*swing,9*pulse),
                              (135,53,48,58,3*swing,3*pulse)))
        if active:
            pen=ImageDraw.Draw(base)
            for cx,cy,r in ((55,154,28),(176,154,32)):
                for i in range(3):
                    a=angle+2*math.pi*i/3
                    _pen_line(pen,((cx,cy),(cx+r*math.cos(a),cy+r*math.sin(a))),
                              fill=round(175*window),width=2)
        # Roll the rider and wheels together, leaving the baseline in place.
        if active:
            floor = Image.new('L', base.size)
            floor.paste(base.crop((0, PAD+185, base.width, base.height)), (0, PAD+185))
            ImageDraw.Draw(base).rectangle((0,PAD+185,base.width,base.height), fill=0)
            base = _add(floor, base, dx=-18*(1-_smooth(u)))
        return base
    if name == 'approved_23':
        # Preserve every pixel outside the screen, including the user's fixed
        # typing person. The completed code remains during the idle stage.
        progress=min(16,frame//2) if active else 16
        screen=figure_motion._screen_code(source,progress)
        if frame >= 32:
            overlay=Image.new('L',source.size)
            d=ImageDraw.Draw(overlay)
            opacity=round(230*math.sin(2*math.pi*u)**2)
            d.line((242,101,241,109),fill=opacity,width=3)
            overlay=ImageChops.multiply(overlay,figure_motion.laptop_screen_mask(source.size))
            screen=ImageChops.lighter(screen,overlay)
        return _canvas(screen)
    if name == 'approved_28':
        angle=16*pulse
        base=_add(base,parts['can'],angle=angle,pivot=(141,94))
        if active and pulse > .08:
            a=math.radians(angle)
            x,y=141+61*math.cos(a)-18*math.sin(a),94+61*math.sin(a)+18*math.cos(a)
            pen=ImageDraw.Draw(base)
            for i in range(5):
                p=(u*7+i/5)%1
                xx=x+(232-x)*p
                yy=y+(138-y)*p-10*math.sin(math.pi*p)
                _pen_line(pen,((xx,yy),(xx+2,yy+3)),fill=round(190*pulse),width=2)
        return base
    if name == 'approved_29':
        base=_add(base,parts['bubble_left'],dy=-16*pulse,dx=-3*swing)
        base=_add(base,parts['bubble_right'],dy=-23*pulse,dx=3*swing)
        # Individual water strokes advance down the stream, without shifting
        # the person, tub, shower head or the original silhouette.
        overlay=Image.new('L',base.size); d=ImageDraw.Draw(overlay)
        for i,(x,y) in enumerate(((212,26),(220,39),(208,44),(226,50),(203,55),(215,64))):
            p=(u*(3 if active else 1)+i/6)%1
            opacity=round((220*window if active else 55*math.sin(math.pi*u)**2)
                          *math.sin(math.pi*p)**2)
            _pen_line(d,((x-9*p,y+12*p),(x-9*p-3,y+12*p+5)),fill=opacity,width=2)
        return ImageChops.lighter(base,overlay)
    if name == 'approved_30':
        base=_add(base,parts['pan'],angle=-23*pulse,pivot=(177,99))
        return _add(base,parts['steam'],dx=9*swing,dy=-18*pulse,opacity=1-.45*pulse)
    if name == 'approved_34':
        angle=12*swing
        base=_add(base,parts['rod'],angle=angle,pivot=(143,109))
        a=math.radians(angle)
        tip=(143+103*math.cos(a)+101*math.sin(a),109+103*math.sin(a)-101*math.cos(a))
        hook=(246+12*swing,154-12*pulse)
        # Keep the original line and hook, shearing them between the moving
        # rod tip and hook instead of substituting a new drawing at rest.
        sy=(hook[1]-tip[1])/146
        shear=(hook[0]-tip[0])/146
        tx=tip[0]+PAD-shear*(8+PAD)-(246+PAD)
        ty=tip[1]+PAD-sy*(8+PAD)
        line=parts['line'].transform(base.size,Image.Transform.AFFINE,
            (1,-shear/sy,shear*ty/sy-tx,0,1/sy,-ty/sy),Image.Resampling.BICUBIC)
        return ImageChops.lighter(base,line)
    if name == 'group_selfie':
        centers=((497,26),(405,84),(585,102),(460,130),(679,135))
        for i,pivot in enumerate(centers):
            sparkle=(math.sin(math.pi*max(0,min(1,(u-i*.07)/.55)))**2 if active
                     else math.sin(math.pi*u)**2*.06*(1+.3*math.sin(2*math.pi*u+i)))
            base=_add(base,parts['star'+str(i)],pivot=pivot,scale=1+.42*sparkle,
                      angle=14*sparkle,opacity=1-.35*sparkle)
        return base
    return _canvas(source)

"""Screen-confined code strokes for the approved laptop drawing."""
from PIL import Image, ImageChops, ImageDraw


def laptop_screen_mask(size):
    """Interior of the approved laptop, excluding every outline stroke."""
    width, height = size
    mask = Image.new('L', size)
    ImageDraw.Draw(mask).polygon([(width*x/332, height*y/161) for x,y in
                                 ((236,77),(274,74),(255,116),(224,116))], fill=255)
    return mask


def _screen_code(alpha, progress_frame):
    width,height=alpha.size
    overlay=Image.new('L',alpha.size)
    pen=ImageDraw.Draw(overlay)
    for index,(length,v) in enumerate(((.53,.2),(.78,.46),(.4,.73))):
        progress=max(0,min(1,(progress_frame-1-index*4)/4))
        if not progress:
            continue
        opacity=210
        xleft=(236+(224-236)*v+3)*width/332
        xright=(274+(255-274)*v-3)*width/332
        y=(77+(116-77)*v)*height/161
        pen.line((xleft,y,xleft+(xright-xleft)*length*progress,y-1*height/161),
                 fill=opacity,width=max(1,round(width/85)))
    overlay=ImageChops.multiply(overlay,laptop_screen_mask(alpha.size))
    return ImageChops.lighter(alpha,overlay)

"""Render every current figure at one scale, retaining the user's review numbers."""
import argparse
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smith_agents import artwork, figure_actions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('/tmp/smith-figure-review.png'))
    parser.add_argument('--frame', type=int, default=0)
    args = parser.parse_args()
    main_poses = sorted(artwork.SESSION_FIGURES - set(artwork.HELPER_FIGURES),
                        key=lambda name: int(name.removeprefix('approved_')))
    items = list(enumerate(main_poses, 1)) + list(zip((14, 15, 18, 19), artwork.HELPER_FIGURES))
    font_path = Path(__file__).resolve().parent.parent / 'smith_agents/fonts/SpaceGrotesk.ttf'
    title = ImageFont.truetype(str(font_path), 30)
    label = ImageFont.truetype(str(font_path), 19)
    small = ImageFont.truetype(str(font_path), 16)
    canvas = Image.new('RGB', (1340, 1100), '#10141b')
    pen = ImageDraw.Draw(canvas)
    pen.text((30, 23), f'All {len(items)} current figures', font=title, fill='white')
    pen.text((30, 65), f'Same 4x widget scale | Frame {args.frame} | Review numbers 16 and 17 removed',
             font=small, fill='#aeb8c6')
    for slot, (number, name) in enumerate(items):
        row, col = divmod(slot, 5) if slot < len(main_poses) else (3, slot-len(main_poses))
        x, y = 30+col*260, 112+row*238
        pen.rounded_rectangle((x, y, x+244, y+222), radius=12,
                              fill='#191f29', outline='#303a48', width=1)
        drawing = artwork.render(name, 'white', 224, 168, frame=args.frame)
        canvas.paste(drawing, (x+10, y+2), drawing)
        pen.text((x+122, y+190), f'{number:02d}  {figure_actions.LABELS[name][0]}',
                 font=label, fill='white', anchor='mm')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.out)
    print(args.out.resolve())


if __name__ == '__main__':
    main()

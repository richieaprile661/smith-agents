# Provider badges

Claude and Codex Downloads images were prepared as transparent cutouts using the
built-in imagegen tool on 2026-09-10. The supplied files remain unchanged. Runtime rendering
scales these PNGs to a 16-logical-pixel corner badge, independently of figure size.

- `codex.png`: source `codex-logo.webp`.
- `claude.png`: source `st,small,507x507-pad,600x600,f8f8f8.jpg`.
- `hermes.svg`: the [SVG version](https://assets.zonalogo.com/technology/hermes-agent.org/logo-black-1778340045798-84487.svg)
  of the portrait in the user's `hermes-agent-logo-png-svg.webp` from Downloads,
  retrieved on 2026-09-17 from the [same logo listing](https://zonalogo.com/hermes-agent-logo-png-svg).
  `hermes.png` is a transparent 1024px CairoSVG render of this vector. The original
  Downloads file is unchanged. This replaces the earlier generated cutout.
  Runtime rendering uses the portrait's transparent mask in pale blue on dark
  themes and dark ink on E-ink, fitting the visible mark to 78% of the same
  16px badge / 18px provider-lamp canvas used by the other logos. The top selector
  dims when inactive; session badges stay bright. Each mark is resized once to
  its display size; the corner badge does not rescale an already rendered lamp.
- `deepseek.png`: the whale mark cut out of the DeepSeek Harness desktop app's own
  icon (`DeepSeek Harness.app/Contents/Resources/icon.png`, version 0.2.2-rc.1) on
  2026-11-26, without an image model. Ink coverage is each pixel's distance below
  the white tile, limited to the tile's inner 76% so its edge and shadow drop out.
  The mark is cropped to its ink and centred on a square transparent canvas with
  about 4% padding. The app's file is unchanged. Runtime rendering uses only the
  mark's transparent mask, in white on dark themes and dark ink on E-ink; the
  selected lamp's halo and the usage fields carry DeepSeek blue `#4D6BFE`.

Final prompt set:

## Codex

Use case: background-extraction. Edit target: the attached user-supplied
purple/blue Codex terminal logo. Prepare a clean transparent PNG icon for a tiny
UI badge. Remove the entire gray/white checkerboard background, including the
checkerboard showing through the > chevron and underscore cutouts. Preserve the
exact existing purple/blue gradient cloud silhouette, proportions and cutout
shapes. Actual alpha transparency outside the logo and in the cutouts, not a
drawn checkerboard. No text, no added details, no shadow, no redesign. Center the
extracted logo on a square transparent canvas with about 4% padding.

## Claude

Use case: background-extraction. Edit target: the attached user-supplied orange
pixel Claude character sticker. Prepare a clean transparent PNG icon for a tiny
UI badge. Remove the light gray/white background, white sticker border, and gray
sticker shadow. Keep only the exact existing orange pixel character silhouette
and its two black square eyes. Preserve orange color, proportions, rectangular
corners, and gaps between the legs. Actual alpha transparency outside the
character and between the legs, not a drawn checkerboard. No text, no added
details, no shadow, no redesign. Center the extracted character on a square
transparent canvas with about 4% padding.

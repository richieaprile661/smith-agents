# Provider badges

User-supplied Downloads images prepared as transparent cutouts using the built-in
imagegen tool on 2026-09-10. The supplied files remain unchanged. Runtime rendering
scales these PNGs to a 16-logical-pixel corner badge, independently of figure size.

- `codex.png`: source `codex-logo.webp`.
- `claude.png`: source `st,small,507x507-pad,600x600,f8f8f8.jpg`.

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

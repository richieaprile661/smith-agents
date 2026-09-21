# Animated code portraits

Record of the Matrix theme's animated session portraits. The visual design was
approved on 2026-09-19 and shipped from commit `d0e2cdf` onward; this file
replaces the pre-implementation handoff that pointed at untracked design output.

## What shipped

- `smith_agents/portraits.py` renders the portraits in the shared Pillow path
  used by expanded rows (`core.render_row`) and tucked strips (`tucked._agent_cell`).
- `smith_agents/assets/portraits/` holds the six approved faces (Smith, Brown,
  Jones, Johnson, Jackson, Thompson) plus nine Smith session expressions.
  `manifest.json` records each file's SHA-256 and original generation source.
- The Matrix theme is the default and uses the portraits. Claude and E-ink keep
  the line-art figures.
- `tests/test_portraits.py` covers identity stability, state-to-colour mapping,
  freeze/thaw and immutable tinting.

## State behavior

| Widget state | Portrait colour | Motion rate |
| --- | --- | --- |
| `done` | Green | Slow, 0.1× |
| `needs` | Yellow | Medium, 1× |
| `working` | Red | Fast, 2.5× |
| `closed` | White/gray | Frozen, 0× |

Portrait colour is independent of the theme's status text and provider lamps.
Reduced motion is respected. Faces stay with a session across state changes,
rescans and row reordering.

A new session takes the face least used among the open sessions of its kind,
main sessions from Smith's nine expressions and helpers from his five
colleagues, with a hash of the session id breaking ties. Two sessions side by
side never share a face until the pool runs out.

## Rendering notes

- Expanded portraits occupy 42 logical pixels; tucked edge portraits 40.
- The source is cropped once to a stable square around its green content, then
  downscaled by progressive halving for compact sizes. The artwork itself is
  stationary; motion is a multiplicative descending light mask.
- Tint is applied from an immutable untinted base, so repeated colour changes
  do not accumulate error.
- Prepared artwork is cached by identity, size, scale and tint. No PNG decoding
  or filesystem reads happen per paint.

The original generation prompts, browser reference (`animate.js`) and mockups
live in the untracked local `output/imagegen/generated-pixel-code/` and
`output/native-widget-mockup/` directories.

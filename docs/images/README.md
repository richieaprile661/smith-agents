# README images

`tools/render_readme_images.py` creates the README artwork from the current
widget renderer, with isolated settings and sample data. It doesn't read real
credentials, account readings, or conversation files.

The samples are `api-service` waiting for Claude approval, `storefront` running
Codex tests, and `design-system` ready. The hero and session detail image focus on
the first two. Claude usage is 42% / 18%; Codex usage is 71% / 31% (5h / week).

| Image | What it shows |
| --- | --- |
| `github-signal-field-hero.png` | Brand, tagline, and the current full widget. |
| `github-signal-field-work.png` | Session identity, context, activity, and folded Read more controls. |
| `github-signal-field-usage.png` | Both providers' 5h and weekly Signal Fields and glowing logos. |
| `github-signal-field-tuck.png` | Horizontal and vertical strips with attached usage drawers. |
| `github-signal-field-themes.png` | Claude, Matrix, and E-ink using the same layout. |

Canvases are 1600 pixels wide. The main UI renders at zoom 2.5; the three-theme
comparison uses 1.45. Fonts follow the platform used to render (the current images
were generated on macOS). Each theme renders in a separate child process.

Regenerate these images after interface changes. They illustrate the UI with
sample data and aren't release artifacts. See
[Regenerate the README images](../development.md#regenerate-the-readme-images).

The Signal Field images use new filenames so browsers do not reuse cached artwork
from the previous widget design. Update README links when introducing a new image set.

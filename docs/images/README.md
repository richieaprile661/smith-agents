# README images

`tools/render_readme_images.py` renders these images with the widget's own
renderer. All of them use the same three sample sessions: `api-service` waiting for
approval, `storefront` running tests in Codex, and `design-system` ready. No
credentials, account readings, or conversation files are read.

| Image | What it shows |
| --- | --- |
| `github-hero.png` | The wordmark, the tagline, and the full widget. |
| `github-work.png` | One session's details, with callouts for the approval, the running command, the last request, the latest message, and the window link. |
| `github-tuck.png` | The strip tucked against the top edge of a screen, over a code editor, with one agent's panel open. |
| `github-themes.png` | The same sessions in the Claude, Matrix, and E-ink themes. |

The canvases are 1760 pixels wide, and the widget renders at zoom 2.5, so the
images stay sharp and readable at GitHub's README width. These images illustrate the interface with
sample data. They aren't release artifacts. When the interface changes, regenerate
them. For the command, see
[Regenerate the README images](../development.md#regenerate-the-readme-images).

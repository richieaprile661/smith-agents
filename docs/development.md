# Development and artwork

## Code layout

The widget is split into shared code and one backend for each platform:

| File | Responsibility |
| --- | --- |
| `smith_agents/core.py` | Shared Claude data, themes, Pillow drawing, and layout |
| `smith_agents/app.py` | Shared controller, polling, interactions, and configuration |
| `smith_agents/runtime.py` | Selects the backend for the current operating system |
| `smith_agents/activity.py` | Bounded tool identities, output, lifecycle, and helper transcript parsing |
| `smith_agents/claude_activity.py` | Observes Claude task events and status-line metadata |
| `smith_agents/codex_sessions.py` | Verifies Codex ownership and reads local thread activity |
| `smith_agents/codex_hooks.py` | Optional trusted Codex lifecycle, tool, and permission snapshots |
| `smith_agents/platform_win32.py` | Windows surface, tray, startup, and process APIs |
| `smith_agents/platform_darwin.py` | AppKit panel and menu, Keychain, LaunchAgent, and Unix processes |

Keep theme colors, fonts, and geometry in the theme definitions in `core.py`.
Bundled artwork lives in `smith_agents/assets/`, and fonts live in
`smith_agents/fonts/`.

## Figures

Each session is drawn as a figure whose pose depends on its state. The approved
figure numbers and their states are listed in
`smith_agents/assets/approved/manifest.json`:

| State | Figures |
| --- | --- |
| Working | 17, 23, 35 |
| Needs you | 3, 7, 8, 12, 34 |
| Ready | 30, 14, 20, 28, 29 |
| Closed | 14, 20, 28, 29 |

Ready sessions can show cooking, cycling, pumping, watering, or showering.

The widget follows these rules when it assigns and animates figures:

- Sessions in the same state get different figures, and every figure is used
  before any repeats.
- A session keeps its figure through refreshes, scrolling, and reordering until
  its state changes.
- The first time a figure appears, it plays one 3.2-second action and then settles
  into a subtle idle. It doesn't repeat on a timer or when sessions are rescanned.
  A state change, or scrolling to a figure you haven't seen yet, starts a new
  action.
- On the laptop figure, only the code and the blinking caret on the screen move.
- The selfie in the header flashes its stars once, then twinkles quietly.

The renderer uses the approved images as theme-colored masks and resamples them
for the display density. The source images stay unchanged.

Helpers use the selected Little Smith character in six poses: working, reviewing,
testing, needs input, finished, and activity unknown. `artwork.helper_pose` selects
from current tool evidence and lifecycle state; it does not classify the task
title. Concurrent tools with different categories use the general working pose.
Permission requests take priority, and failed/stopped helpers never get the
successful result pose. A pose change restarts its entrance animation.

The helper uses the approved original-style study in `smith-helper-states.png`:
a single flowing, open body contour with no legs, feet, pouch, or floor line.
`helper_motion.py` animates hands and props using bounded local deformations while
keeping the lower silhouette fixed. The new atlas is separate from the original helper
pair, and helper geometry does not change the size of the main figures.

Export a self-contained comparison of all six animations, with pause, replay,
and compact-size previews:

```sh
python tools/render_helper_preview.py
```

Open `design/little-smith-animated.html` in a browser. Its frames come from the
same renderer used in the native widget, and it uses no account or session data.

## Preview the figures

To review all 13 poses and the selfie with sample data, run the preview:

```sh
.venv/bin/python -m smith_agents.animation_preview
```

Click a row to replay its action. On macOS, choose **Replay all figure actions**
in the preview's menu-bar menu to restart the set. The preview uses the shared
renderer and its own temporary settings. Add `--smoke-test` to check native
scrolling and replay, and then exit.

## Run the tests

To run the shared tests, use the following command:

```sh
python -m unittest discover -s tests -v
```

`test_subagent_activity.py` covers parallel tool attribution, yielded commands,
nested helpers, late task events, disconnected observers, process reuse,
completion retention, bounded previews, and native renderer geometry.

The workflow in `.github/workflows/test.yml` runs these tests and starts the
widget with sample data on Windows and macOS. For the VS Code bridge and the
approval review, see [Windows setup](windows.md). For the Mac smoke test and
packaging, see [Mac setup](macos.md).

## Regenerate the README images

The images in `docs/images/` come from the widget's own renderer, using one set of
sample sessions for every image. After an interface change, rebuild them on
Windows:

```powershell
py tools/render_readme_images.py
```

Render on Windows because the Claude theme uses Segoe UI, a Windows system font.
The script renders each theme in a separate process with temporary settings. It
doesn't read credentials, transcripts, or account readings. For what each image
shows, see [docs/images/README.md](images/README.md).

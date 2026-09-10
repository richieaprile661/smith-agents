# Development and artwork

The platform split is:

| File | Responsibility |
| --- | --- |
| `smith_agents/core.py` | Shared Claude data, themes, Pillow drawing, layout |
| `smith_agents/app.py` | Shared controller, polling, interactions, configuration |
| `smith_agents/platform_win32.py` | Windows surface, tray, startup, process APIs |
| `smith_agents/platform_darwin.py` | AppKit panel/menu, Keychain, LaunchAgent, Unix processes |

Keep theme colors, fonts, and geometry in the existing theme definitions.
The design handoffs live in `design/`; bundled runtime artwork lives in
`smith_agents/assets/` and fonts in `smith_agents/fonts/`.

Approved figure numbers and state assignments live in
`smith_agents/assets/approved/manifest.json`: working 17/23/35, approval
3/7/8/12/34, Ready 30/14/20/28/29, and closed 14/20/28/29. Ready sessions
can show cooking, cycling, pumping, watering, or showering. The widget spreads
figures across sessions in the same state, using unused choices before repeating.
A session keeps its assigned figure through refreshes, scrolling, and reordering
until its state changes. On first visible appearance, the figure plays
one activity lasting 3.2 seconds, then continues a very subtle idle. In the main console, the action
does not repeat on a timer or on session rescans; changing state starts a new
action when that figure becomes visible. Scrolling to a previously unseen figure
starts its action then. On the PC figure, only code and a blinking caret inside
the laptop screen animate. The top-bar selfie flashes its stars once, then
quietly twinkles. The renderer uses the approved pixels as theme-colored masks
and resamples for display density; the source images are preserved unchanged.

Review all 13 poses and the selfie with isolated sample data:

```sh
.venv/bin/python -m smith_agents.animation_preview
```

Click a row to replay its action. On macOS, choose **Replay all figure actions**
in the preview's menu-bar menu to restart the set. Scroll to see the remaining
poses. This preview uses the shared widget renderer and separate temporary
settings. Add `--smoke-test` to verify native scrolling and replay, then exit.


```sh
python -m unittest discover -s tests -v
```

The workflow in `.github/workflows/test.yml` runs the shared tests and launches
the widget with sample data on Windows and macOS. The VS Code launcher and
in-widget approval dialog support both platforms; see [Windows bridge setup](windows.md). The Mac native smoke test and packaging commands are documented in
[docs/macos.md](macos.md).

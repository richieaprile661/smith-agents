# Smith Agents on macOS

The Mac version uses the same artwork, themes, readings, and controller as the
Windows version. AppKit, through PyObjC, supplies the transparent floating panel
and the menu-bar item. Tk and the Windows APIs aren't loaded on macOS.

## Install

To install Smith Agents, use the one-line command in the
[README](../README.md#install). It creates **Smith Agents.app** in your user
Applications folder (`~/Applications`).

### Reopen after you quit

- Press **Command+Space**, search for **Smith Agents**, and press **Return**. You
  can also double-click the app in Finder, or drag it to the Dock.
- If the widget is only hidden, click **Show console** in its menu-bar menu.
- To reopen the app when you sign in, turn on **Start at login** in the same menu.

### Install from source

To run Smith Agents from a checkout, set up a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m smith_agents
```

After that setup, you can double-click `run.command` to start the widget.

To preview the widget without reading credentials or sessions, and without
contacting Anthropic, start it in demo mode:

```sh
.venv/bin/python -m smith_agents --demo
```

Demo mode uses temporary settings, sample readings, and example figures. Its
menu reads **Demo — sample data**. Changing the theme or size relaunches the demo
with the same temporary settings.

## Connect Claude

Install Claude Code, and sign in by running `claude` in Terminal. The widget uses
that login:

- With the default Claude configuration, the widget reads the
  `Claude Code-credentials` Keychain item. It falls back to
  `~/.claude/.credentials.json` only if that item doesn't exist.
- macOS might ask you to allow Keychain access. If you deny it, the widget stays
  disconnected.
- The widget never refreshes or changes Claude's credentials. If your login
  expires, renew it in Claude Code.

For sign-in instructions, click **Connect Claude Code** in the menu. After you sign
in, click **Refresh now**.

The widget respects `CLAUDE_CONFIG_DIR` for transcripts, session files, and
file-based credentials. Custom Keychain service names aren't supported. When you
select a custom directory, the widget doesn't fall back to your default account.

## Use the widget

You can control the widget in the following ways:

- Click the menu-bar figure for settings, refresh, visibility, and **Quit**.
  Right-click or Control-click the widget for the same menu.
- Click the percentage to cycle the visible limit.
- Click the bar or caret to fold the widget, and click the corner stroke to tuck
  it. Click the tucked figure to restore it.
- Drag the widget to move it. Docking uses each screen's usable area, including
  the menu bar, the notch, and the Dock. Saved positions recover after a monitor
  is disconnected.
- **Start at login** creates this app's LaunchAgent. Before you move or remove a
  source installation, turn it off from the same menu.

The menu-bar figure is a template image, so it follows the macOS appearance. An
`!` marks an error or a critical limit, so the warning doesn't rely on color.

### Agent rows

Click an agent to show its last request, model and source, timing, Git branch,
project folder, and latest message. Scroll long lists with the wheel, the
trackpad, or the **Up** and **Down** links in the footer. The header and footer
stay visible.

Session age and idle time are separate. Idle time is the time since the session's
transcript was last updated.

Each row says whether the session is a **Main agent** or a **Subagent**, and
whether its host window is **in front**, **in background**, **hidden**, or
**unknown**:

- A subagent shows its **parent window**, and its **Open** and **Hide** links act
  on that window.
- **In background** describes window focus. It doesn't mean that the agent
  stopped working.
- The state describes the host window, not the selected chat or terminal tab.
- Without Accessibility access or an unambiguous window match, the state stays
  **unknown**. Reading the state never asks for permission or changes focus.

### Context readings

Each agent and subagent shows a context reading under its name, such as
`313.6k / 1M`. When the capacity is known, the row also shows a percentage and a
fill bar.

The reading follows
[Claude Code's context calculation](https://code.claude.com/docs/en/statusline):
the most recent request's input tokens, plus cache-creation and cache-read tokens.
It doesn't include output tokens, and it doesn't add requests together.

- After compaction, the reading uses the recorded post-compaction size when it's
  available. Otherwise, it waits for the next request.
- A dash means that the recent transcript has no reading.
- Readings update after Claude writes usage data. They aren't a live count of text
  that's still streaming.
- A `?` capacity means that Claude hasn't reported that agent's capacity yet. The
  widget never assumes a model's default context window. To report capacities, see
  [Enable exact context capacity](#optional-enable-exact-context-capacity).

### Open and hide windows

**Open** brings the agent's window to the front, and **Hide** minimizes it while
the agent keeps running:

- The widget matches a window by a unique project title in the host app, or by the
  host app's only window. When more than one window matches, the widget leaves
  them alone. It can't select a particular terminal tab.
- For a VS Code agent, **Open** matches the agent's extension-host process to VS
  Code's window log, and then sends Claude's session link to that window. Both the
  process ID and the process start time must match. If the window can't be
  identified, the widget leaves other windows alone and reports that it can't open
  the session.

Window controls require Accessibility access. In **System Settings > Privacy &
Security > Accessibility**, allow Smith Agents, or Python if you run it from
source. Without that access, a VS Code session link can still open its chat, but
**Hide** isn't available. The widget never activates or hides a whole app as a
fallback.

### Approve requests

After you set up the [VS Code bridge](#vs-code-chats), supported permission
requests appear in the widget:

- **Approval needed** in the agent row is a link. The collapsed row also shows
  **Allow** and **Deny**, and the expanded row shows **Review & allow**.
- Both allow links open the complete tool input for review. **Allow once** grants
  only that request, **Deny** refuses it, and **Cancel** leaves it pending.
- Tool questions, plan approvals, and tools without supported direct approval stay
  in Claude's chat.
- A session that's only been quiet for a while never shows **Allow**.

### End a session

**End session** asks for confirmation, and then checks the session again before it
stops the process. The process start time must match. The widget doesn't stop a
process that other sessions or listed subagents share; stop those agents one at a
time inside Claude. A subagent can't stop its parent process. Other independent
sessions keep running.

## Optional: Enable exact context capacity

The context capacity comes from optional bridges that you configure from a source
checkout. The commands in this section assume that you built the app into `dist/`.
For build steps, see [Build and verify](#build-and-verify).

### Terminal sessions

Claude's [status-line data](https://code.claude.com/docs/en/statusline) reports the
main session's `context_window.context_window_size`. Its `subagentStatusLine` feed
reports each task's `contextWindowSize` separately, in Claude Code 2.1.205 or
later.

The status-line bridge records only session and task IDs, the model, the capacity,
and the update time, under `~/.claude/widget-context`. It passes the original input
through to your existing status-line commands, and keeps their output and settings.
It doesn't save transcript content or credentials.

From the repository, turn on the feed:

```sh
.venv/bin/python tools/configure_context.py --executable 'dist/Smith Agents.app/Contents/MacOS/Smith Agents'
```

Claude reloads its settings on its own. Capacity appears after the next status-line
update in each session. Keep the following in mind:

- Project-level status-line settings might take precedence over the user-level
  bridge.
- Keep the app at the configured path. If you move it, rerun the command with the
  new path.
- To restore the original commands, run the same command with `--remove`. The
  original settings are backed up in
  `~/.claude/widget-context/settings-backup.json`.

The status-line bridge works with the CLI, including in VS Code's integrated
terminal. The VS Code chat panel uses the separate bridge in the following section.
A session without a capacity snapshot shows `?`; it never borrows another session's
limit.

Claude can append `[1m]` to a model name in its status-line feed and leave it out
of the transcript. The widget ignores that suffix when it matches model names, and
still takes the capacity from the value reported for that session or subagent.

### VS Code chats

From the repository, install the optional VS Code launcher:

```sh
.venv/bin/python tools/configure_vscode_context.py --executable 'dist/Smith Agents.app/Contents/MacOS/Smith Agents'
```

Reopen the Claude chat, or reload its VS Code window, and then let it finish one
response.

The launcher reads the same `result.modelUsage[model].contextWindow` value that
Claude's VS Code extension uses. It matches the main conversation's model and
session ID, and writes only the capacity metadata to `widget-context`. The
protocol, prompts, replies, error output, and cancellation pass through unchanged,
and no conversation text is saved. The integration was checked against extension
version 2.1.263.

The same launcher sends pending permission requests to the widget over a private
local Unix socket:

- Request details stay in memory. The connection file on disk holds only the
  session ID, the process ID, the socket path, and a random connection token.
- Answered or cancelled requests disappear.
- The first reply wins, whether it comes from VS Code or the widget. The other
  one can't override it.
- Chats that were already open must be reopened before they can send requests.

The installer sets `claudeCode.claudeProcessWrapper` in VS Code's user settings.
It keeps any existing executable wrapper and doesn't edit the installed extension:

- It backs up your settings first. It supports plain JSON only, and refuses
  settings that contain comments or trailing commas without changing them.
- To configure a different VS Code profile, use `--settings`.
- New chats that use the launcher can start in Manual permission mode, unless
  you've selected a mode or set `claudeCode.initialPermissionMode`.

Capacity arrives at the end of each response, while used tokens update from the
transcript during work. Subagent responses never overwrite the main session's
capacity. The launcher doesn't report capacities for VS Code subagents; CLI
subagents use their own status-line feed.

To restore the previous launcher, run the same installer with `--remove`, and then
reopen the chat. If you move the app, reinstall the bridge with the new path. A chat
that's already running keeps its launcher until you reopen it.

## Local files

Settings, cached usage, the instance lock, and a size-limited diagnostic log are
stored in your local application data folder. Upgrades keep the same location.
The folder doesn't hold a copy of your OAuth token.

**Start at login** manages the app's LaunchAgent registration. Startup
registrations from before the rename to Smith Agents keep working.

For isolated development and tests, `SMITH_AGENTS_CONFIG_DIR` overrides the
widget's settings, cache, and log folder. It doesn't change where Claude's
credentials are read from.

## Build and verify

Install the build dependencies, run the tests and the native smoke test, and then
build the app:

```sh
.venv/bin/python -m pip install -e '.[mac-build]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/smoke_macos.py
.venv/bin/python tools/build_macos.py
```

The native smoke test opens its own demo window. It exercises AppKit mouse events,
tabs, reading selection, folding, tucking, visibility, docking, and menus, and then
closes. It saves only the app's rendered view, to `build/previews/mac-native.png`,
and doesn't capture the rest of the desktop.

The build creates `dist/Smith Agents.app` and `dist/Smith-Agents-macOS.zip`. The
app bundles the artwork, fonts, Python, Pillow, PyObjC, and psutil, and leaves out
Tk and pystray, so it runs without the source tree or a virtual environment.

- The build targets the Mac that it runs on. It was tested on Apple Silicon with
  macOS 15.5. To build for Intel, run it on an Intel Mac. It doesn't produce a
  universal binary.
- The build is ad-hoc signed. It isn't signed with a Developer ID or notarized.

### Prepare a signed build for distribution

Before you distribute a build outside local testing, do the following:

1. Test a clean install on another Mac. Include a real Claude sign-in, a denied
   Keychain prompt, a theme and size relaunch, sleep and wake, and login startup.
2. Run the shared tests on Windows.
3. Sign the embedded executables, the frameworks, and the app with a Developer ID
   certificate. Use the hardened runtime and the entitlements that the bundled
   Python runtime requires.
4. Verify the signature, notarize the app with Apple's notary service, and staple
   the accepted ticket before you create the ZIP file.
5. Include the MIT license and the font license notices.

Keep developer certificates, signing keys, and notary credentials outside the
repository.

## References

For the code layout, see [Development and artwork](development.md#code-layout).

The original [codenotch](https://github.com/vinzdg/codenotch) inspired the widget.
Its platform behavior was consulted for the Mac version, but none of its code was
copied.

- [PyObjC](https://pyobjc.readthedocs.io/en/latest/)
- [Claude credential management](https://code.claude.com/docs/en/authentication#credential-management)
- [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

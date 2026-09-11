# macOS

The Mac version uses the same Pillow artwork, themes, readings, and console
controller as Windows. AppKit supplies the transparent floating panel and the
menu-bar item through PyObjC. Tk and Windows APIs are not loaded on macOS.

## Try the local app

The build produces `dist/Smith Agents.app` and `dist/Smith-Agents-macOS.zip`.
Copy the app into Applications and open it. Python is bundled inside the app.
The first build is for **Apple Silicon**, tested on **macOS 15.5**. Build on an
Intel Mac to produce an Intel package; this build is not a universal binary.

This is a local test build, ad-hoc signed rather than Developer ID signed and
notarized. A public release needs the signing steps below. No public download
has been published yet.

## Connect Claude

Install Claude Code and sign in using `claude` in Terminal. The widget reads its
existing login. For the default Claude configuration, it checks the
`Claude Code-credentials` Keychain item, then falls back to
`~/.claude/.credentials.json` only if the item is absent. macOS may ask you to
allow Keychain access. Denying access leaves the widget disconnected.

Choose **Connect Claude Code…** in the menu for sign-in instructions, and
**Refresh now** after signing in. The widget never refreshes or modifies Claude's
credentials. An expired login must be renewed by Claude Code.

`CLAUDE_CONFIG_DIR` is respected for transcript/session files and file-based
credentials. Custom Keychain service names are not yet supported: the widget
does not fall back to your default account when a custom directory is selected.

## Controls

Click an agent to expand its request, model/source, timing, branch, project folder,
and latest message. The wheel/trackpad and footer **Up / Down** controls scroll
long lists while keeping the header and footer visible. Session age and idle time
are separate; idle measures time since the transcript was updated.

Rows also distinguish **Main agent** from **Subagent**, with the host window
shown as **in front**, **in background**, **hidden**, or **unknown**. Subagents
show their **parent window** state, and Open/Hide targets that parent window.
Background describes window focus, independently of whether the agent is
working. This tracks the host window, not the selected chat or terminal tab.
Without Accessibility access or an unambiguous match, the state stays unknown;
reading the indicator never requests permission or changes focus.

Each agent and subagent shows a full-width context reading beneath its identity,
such as `313.6k / 1M`, with the percentage and a fill indicator when capacity is known.
This uses the most recent request's input tokens plus cache creation and cache
read tokens, following [Claude Code's context calculation](https://code.claude.com/docs/en/statusline).
It excludes output tokens and does not sum requests. After compaction, the
reading uses the recorded post-compaction size when available, or waits for
the next request. A dash means no reading is available in the recent transcript.
Readings update after Claude writes usage data; they are not a live count of
text currently streaming. A `?` denominator means Claude has not reported that
agent's capacity yet; the widget never assumes a model's default window.

### Enable exact context capacity

Claude's [status-line data](https://code.claude.com/docs/en/statusline) reports
the main session's `context_window.context_window_size`. Its
`subagentStatusLine` feed reports each task's `contextWindowSize` separately
(Claude Code 2.1.205 or later). The widget's optional bridge records only session
and task identifiers, model, capacity, and update time under
`~/.claude/widget-context`. It passes the original input through to your existing
status-line commands and preserves their output and settings. No transcript
content or credentials are saved by the bridge.

After building the Mac app, enable the feed from the repository:

```sh
.venv/bin/python tools/configure_context.py --executable 'dist/Smith Agents.app/Contents/MacOS/Smith Agents'
```

Claude reloads its settings automatically. Capacity appears after the next
status-line update in each session. Project-level status-line overrides may
take precedence over the user-level bridge. Keep the app at the configured
path; rerun this command with its new path after moving it. To restore the
original commands, run the same command with `--remove`. The original settings
are backed up in `~/.claude/widget-context/settings-backup.json`.

The status-line bridge works with the CLI, including VS Code's integrated
terminal. The VS Code graphical chat panel uses the separate bridge below.
A session without a capacity snapshot continues to show `?` rather than
borrowing another session's limit.

Claude may append `[1m]` to a model name in its status-line feed and omit it in
the API transcript. The widget ignores that selector when matching model names;
it still takes capacity from the reported value for that session or subagent.

### Enable capacity for VS Code chats

After building, install the optional Mac VS Code launcher:

```sh
.venv/bin/python tools/configure_vscode_context.py --executable 'dist/Smith Agents.app/Contents/MacOS/Smith Agents'
```

Reopen the Claude chat (or reload its VS Code window), then finish one response.
The launcher observes the same `result.modelUsage[model].contextWindow` value
used by Claude's VS Code extension. It matches the main conversation's model
and session ID, and writes only the capacity metadata into `widget-context`.
The protocol, prompts, replies, stderr, and cancellation pass through; no
conversation text is saved by the bridge. The VS Code protocol integration
was checked against extension version 2.1.263.

The same launcher exposes pending tool-permission requests to the widget over
a private local Unix socket. Request details remain in memory; the on-disk
connection descriptor contains only the session ID, process ID, socket path,
and a random connection token. Answered or cancelled requests disappear. A
reply in either VS Code or the widget wins once; the other view cannot override
it. Existing chats must be reopened after installing this update to enable
the permission connection.

This sets `claudeCode.claudeProcessWrapper` in VS Code's user settings, preserving
any existing executable wrapper. It does not edit the installed extension.
The installer backs up the settings and supports plain JSON; it refuses settings
with comments or trailing commas without changing them. Use `--settings` for a
different VS Code profile. New wrapped chats can start in Manual permission mode
unless you have selected a mode or set `claudeCode.initialPermissionMode`.

Capacity arrives at the end of a response, while used tokens still update from
the transcript during work. Subagent responses never overwrite the main
session's capacity; this launcher does not yet provide VS Code subagent
capacities. CLI subagents continue to use their own status-line feed.

Run the same installer with `--remove` to restore the previous launcher, then
reopen the chat. Moving the app requires reinstalling the bridge with its new
path. An already-running chat keeps its existing launcher until reopened.

- Click the menu-bar figure for settings, refresh, visibility, or Quit.
- Right-click or Control-click the console for the same menu.
- Click the percentage to cycle the visible limit.
- Click the bar/caret to fold the console; click the corner stroke to tuck it.
- Click the tucked figure to restore the console.
- Drag the console to move it. Docking uses each screen's usable area, including
  the menu bar/notch and Dock. Saved positions recover after a monitor disappears.
- **Start at login** creates this app's LaunchAgent. Turn it off from the same
  menu before moving/removing a source installation.
- **Open** on a VS Code agent matches its live extension-host process to
  VS Code's window log and sends Claude's session link to that window. Both
  the PID and process start time must match. If the window cannot be identified,
  the widget leaves other windows alone and reports that it cannot open the session.
  **Hide** minimizes only the recorded window, leaving Claude running; **Open**
  restores that same window. Matching uses a unique project title within the
  hosting application, or its sole window. Duplicate matches are left alone.
  Selecting a particular terminal tab remains unsupported.
- Individual window controls require **System Settings → Privacy & Security →
  Accessibility** access for Smith Agents (or Python when running from source).
  Without it, a scoped VS Code session link can still open its chat, but Hide is
  unavailable. No app-wide activation or hiding is used as a fallback.
- A confirmed permission request makes **Approval needed** clickable in the agent row.
  The collapsed panel also offers **Allow…** and **Deny**; the drawer offers
  **Review & allow…**. Both Allow links open
  the complete tool input for review; **Allow once** grants only that request,
  **Deny** refuses it, and **Cancel** leaves it pending. Both collapsed and expanded rows
  offer **Deny**. Tool questions, plan approval, and tools without supported
  direct approval stay in Claude's own chat. An inactivity-based waiting state
  alone never enables Allow.
- **End session…** rechecks the selected session after confirmation and refuses
  to stop a process shared with other sessions or listed subagents. Shared agents
  must be stopped individually inside Claude. Other independent sessions stay
  running. The drawer action requires a matching process start
  time. Child agents cannot terminate their parent process.

The menu-bar figure is a template image that follows macOS appearance. `!` marks
an error or a critical limit without relying on color in the menu bar.

## Source setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m smith_agents
```

`run.command` is a double-clickable launcher after that setup.

For a preview that does not read credentials or sessions or contact Anthropic:

```sh
.venv/bin/python -m smith_agents --demo
```

Demo mode uses temporary settings, sample readings, and the existing example
figures. Its menu says **Demo — sample data**. Theme and size changes relaunch
the demo with the same temporary settings.

## Local files

Settings, cached usage, the instance lock, and a bounded diagnostic log live in
your local application-data folder. Existing installations retain their storage
location across upgrades. This folder does not hold a copy of your OAuth token.

**Start at login** manages the app's LaunchAgent registration. Existing startup
registrations remain compatible across the branding change.

For isolated development/tests, `SMITH_AGENTS_CONFIG_DIR` overrides the widget's
settings/cache/log directory. It does not change Claude's credential location.

## Build and verify

```sh
.venv/bin/python -m pip install -e '.[mac-build]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/smoke_macos.py
.venv/bin/python tools/build_macos.py
```

The native smoke test opens its own demo window, exercises AppKit mouse events,
tabs, reading selection, folding, tucking, visibility, docking, and menus, then
closes. It saves only the app's rendered view to
`build/previews/mac-native.png`; it does not capture the rest of the desktop.

The build script bundles the existing artwork and fonts, Python, Pillow, PyObjC,
and psutil. It excludes Tk and pystray. The resulting app runs without the
source tree or virtual environment.

## Public release

Development checkpoint (2026-09-09): the local Apple Silicon app builds and
runs with live Claude usage. The unit suite and native AppKit smoke test
pass. The packaged context bridge also passes its stdin/stdout preservation
check. The exact-capacity feed is installed on the development Mac. A live
terminal session reported 1,000,000 tokens. VS Code's separate launcher now
captures capacities from its result stream and passes protocol preservation
tests; the user confirmed VS Code capacity readings on September 9. The
permission relay passes request matching, cancellation, duplicate-response,
subagent isolation, and full process-stream tests. Native Cancel, Allow once,
and Deny button dispatch is covered by the smoke test. A live multiline
permission request exposed a row-rendering crash; previews now flatten line
breaks while preserving the complete tool input. The live request renders
successfully, and the user confirmed the widget was working after the fix.
The model-name selector mismatch is fixed.
Subagent matching passes sample-data tests; verification with live subagents
is still pending. Windows regression testing,
distribution signing, and public publication remain pending. Codex support has
not been implemented.

Before distributing outside local testing:

1. Test a clean install on a second Mac, including real Claude sign-in and
   Keychain denial, theme/size relaunch, sleep/wake, and login startup.
2. Run the shared tests on Windows and confirm the existing Windows UI still
   works on the two machines used for the original app.
3. Sign the embedded executables/frameworks and app with Developer ID, using
   the hardened runtime and the entitlements required by the bundled Python
   runtime. Verify the result, notarize it with Apple's notary service, and
   staple the accepted ticket before creating the public ZIP.
4. Include the MIT and font license notices. Review tracked assets/history
   before making the repository public.

Developer certificates, signing keys, and notary credentials belong outside the
repository. The build script currently produces only a local ad-hoc signature;
it does not claim to produce a notarized release.

## Architecture and references

- `core.py`: shared data parsing, rendering, themes, and layout constants.
- `app.py`: shared polling, controls, configuration, and interaction state.
- `runtime.py`: selects the backend for this operating system.
- `platform_win32.py`: Windows surfaces, tray/menus, process APIs, startup, lock.
- `platform_darwin.py`: AppKit panel/menu, Keychain reader, Unix process identity,
  LaunchAgent, and file lock.

The original [codenotch](https://github.com/vinzdg/codenotch) inspired the Windows
widget. Its platform behavior was consulted for this port; its code was not
copied. The port retains this project's console and artwork.

References: [PyObjC](https://pyobjc.readthedocs.io/en/latest/),
[Claude credential management](https://code.claude.com/docs/en/authentication#credential-management),
[Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

## Reopening after quitting

The one-line installer creates **Smith Agents.app** in your user Applications
folder (`~/Applications`). Press **⌘ Space**, search **Smith Agents**, and press
Enter, or double-click the app in Finder. Drag it onto the Dock for quick access.

If the console is only hidden, use **Show console** in its menu-bar menu. Enable
**Start at login** there if you want the app to reopen when you sign in.

For a source checkout, `run.command` remains available as a launcher.

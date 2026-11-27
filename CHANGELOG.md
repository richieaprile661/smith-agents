# Changelog

The one-line installer installs the latest GitHub release, pinned by version
and checksum. Changes on `master` reach it only when a new version is released.

## 1.1.11 — 27 November 2026

### Fixed
- A usage reading is no longer shown after the limit it measured has reset.
  When a refresh fails, for example because Claude Code is signed out, the
  widget keeps its last reading; it went on drawing that number for as long as
  the refresh kept failing, so a reading from weeks ago looked like the number
  now. A reading is dropped once its window has reset since it was taken, and
  its place shows `—`. This applies to Claude and Codex readings. A reading
  less than two refresh intervals old is always shown.
- With no reading to show, the Usage tab says why (`Sign in to Claude`)
  instead of only "No usage reading available".
- The notice over a kept reading gives the day it was taken as well as the
  time (`Cached 19 Oct 23:12`), so an old reading no longer reads as today's.

## 1.1.10 — 26 November 2026

### Added
- A Claude session's row shows its step list: cells for the steps done, in
  hand, and waiting, with the count and the step in hand (`3/5 · Fix tax
  rounding`), and the whole list when the row is open. It is read from the
  session's own transcript, from the Claude desktop app's task list or Claude
  Code's `TodoWrite`.
- A working Claude session shows how long its turn has run (`Turn 02:14`),
  and a finished one how long its last turn took.
- An "Open sessions" panel under the rows lists the sessions left open in the
  Claude desktop app: how many are working, used today, and unused for one to
  three days, and each one unused for three days or more with a link that
  opens it. It reads only each session's title, folder, last use, and archived
  flag from the records the app keeps on this computer, and changes nothing;
  closing a session stays in the app.

### Changed
- Sessions of the same project sit together in the list, in the place of the
  project's oldest session, instead of being spread through it by start time.
  A session in a worktree counts as its project.

### Fixed
- A Claude helper that has finished no longer stays in the list as "Activity
  unknown" until its session closes. Helpers now end by handing their result
  back, not with the reply the widget waited for. A session with such a
  helper can be ended again.

## 1.1.9 — 26 November 2026

### Fixed
- Windows: "Open session" on a Claude desktop, ChatGPT desktop, or DeepSeek
  Harness row no longer answers "Could not identify this session's window".
  The app's window is brought forward, and a Claude or ChatGPT session is
  then opened by the app's own link, as on macOS. In 1.1.8 these rows could
  not be opened on Windows at all, whatever its notes said.

## 1.1.8 — 26 November 2026

### Added
- DeepSeek Harness, as a fourth provider. Its open sessions appear as rows
  with their state, model, and context use; a session counts as open only
  while the running app holds its lock. The Usage tab shows token totals from
  the logs the app saved on this computer, with today's share, and DeepSeek
  has its own lamp in the tucked bar. Reading the logs needs Python 3.14.
- The DeepSeek account balance, in the header, the Usage tab, the tucked
  drawer, and the tray. The API key the app keeps is read only to ask
  DeepSeek's balance endpoint; it is never stored, logged, or sent anywhere
  else, and redirects are refused.
- Sessions in the Claude desktop app's Code tab and in the ChatGPT desktop
  app are marked "Desktop", and Claude's are named by their session title.
  On macOS, "Open session" opens that session in its app. On Windows it
  brings the app's window forward.

### Fixed
- Claude sessions in a folder whose path has an underscore, a dot, or a space
  no longer stay on "working" after they finish: their transcript was looked
  for under the wrong name.
- A Claude session's state follows what Claude itself reports (idle, busy,
  waiting) where it reports it, instead of being worked out from the
  transcript.
- A session in a Claude worktree is named after its project, not the
  worktree's folder.

## 1.1.7 — 13 October 2026

### Fixed
- Windows: the widget no longer freezes (a black box around the side strip,
  nothing updating) or crashes after running for a while with Codex open.
  It found Codex's sessions through psutil's `open_files()`, which on
  Windows kills the threads it gives up waiting for; Codex's idle pipe to
  VS Code made it do so on nearly every scan. Codex's open files are now
  read without starting or killing any thread.
- Windows: the app icon in the Start menu and on login and desktop shortcuts
  is Smith's face on a dark tile, as on macOS, instead of the old white
  figure. The installer refreshes Windows' icon cache so it shows at once.

## 1.1.6 — 28 September 2026

### Fixed
- The dashboard closes the Hermes and Codex history databases after reading
  them, instead of holding them open until Python cleans up.
- Windows: a workspace keeps one dashboard id whether its path was recorded
  with forward or back slashes.
- The footer's "Open dashboard" and settings targets no longer share a pixel
  at the edge where they meet, so a click there opens only one of them.

## 1.1.5 — 23 September 2026

### Fixed
- Windows: the dashboard opens in its own window (WebView2), as on macOS,
  instead of an Edge app window that could keep reopening. Opening it again
  brings the same window forward. Without WebView2 it falls back to Edge.
- Windows: the dashboard's git history reads and the Hermes balance check no
  longer flash console windows.
- Windows: Hermes is found in `%LOCALAPPDATA%\hermes`, where Hermes keeps it
  there, so its balance and sessions appear.

## 1.1.4 — 23 September 2026

### Added
- Weekly allowance per day and per session in the dashboard, from the day the
  widget starts saving account readings. Each calendar day shows how much of
  the Claude and Codex weekly limits its sessions used, and the day panel
  shows the account's start and end reading. A rise while one session ran is
  exact; overlapping sessions split it by token weight, marked "~". Use from
  claude.ai or another computer stays outside local sessions. Readings are
  kept in `allowance-history.jsonl` in the widget's settings folder for 90
  days: percentages and reset times only.

## 1.1.3 — 23 September 2026

### Added
- A usage dashboard, opened from the widget in its own window: a month
  calendar of active sessions with each day's sources, a day breakdown beside
  it, and a projects panel, across Claude Code, Codex, and Hermes history.
- Codex credit balance in the header, the tucked drawer, the Usage tab, and
  the tray, marked "on credits" once the weekly limit is spent.
- A free Codex rate-limit reset, shown when one is available.
- The real Nous balance and spend behind Hermes, which now fills the Hermes
  usage tab.

### Changed
- The one-line installers install the published release wheel and verify its
  SHA256, instead of installing `master`.

### Fixed
- A new main session takes the least-used Smith expression among open
  sessions instead of a hash of its id, so faces stop repeating side by side.
- Tilt, Shout, and Chin Up keep their chins inside the portrait crop.

## 1.1.2 — 16 to 21 September 2026

### Added
- The Matrix theme, now the default, with animated code portraits: Smith with
  nine expressions for main sessions, and his colleagues Brown, Jones,
  Johnson, Jackson, and Thompson for helpers.
- Hermes Agent sessions and usage.
- Signal Field usage: 5-hour and weekly readings side by side in the header,
  one lit cell per percent.
- Tucking to all four screen edges, with compact usage drawers attached to
  the strip.
- On the tucked strip, a bar marks the session whose window is in front, and
  clicking a portrait brings that window forward.
- Live helper activity, with helpers shown compact under their parent.
- README images rendered from the widget itself, in light and dark.

### Fixed
- Clean shutdown on SIGTERM, so the single-instance lock is released.
- Stale launchers and duplicate widget relaunches.
- Session rows keep the slot they were created in.
- Every face is drawn at one size on one eye line; the over-glasses expression
  no longer loses its chin.
- The bottom hairline of a tucked cell no longer covers its edge bars at 1×.
- Window scans run off the UI thread.

## 1.1.1 — 16 September 2026

### Fixed
- Helper tracking.
- Windows placement stays on the monitor that holds the widget.

## 1.1.0 — 10 September 2026

First public release: Claude Code and Codex sessions, usage, and the tucked
strip on Windows and macOS.

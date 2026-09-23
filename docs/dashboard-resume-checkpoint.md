# Dashboard checkpoint — resume tomorrow

Saved 21 September 2026 at the user's request: “tomorrow save it.” Stop implementation work until the user resumes. No automatic restart or overnight work is requested.

## Start here

**The implementation exists in a separate worktree. It is NOT absent or lost.**

Worktree: `.claude/worktrees/dashboard-integration` (relative to the repository root). Removed on 23 September 2026 after the work was merged and pushed.

Branch: `worktree-dashboard-integration`, based on `83d29e5`. Git marks this worktree locked; preserve it. Changes are uncommitted. The main checkout has only the handoff documentation edits, not the dashboard implementation. Do not assess progress using only the main checkout.

Read [the approved implementation handoff](dashboard-implementation-handoff-opus-5.md) and inspect the worktree before any further coding. Do not rebuild the implementation from scratch.

## Saved implementation

- Modified: `smith_agents/app.py`, `core.py`, `platform_darwin.py`, `platform_win32.py`.
- New: `smith_agents/dashboard/` with local server, history and account readers, and packaged HTML/CSS/JS.
- New: `tests/test_dashboard.py`.
- Modified: `tools/build_macos.py`, `tools/smoke_macos.py`.
- Rebuilt in the worktree: `release/smith_agents-1.1.2-py3-none-any.whl`.
- No changes have been brought into the main checkout or installed over the running widget.

A local backup is saved as `output/dashboard-resume-2026-09-21.tar.gz`. It contains the implementation files listed above, the current local trial/artifact directory, and the dashboard handoff/checkpoint documents. It excludes worktree Git metadata, credentials and raw transcripts. It includes private local trial content; **do not publish or commit the archive**. Prefer continuing directly in the preserved worktree. Extract the backup into a separate temporary directory if recovery is needed, not over newer files.

## Evidence and remaining work

Claude's saved test logs (local, outside the repository) report:

- A targeted run: 59 dashboard tests, OK.
- A later full run: 362 tests, OK, 8 skipped.
- An earlier full run failed before the fixes and wheel rebuild; do not confuse it with the later passing run.
- Packaging/browser/native checks were attempted. These require review of actual results; do not claim all passed based on these notes.
- The final transcript actions concern a native AppKit shortcut smoke check. Claude reported existing smoke-script failures and attempted a focused check in a local scratch script. It was adjusting use of the AppKit run loop when interrupted. Native validation remains unresolved.

Next: inspect/review the preserved implementation; perform a time-bounded native check or state its concrete limitation; finish necessary documentation; reconcile the approved behavior and privacy/packaging requirements; integrate the reviewed changes into the main checkout while preserving user edits. Reuse passing verification where still applicable. Do not release, publish, merge remotely or replace the running app without authorization.

## Claude session and failed resume

The original Claude session, its companion job and its transcript are recorded locally, outside the repository. A later resume attempt failed before any work resumed: the companion passed a short job identifier to `--resume`, which requires a full session ID or title (`--resume requires a valid session ID or session title when used with --print`). Do not repeat that invocation or report that it resumed. Use the supported companion workflow with a full session ID, or a fresh bounded task pointed at the preserved worktree.

No resumed Claude work is running as of this checkpoint. There may still be preview servers on ports 8768/8769/8770 and unrelated test processes; identify exact ownership before stopping anything.

## Important correction

The orchestrator initially checked only the main checkout, wrongly concluded that no implementation existed, and cancelled Claude. Subsequent worktree/transcript inspection proved Claude had implemented and tested substantial changes. The 50 minutes were not idle. The files remain available. Future progress reports must cite actual worktree changes, timestamped transcript actions and test results, not the companion spinner or main-checkout status alone.

## 23 September 2026 — dashboard opens as an app window

The user corrected the handoff: the dashboard must open as an app screen, not a browser tab. In the worktree:

- `smith_agents.dashboard.launch` now only starts the server and returns the URL. `app._show_dashboard` passes it to `platform.open_dashboard_window` on the UI thread.
- macOS: one native `NSWindow` + `WKWebView` ("Smith Agents Dashboard"). While it is open the app becomes a regular app with a small menu bar (Close ⌘W, Copy, Select All, Reload ⌘R), and it returns to accessory mode on close. Reopening reuses the window and reloads only when the project or server changes. Non-loopback links open in the default browser. New dependency: `pyobjc-framework-WebKit` (pyproject, requirements, PyInstaller hidden import, license bundling).
- Windows: `msedge --app=<url>` window, falling back to `webbrowser`. Unit test runs on Windows only.
- Fixed a date-dependent test (`resets_at` fixed at 2026-09-22) to be relative to now.
- Wheel rebuilt. Full suite: 364 tests, OK, 9 skipped. Native check: window opened, dashboard loaded, reopen reused the window, close restored accessory policy.
- `tools/smoke_macos.py` fails on unmodified master as well (tray `isTemplate` assert, then a missing `reading` row). It is stale and has not been fixed.
- User approved the app-window mockup (`design/real-project-dashboard/widget-entry-preview.html`, section 03). In the default Matrix theme the tray icon, the dashboard header logo (`/logo.png` via `service.brand_mark`) and the Dock tile while the window is open (`platform_darwin._dock_icon`) all use Smith's face (`assets/tray-matrix.png`), drawn as is. Claude and E-ink keep the line man. Suite: 365 tests, OK, 9 skipped.
- The macOS app bundle icon (PyInstaller build and pip launcher) is now `artwork.app_icon()`, Smith's face on a dark tile. The Windows `.ico` is unchanged. A review found one issue (the window treated `localhost` as external), now fixed.
- **Integrated:** commit `2e0d5e4` fast-forwarded onto local `master`, which is 1 ahead of origin and not pushed. Main checkout suite: 366 tests, OK, 9 skipped. Not released, and the running widget has not been replaced.

# Changelog

The one-line installer installs the latest GitHub release, pinned by version
and checksum. Changes on `master` reach it only when a new version is released.

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

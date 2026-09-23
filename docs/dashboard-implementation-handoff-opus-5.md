# Dashboard implementation handoff — Opus 5

> **Resume checkpoint:** Implementation now exists in Claude's separate worktree and has been saved for tomorrow. Read [dashboard-resume-checkpoint.md](dashboard-resume-checkpoint.md) before continuing; do not start this implementation from scratch.

Prepared 21 September 2026. The user approved the widget entry-point artifact and requested this handoff for implementation. This turn writes documentation only; the installed widget has not been changed.

## Implement the approved result

Make the existing dashboard available from Smith Agents on macOS and Windows:

1. Add a compact dashboard icon beside a Settings shortcut in the expanded widget footer. Its label/tooltip is **Open dashboard**. The approved artifact shows a four-tile outline icon and a compact footer action row.
2. Add **Open dashboard** to the tray menu, available while the widget is tucked. Use the same launch operation for both entry points. Include the equivalent context-menu entry where appropriate.
3. Open the dashboard in the default browser, starting or reusing a widget-owned local server. Select the current project when there is an unambiguous selection; otherwise use the last valid project or the dashboard's project picker.
4. Preserve the approved compact activity calendar inside the existing dashboard layout, with session selection and receipts beneath it. Do not substitute the standalone options-comparison page.

The immediate approval is for these entry points. The dashboard needs packaging and lifecycle integration to make them work; a shortcut hardcoded to a manually running preview is not a completed implementation.

## Approved visual artifact

Local, gitignored files, relative to the repository root:

- [Clickable widget + tray proposal](../design/real-project-dashboard/widget-entry-preview.html)
- [Rendered proposal](../design/real-project-dashboard/widget-entry-preview.png)
- [Main dashboard markup](../design/real-project-dashboard/index.html)
- [Main dashboard behavior](../design/real-project-dashboard/app.js)
- [Main dashboard styles](../design/real-project-dashboard/style.css)

Open the artifact before implementing. It combines existing widget screenshot artwork with proposed HTML footer controls; it is not a native widget implementation. The Settings icon is also proposed: do not assume the current widget already has this exact control. Wire it to existing settings/context-menu functionality rather than inventing an unrelated settings screen. The mockup's project label is illustrative; project-aware launch is not implemented yet.

The artifact links to a development server at port 8770. That address is temporary, not a production port or launch contract.

## Decisions from this conversation

- The user selected **02 — Activity calendar** from three interactive options.
- They rejected large calendar cubes and asked to see the calendar in the main mockup. The latest main dashboard uses compact cells (approximately 82 px tall on desktop, 75 px on narrow screens), with day number, new-token count, and a small allowance-change example.
- Calendar selection lists the day's sessions by descending new-token usage; selecting a session updates the receipt. Helpers remain grouped with their parent.
- They requested allowance start/end readings next to tokens **inside the calendar cells**, not a separate large allowance banner.
- The example is **5% → 17% used**, a change of **12 percentage points**, leaving **83% remaining**. These percentages are illustrative, not derived from historical receipts. New tokens are fresh input plus output; cached context is separate. Tokens are not subscription cost or allowance percentages.
- Historical allowance changes are available only if real readings exist. The conversation has not established that retained session logs contain sufficient readings.
- The user asked to connect account usage. A live **Plan usage** tab was added using the existing widget collectors. They then said it was not needed, immediately retracted that with “ignore leave it,” and were told it would stay. **Keep this tab**; do not remove it on the basis of the retracted message.
- No new image generation, figure changes, replay/movie controls, remote hosting, or model-based summaries are needed for this implementation.

## Current local implementation

All trial sources below are under `design/real-project-dashboard/`, which is gitignored. They exist in this workspace but are absent from a fresh clone. The previous `output/usage-dashboard-trial-2026-09-21/` snapshot predates the calendar and connected Plan usage changes. Do not restore it over the current sources.

| Source | Purpose / limitation |
| --- | --- |
| `server.py` | Loopback HTTP preview, explicit route/asset allowlist and Host validation; currently launched manually |
| `history.py` | Discovers local Codex + Claude projects, reads/deduplicates receipts, associates helpers, reads Git milestones |
| `summaries.py` | Local reviewed session copy with exact evidence matching; contains real local excerpts; **not a public shipping fixture** |
| `app.js`, `index.html`, `style.css` | Current compact calendar inside the full dashboard; default Sessions view |
| `account_usage.py` | Sanitized account readings through existing collectors, cache/backoff, in-memory change baselines |
| `plan-usage.js`, `plan-usage.css` | Live Plan usage tab: remaining limits, reset countdowns, Codex credit balance, observed account-wide change |
| `test_history.py` | 13 accounting/reader tests |
| `test_account_usage.py` | 5 tests for allowance deltas, resets/decreases, stale fallback, retry backoff and invalid readings |
| `history-options.*` | Earlier three-layout comparison; reference only |
| `widget-entry-preview.*` | Approved entry-point proposal; reference only |

Current preview command, from repository root:

```sh
.venv/bin/python design/real-project-dashboard/server.py --port 8770
```

Preview URLs if that process is running:

- `http://127.0.0.1:8770/` — main calendar dashboard.
- `http://127.0.0.1:8770/#plan-usage` — connected Plan usage.

Older servers on 8768/8769 may still exist. Do not assume their route tables are current. Do not kill unidentified Python processes.

## Relevant application integration points

- `smith_agents/core.py`: `render_console`, footer geometry, hit-target tuples, `fit_agent_viewport`. The existing Agents footer includes account navigation, session/helper count, optional clear-closed action and overflow controls. Preserve those functions and avoid overlapping hit regions. Make the new action row available consistently across expanded tabs if feasible.
- `smith_agents/app.py`: `_on_console_click`, `_build_context_menu`, `_build_tray`, and `quit`. Add one shared dashboard-open operation used by the footer and menus. Route UI updates/errors onto the appropriate platform UI thread.
- `smith_agents/platform_darwin.py`: AppKit menu/tray adapters (`build_context_menu`, `build_tray` and their menu wrappers).
- `smith_agents/platform_win32.py`: `build_tray`, `build_context_menu`.
- `smith_agents/core.py`: `fetch_usage`, `load_cache`, `build_metrics` for Claude; credentials remain in the existing server-side collector.
- `smith_agents/codex_usage.py`: `AccountClient`, account rate-limit reads, `build_metrics`, `build_credits`.
- `pyproject.toml`: wheel packages currently include `smith_agents` and `claude_widget`; `design/` is not application data.
- `tools/build_macos.py`: PyInstaller collects `smith_agents` data. Verify newly packaged HTML/CSS/JS are actually included and addressable in a frozen build.
- Existing regression areas: `tests/test_ui_flows.py`, `tests/test_tucked.py`, `tests/test_launch_lifecycle.py`, `tests/test_release.py`, `tests/test_codex_usage.py`, plus platform tests.

## Implementation requirements and recommended approach

### Package and launch

- Promote reusable, reviewed code and web assets into an application-owned package, for example `smith_agents/dashboard/`. Do not depend on the repository checkout, `design/`, `.venv`, a standalone Python executable, or port 8770 at runtime.
- Prefer a lazily started in-process server with a synchronized start-once lifecycle and OS-assigned loopback port. Start work off the UI thread; wait for readiness before opening the browser. Repeated clicks must reuse the listener, not create duplicate servers.
- Close the server and release its port on normal widget shutdown; handle startup failures without freezing the widget. A frozen macOS app and installed Windows launcher must behave like the source run.
- Use the platform/browser opener with a structured, encoded URL. Project selection should use an opaque discovered project identifier, not an arbitrary filesystem path. Add actual frontend URL initialization; the trial does not currently read a project launch parameter.
- Identify current project from explicit selected/expanded session or another reliable existing UI selection. If several sessions are active and no selection exists, fall back rather than guess.
- The screenshot preview's HTML is not production rendering. Draw native icons with existing Pillow/theme/scaling conventions and add actual hit targets. Keep the existing widget's figure assets and layout behavior intact.

### Data and local service

- Bind only to loopback. Preserve explicit asset routes, Host validation, no credential-bearing browser payloads, no permissive CORS, and no arbitrary file-serving routes. For production local-session data, add an appropriate per-launch authorization mechanism rather than assuming loopback alone authorizes every local client. Ensure the HTML/CSS security policy supports the chosen rendering method; the current trial sets inline styles dynamically and deserves a CSP check.
- Share existing account polling/results with the widget where practical instead of introducing a second independent account poller. The trial reuses a recent Claude cache and limits account requests to one per 30 seconds; carry forward provider backoff and visible stale-state behavior.
- Avoid transcript scans or account requests during paint/hit testing. Keep file-signature caching, shared snapshots, and hidden-page polling suspension. The current history JS checks document visibility but not the Plan usage tab state; tighten this during integration.
- Remove workstation-specific assumptions: `history.py` currently uses the repository-derived `ROOT` and hardcodes `Asia/Jerusalem`. Packaged resources, project selection and local day grouping must work on another user's machine and timezone.
- Keep real user prompts, reviewed excerpts, local screenshots, history exports and credentials out of shipping fixtures and public commits. Use synthetic test data. Port the generic summary fallback; do not ship the local `summaries.py` entries as product content.

### Allowance semantics

- Live Plan usage is account-wide. Show provider-supplied labels and actual limits; do not hardcode a plan name such as Pro Max.
- Keep reset times, stale data and on-credit status explicit. A single reading does not establish pace or predict exhaustion.
- The existing observation baseline is in memory and resets when the server restarts, the allowance window changes, or usage decreases. It is not a persistent session history collector.
- **Do not ship the calendar's hardcoded `5% → 17%` as real readings.** Retain it only in an explicitly labelled demo. For real sessions lacking paired readings, show “Allowance change unavailable” or a compact dash with an explanation.
- Automatic persistent session-start/end allowance collection is additional work beyond the approved entry-point integration. If added later, record real account snapshots with timestamps and reset-window identity, show sampling gaps/overlap, and never attribute account-wide consumption exclusively to one concurrent session.
- The trial retains `renderLegacyTimeline` and the comparison files; remove unused prototype code when promoting the selected calendar. Check selection synchronization on provider/project/date changes and exact daily receipt reconciliation, including histories longer than 45 days (the old story grouping can aggregate weeks).

## Acceptance checks

1. Clicking the widget dashboard icon opens the packaged calendar in the default browser; no manual server command is required. Tooltip reads “Open dashboard.” Settings opens existing settings/menu functionality.
2. Tray action works while tucked on every edge and does not require expanding the widget. Existing provider clicks, account navigation and session focus behavior still work.
3. Repeated launches reuse one server. Shutdown releases it. Browser failure, port conflict and collector failure produce clear recoverable behavior; neither paint nor session monitoring freezes.
4. Project-aware launch selects the intended workspace. Missing/deleted projects and ambiguous current selection have a sensible fallback.
5. Footer geometry and hit targets work at supported scales, in all themes/tabs, with long labels, no agents, many agents, overflow scrolling and clear-closed controls.
6. Calendar/day/session selection updates the correct receipts. Daily totals reconcile with underlying deduplicated receipts; helper traffic counts once. Check Day/Weekly/All time and desktop/mobile widths. No made-up allowance percentages in the real-data view.
7. Plan usage remains accessible and switches back to Project history. Test connected, unavailable, stale, reset, limit-exhausted and credits states. Browser receives only sanitized readings; sampling obeys cache/backoff.
8. Test the built wheel/app outside the repository, including static assets, collectors, launch and shutdown. Run platform smoke checks where the platform is available; state clearly if Windows verification could only be unit-level on this Mac.

Checks already performed on the **trial**, not the future integration: 13 history tests passed earlier; 5 account tests passed after connecting usage; local Chromium verified calendar selection/receipts across Day/Weekly/All time at 1440/390 px, and both live providers plus Plan usage tab switching at those widths. The widget-entry HTML was rendered and its menu toggle/mobile overflow checked. These do not prove native widget integration or packaging.

## Finish and report

Implement and verify the approved native entry points plus their packaged dashboard destination. Update usage/development documentation and give the user the resulting artifact/build and concise validation results. Preserve unrelated work. This handoff does not authorize publishing private artifacts, pushing commits, merging, or releasing.

For older design history see [usage-dashboard-handoff.md](usage-dashboard-handoff.md). This handoff supersedes its earlier statements that the dashboard has no account connection or only the original timeline design.

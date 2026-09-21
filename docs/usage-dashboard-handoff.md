# Usage dashboard — saved for the next session

> **Local references.** Links into `../design/` and `../output/` point at
> untracked local directories and will not resolve in a fresh clone. The
> selected design reference is committed as
> [`docs/images/usage-dashboard-build-story.png`](images/usage-dashboard-build-story.png)
> with its prompt in [`docs/design/usage-dashboard-build-story-prompt.txt`](design/usage-dashboard-build-story-prompt.txt).

Saved 17 September 2026. The user wants to pause and possibly build this tomorrow. Do not start implementation simply because this handoff exists.

## Current selection — restore the previous build-story design

The user rejected the white-figure refinement (07) and asked to go back to the previous design. The active visual reference is again [06 — Editorial graphite build story](images/usage-dashboard-build-story.png), with its original icons. Do not use 07 as the selected design or apply the white-figure treatment. Preserve both artifacts as exploration history.

The preference to omit animation/movie controls still applies. The real-project trial below implements the build-story direction without replay controls.

## Real-project trial — before widget integration

### Plain-language session summary options

The user liked the trial but found the recorded-activity tables too long and technical. They requested different ways to explain what happened to people who do not know code. [Interactive comparison](../design/real-project-dashboard/summary-options.html) · [Screenshot](../design/real-project-dashboard/summary-options.png).

Three options were shown: **01 A short story**, **02 What changed for you** (recommended: three outcome highlights), and **03 Answer my questions** (expandable plain-language questions). Each hides exact token counts, session IDs and model information behind optional details. The examples use the real 16 September session beginning `01a0a945`; its completion reports and matching Git milestones support the activity summaries. The 88,950,220-token / 730-response receipt is unchanged, with 96.9% cached input explained as reused material. Passing checks are attributed to session reports rather than claimed to have been independently rerun.

The user then asked to implement the simpler summary in the real-data mockup. Option 02 was implemented as the default **Open activity** dialog: up to three plain-language highlights, an optional additional-work line, and collapsed usage, evidence, and session details. The exact counts are still calculated from the selected real receipts. The chapter cards and inline activity area also use reviewed summaries where available. [Desktop dialog](../design/real-project-dashboard/plain-summary-desktop.png) · [Mobile dialog](../design/real-project-dashboard/plain-summary-mobile.png).

This is reviewed copy for the local mockup, not an automatic AI summarization service. `design/real-project-dashboard/summaries.py` holds reviewed highlights keyed to an exact session ID, report timestamp, and supporting excerpt. The collector emits a highlight only when its original report matches. The UI scopes highlights to the selected period/hour and session. Unreviewed activity gets a factual, explicitly limited description of recorded tool requests, without claiming completed features or passed checks. Raw prompts and tool outputs remain unexported; only the short reviewed evidence excerpts are added.

Validation after integration: eight Python tests pass, including evidence matching. Chrome checked the 88,950,220-token session, its 96.9% cached-input explanation, hidden-by-default technical counts, all disclosures, a different session's distinct summary, hour/date scoping, generic fallback, total reconciliation across all six range/view combinations, and responsive widths 320–1536 with no page errors or dialog overflow. The local preview server was restarted and remains at http://127.0.0.1:8768. The widget is unchanged.

The user requested a test of design 06 with a real project before full implementation. A standalone, read-only local preview now lives in `design/real-project-dashboard/` (gitignored). Start it with:

```sh
python3 design/real-project-dashboard/server.py
```

Open **http://127.0.0.1:8768**. The server was left running for this session's user review. It binds only to loopback, serves an explicit asset allowlist, and does not modify the widget, transcripts, Git history, or account state. No account API is called. The browser receives aggregated receipt/session metadata, tool-call categories, and Git commit subjects; prompts, tool arguments and outputs are not exported. Snapshots live in memory, with a shared 30-second cache and file-signature caching; hidden pages stop polling.

The trial uses this repository's retained local Codex history. It excludes internal permission-review sessions and deduplicates response IDs. Fresh input subtracts cached input from total input; output already includes reasoning. Per-response receipts are used in preference to cumulative counters, not added to them. A fallback for counter-only logs excludes the first cumulative baseline and resets from dated totals. The current data does not exercise that fallback; synthetic tests cover it.

The Story view groups real receipts by day, by active hour in Day view, or by week for histories longer than 45 days. Available Git milestone subjects label time chapters; they are not fabricated build phases or token attribution to commits. Sessions mode provides individual session receipts. Day / Weekly / All time, previous/next period, direct chapter selection, keyboard arrows/Home/End, zoom, activity/coverage dialogs, exact token receipts, refresh and stale-data handling are functional. No animation/movie controls. The allowance tab explains that account allowance is not connected in this trial.

Only `smith-agents` is connected. Other projects, other providers, and another computer's history are outside this snapshot. Missing recorded activity is identified explicitly. All time includes all matching retained Codex log receipts discovered locally, not a fixed window. Local snapshots may differ from provider account/session counters; do not interpret recorded traffic as subscription allowance or API cost.

Preview screenshots: [desktop](../design/real-project-dashboard/desktop.png), [mobile](../design/real-project-dashboard/mobile.png). These contain real local project data and remain in the gitignored design directory.

Validation: seven Python accounting tests pass (`python3 -m unittest discover -s design/real-project-dashboard -p 'test_history.py' -v`). Local Chrome checked 39 chapter receipts across all six range/view combinations, exact receipt totals, deduplication, keyboard/date navigation, zoom, activity/coverage/allowance dialogs, refresh and failed-refresh behavior, and responsive widths 320–1536. No page errors or horizontal page overflow after the narrow-phone layout fix. Read-only route and Host checks passed. This validates the local trial, not a production dashboard integration.

## Superseded exploration — build history with white Smith figures

On 18 September, the user accepted the Editorial graphite build-story direction and requested two changes: remove the animation/movie experience, and use the existing Smith figures rendered white like the brand icon for most dashboard icons. The user explicitly clarified that this figure treatment applies **only to the dashboard**. Do not change widget figures, shared figure assets, or the app icon.

[Latest refined mockup](../output/imagegen/dashboard-directions/07-editorial-white-figures.png) · [Generation prompt](../output/imagegen/dashboard-directions/07-editorial-white-figures-prompt.txt).

The latest mockup has direct chapter selection, helper branches, a Story / Sessions switch, and an activity/token receipt. It has no play, replay, speed, or scrubber controls. This supersedes the earlier animation requirement and the prior native-style visual direction below. The user accepted the build-story direction; the white-figure refinement is the latest proposal shown for review. It remains a static mockup: the existing functional HTML prototypes have not yet been converted to this direction. The white figures in the mockup were generated from the approved helper and figure sheets with `tray.png` as the silhouette-style reference, using the built-in image tool. Original assets remain unchanged.

## 18 September — refined interactive prototype

The user asked to refine the interactive prototype to match the selected design. The latest functional version is [the native-style prototype](../output/usage-dashboard-native/interactive-prototype.html), with [desktop](../output/usage-dashboard-native/desktop.png) and [mobile](../output/usage-dashboard-native/mobile.png) previews. The original selected image and original prototype below are preserved.

This refinement brings the warm charcoal surfaces, compact toolbar, project sidebar, integrated summary, copper bars, sage comparison line, activity inspector, and equal square project-share blocks into the working HTML. It preserves Day / Weekly / All time, project filters, keyboard selection, replay / pause / scrubbing, and the separate allowance-pace view. Replay speed and a desktop sidebar toggle are functional. All data remains fictional; no live widget integration was added.

Totals and activity categories follow the original prototype's consistent records, rather than copying the generated image's differing sample receipts. The inspector displays the three largest activity categories, with the full category receipt available in its disclosure. Comparison baselines and history coverage remain explicit. The squares round shares visually; the numerical percentages remain visible.

Editable sources: `design/build-native-dashboard.py`, `design/native-dashboard.css`, and `design/native-dashboard.js`. Rebuild with `python3 design/build-native-dashboard.py`; it reads the saved original prototype and `design/project-history.js`, then writes the new self-contained HTML. The `design/` directory remains gitignored.

Validated in local Chrome with Playwright: all nine scope/range combinations, 63 bucket receipts, comparison baselines, project-row filtering, replay/pause/scrubbing/speed, keyboard selection, activity disclosure, switching between usage and allowance views, reduced-motion initial state, and sidebar toggle. No JavaScript errors. Checked history layout at widths 320, 390, 540, 650, 768, 850, 1024, 1280, and 1440, plus the 1536-pixel desktop preview; allowance layout at 320, 390, 768, 1024, and 1536. No horizontal overflow in those checks. These checks cover the sample prototype only, not production integration.

## Chosen visual direction

### Later exploration — Editorial graphite build story

After viewing the native-style prototype, the user asked to revisit original direction 03, Editorial graphite, then requested a refinement with a more interactive way to show the project build instead of column graphs. [The new build-story mockup](images/usage-dashboard-build-story.png) explores selectable chronological chapters, branches for helper activity, a build replay scrubber, a Story / Sessions switch, and a selected-chapter receipt. This is a static visual proposal, not a wired interactive prototype or an approved replacement for the prior direction. All chapter data is a new illustrative fixture. [Saved prompt](design/usage-dashboard-build-story-prompt.txt). Generated using the built-in image tool with direction 03 as the edit reference.

### Previously selected direction

Use [the latest mockup](../output/usage-dashboard-handoff/selected-design.png) as the starting point. The user initially preferred direction 1, rejected the first Apple-inspired refinement as too similar, then accepted the substantially redesigned native-style image as the direction to save.

The selected image shows a compact Mac-style toolbar, project sidebar, integrated usage summary, large copper bar chart with a sage comparison line, right-side activity inspector, and project comparison rows made of square blocks. Keep white numbers, warm charcoal surfaces, and restrained materials. The image is an independent Apple-inspired concept, not an implementation of Apple's native components.

- [Selected design, full resolution](../output/usage-dashboard-handoff/selected-design.png)
- [Interactive functional prototype](../output/usage-dashboard-handoff/interactive-prototype.html)
- [Exact generation prompt](../output/usage-dashboard-handoff/generation-prompt.txt)
- [Original direction 1](../output/usage-dashboard-handoff/original-direction-1.png)

The selected image was generated with the bundled imagegen CLI, requesting `gpt-image-2.5-sunburst`, high quality, 1536×1024. The user explicitly prefers this model for visual explorations. Preserve the saved image rather than regenerating it by default.

## Intended behavior

- Explain usage pace, spikes compared with the user's usual activity, and what work occurred during expensive periods.
- Support Day, Weekly, and All time, with all-project and individual-project filtering.
- Animate the timeline on user request, with pause, replay, and scrubbing. Clicking a peak opens activity and token details.
- Show fresh input, cached input, and output separately. Include helper traffic once.
- Show project shares with equal square blocks and clear numerical totals.
- Preserve the allowance-pace explanation from the prototype alongside project history.
- Weekly comparisons use the same weekdays in the preceding four weeks; day comparisons use matching hours in the preceding seven days. All-time prototype buckets are weekly, with their average explicitly labelled.
- Real all-time history means all available recorded history. Display its start date and handle incomplete or missing history honestly.
- Account allowance, recorded token traffic, and API cost are different quantities. Do not allocate exact subscription allowance to projects from token counts alone. Explain observed activity without claiming unsupported causation.

All current dashboard data is fictional. The prototype is not connected to live accounts. The selected image and prototype illustrate different sample receipts; do not treat either fixture as an actual user's telemetry.

## Implementation context and checks

The running widget is Python, Pillow, and platform-specific integration; it supports macOS and Windows. Dashboard artifacts are plain HTML/CSS/JavaScript. No dashboard has been integrated into the live widget, no Apple component library has been installed, and this design work has not been committed or pushed.

Before production implementation, inspect the current collectors and stored history to establish what each provider can actually supply. Choose a cross-platform dashboard delivery approach that fits the existing app. Preserve the user's preference for roughly 30-second usage refreshes and avoid continuous expensive rendering, particularly with multiple windows open.

The prototype's scope/range totals, receipts, replay controls, keyboard interactions, reduced-motion default, and responsive widths were checked with Puppeteer. Those checks validate only the prototype, not production data integration. Validate the new implementation against the selected image and real data separately.

Editable prototype sources remain under `design/`: `usage-explained-v2.html`, `project-history-fragment.html`, `project-history.css`, `project-history.js`, `build-usage-v3.py`, and `export-usage-concept.py`. The self-contained HTML in this handoff keeps the functional prototype available even without its build sources. The `design/` directory is gitignored; this handoff and `output/` artifacts are saved locally and currently uncommitted.

## Design references

- [Apple Design Resources](https://developer.apple.com/design/resources/)
- [Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/)
- [Materials guidance](https://developer.apple.com/design/human-interface-guidelines/materials): reserve Liquid Glass for navigation and controls, keeping chart content clear.

The original exploration images and comparison viewers remain under `output/`. No existing artifacts were replaced when saving this handoff.

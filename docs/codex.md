# Codex sessions

The Agents tab and tucked strip show local Codex CLI and VS Code extension
sessions alongside Claude Code. Figures, state colors, context meters, project
names, and helper animations use the same renderer. A Claude/Codex label identifies
the provider. Codex monitoring does not require a Claude login or an OpenAI API key.
The **Usage** and **Stats** tabs follow the Claude/Codex switch above the header
readings. Click the provider logo beside the tucked usage reading to switch there.
Agent rows continue to show both providers. Codex context tokens are not a
subscription-quota reading.

## Account usage and stats

Codex quota windows and token-activity summaries come from the official
[App Server account methods](https://learn.chatgpt.com/docs/app-server):
`account/rateLimits/read` and `account/usage/read`. The widget initializes its own
short-lived account connection using the installed Codex executable and existing
login. It does not start/resume conversations or run model turns for these reads.
The executable can come from a running CLI/extension, the CLI installation, or a
VS Code extension's bundled runtime. Codex manages authentication itself.

Usage displays the actual returned windows, percentages used, reset times, and
model-specific buckets. It does not assume every plan has both a five-hour and
weekly limit. The main Codex bucket appears first; each additional bucket has its
own label. Click the header readings to cycle through used percentage, remaining
percentage, and reset time/day. The small caption identifies the current view.
Click the tucked reading to cycle through available windows.

Codex Stats shows tokens in the last 14 UTC calendar days, lifetime tokens, peak
daily tokens, longest turn, current streak, best streak, and daily token activity.
Null/unsupported readings display as unknown. Codex account token totals use the
service's definition; Claude's local token stats exclude cache reads. These are
separate views, not summed or presented as directly equivalent measurements.

Claude and Codex refresh independently. **Refresh now** requests both providers.
Codex reads normally refresh every five minutes and cache the latest readings in
`codex-usage.json` in the widget's configuration directory. When refresh fails,
the Usage/Stats pane labels retained data as the last reading. Account-statistic
failures do not hide valid quota readings, or affect Claude. No login tokens or
credentials are copied into the cache.

Live quota and activity reads and native provider switching were verified on Mac
with Codex 0.153.4. The protocol, failed/partial responses, null values, dynamic
windows, and switching controls have synthetic regression coverage on both OSes;
native Windows verification remains a release check.

## Automatic detection

Install the current dependencies (`python -m pip install -e .`) and start the
widget normally. Open or resume Codex in a local terminal or the VS Code extension.
It can take several seconds to catch up with a large existing conversation.
There is no terminal-specific launcher and no VS Code executable override.

The widget finds native Codex processes and their open rollout files or thread
writer locks. It identifies each thread separately, including when several
threads share one extension process or project. Historical transcripts alone
never make an agent appear alive. It honors `CODEX_HOME` for hook snapshots and
discovers custom session locations through open handles.

Working and Ready come from turn lifecycle records, rather than guessing that a
long-running tool must be waiting for approval. A version without a recognized
state record shows **Activity unknown**. Recent context uses the latest input
token reading; cached input is already part of that count. Compaction invalidates
the previous context reading. Missing data stays unknown.

Loaded helpers stay attached to their recorded parent. Completed helpers are
shown briefly, then removed. A disappeared main session gets a short-lived closed
row, which can be dismissed. Session IDs include the provider to avoid collisions.

## Precise approval status

Choose **Connect Codex status…** in the tray/menu bar, then use Codex's `/hooks`
interface to review and trust the **Widget session status** hooks. Start or resume
a session after enabling them. For source installs, the equivalent command is:

```sh
python -m smith_agents.codex_hooks install
```

The installer merges handlers into `CODEX_HOME/hooks.json` (default `~/.codex`),
keeps existing hooks, and saves the original as `hooks.json.before-widget`.
It does not change hook trust, approval policies, terminal settings, or VS Code
settings. Keep the source environment/app at its installed path; reconnect after
moving it. Individual handlers can be disabled from `/hooks`.

The optional hooks report lifecycle and pending-permission events to a local
`widget-events/events.sqlite` snapshot. Snapshots contain process identity,
session/model metadata, and bounded previews of pending tool inputs. They are
never uploaded. Hooks emit no approval decision and do not answer prompts.
The widget displays **Answer in Codex**; approve or deny in the original client.
An unrelated parallel tool or a helper finishing cannot clear the parent's
pending approval. Snapshots from a reused PID are ignored.

Hook support and trust behavior depend on the installed Codex client and any
administrator policy. Automatic file-based detection remains available without
hooks. The snapshot path and transcript reader are kept separate because
[Codex documents that transcript formats are not a stable interface](https://learn.chatgpt.com/docs/hooks).

## Window controls

**Open window** reveals the identifiable host window; **Hide window** minimizes
that recorded window while the agent keeps running. These controls do not switch
to a particular terminal tab or extension conversation. An ambiguous match fails
without bringing every window forward. macOS requires Accessibility access for
native window controls. Windows matches only windows owned by the agent's host
process ancestry, rather than any application with the same project title.

Codex rows display **End in Codex** instead of terminating a process. The extension
can host several agents in one process, so stopping that process would end other
sessions. Close or interrupt the specific conversation inside Codex.

## Compatibility and validation

| Setup | Implementation | Validation |
| --- | --- | --- |
| Local CLI in a macOS terminal, including VS Code's terminal | Automatic monitoring; optional hooks | Live Codex 0.153.4 terminal session detected |
| macOS VS Code extension | Automatic monitoring; optional hooks | Live bundled Codex 0.153.4 session detected; native widget smoke test passed |
| Local Windows CLI / VS Code extension | Shared scanner, hooks, and native window backend | Synthetic compatibility tests; native Windows verification remains required |
| Other local terminal hosts | Monitoring uses Codex handles, independently of the terminal | Exact window controls depend on host ancestry and accessible window titles |
| WSL, SSH, containers, remote extension hosts | A bridge in that process/filesystem environment is needed | Not connected by this local implementation |

`tests/test_codex_sessions.py` covers CLI and extension record formats, pagination,
partial writes, compaction, context arithmetic, parent/helper relationships,
four threads in one process, closed sessions, read-only index fallback, hook
merging, permission isolation, and mixed-provider rendering. The existing CI
matrix runs the suite and native smoke test on Windows and macOS after push.

No claim of universal client-version or remote-environment compatibility is made
until those setups pass their own native checks.

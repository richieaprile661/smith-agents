# Codex sessions

Smith Agents shows local Codex CLI and VS Code extension sessions next to your
Claude Code sessions, in the **Agents** tab and in the tucked strip. Codex sessions
use the same figures, state colors, context meters, and project names. A provider
logo on each row shows whether Claude or Codex runs it. Monitoring Codex doesn't require a
Claude login or an OpenAI API key.

The **Usage** and **Stats** tabs show the provider that's selected with the
Claude and Codex switch above the header readings. In the tucked strip, click the
provider logo beside the usage reading to switch. The agent list always shows both
providers.

## Set up Codex monitoring

Smith Agents finds Codex sessions on its own:

1. Install or update Smith Agents, and start it.
2. Open or resume Codex in a local terminal or in the VS Code extension.

It can take several seconds to catch up with a large existing conversation. Codex
doesn't need a special launcher or a VS Code setting.

To show pending approvals as well, see
[Optional: Show pending approvals](#optional-show-pending-approvals).

## How sessions are detected

The widget finds running Codex processes and the rollout files or thread writer
locks that they have open:

- It tracks each thread separately, including several threads that share one
  extension process or one project.
- A saved transcript on its own never makes an agent look alive.
- It honors `CODEX_HOME` for hook snapshots, and finds custom session locations
  through the files that Codex has open.

**Working** and **Ready** come from Codex's turn lifecycle records. The widget
doesn't guess that a long-running tool is waiting for approval. If a Codex version
writes no recognized state record, the row shows **Activity unknown**.

The context reading uses the latest input token count, which already includes
cached input. Compaction clears the previous reading. Missing data stays unknown.
Codex context tokens aren't a reading of your subscription limits.

Helpers stay attached to their parent session, including nested helpers. Once
observed, up to three recently finished helpers remain visible per main session
for two minutes. Helpers from before the current process started stay out of the
live list. Running helpers stay visible regardless of command duration. When a
main session ends, it leaves a closed row for a short time,
which you can dismiss. Session IDs include the provider, so Claude and Codex IDs
never collide.

Each helper row shows its current tool or command and elapsed time. Expand it to
see its assigned task, its own latest message or final result, and a bounded
preview of tool output. Results are matched to tool call IDs, including parallel
calls and commands that yield a running session for later polling. Tool counts
and cumulative tokens are separate from the context meter; missing values stay
unknown. If ownership or the activity feed is lost, the row says so instead of
claiming that an old command is still running.

## Usage and stats

Codex limits and token activity come from the official
[App Server account methods](https://learn.chatgpt.com/docs/app-server),
`account/rateLimits/read` and `account/usage/read`. The widget opens its own
short-lived connection with the installed Codex executable and your existing
login. These reads don't start or resume conversations, or run model turns. Codex
manages its own authentication.

The widget can use the Codex executable from a running CLI or extension, the CLI
installation, or a VS Code extension's bundled runtime.

### Usage

The **Usage** tab shows the windows that Codex returns, with the percentage used,
the reset time, and any model-specific buckets. It doesn't assume that every plan
has both a 5-hour and a weekly limit. The main Codex bucket comes first, and each
additional bucket has its own label.

- Click the header readings to switch between the percentage used, the percentage
  remaining, and the reset time or day. The small caption shows the current view.
- In the tucked strip, click the reading to cycle through the available windows.

### Stats

The **Stats** tab shows the following Codex readings:

- Tokens in the last 14 UTC calendar days
- Lifetime tokens
- Peak daily tokens
- Longest turn
- Current and best streaks
- Daily token activity

Readings that Codex doesn't return show as unknown. Codex account token totals use
the service's own definition, and Claude's local token stats leave out cache
reads. The two views stay separate; they aren't added together or presented as
the same measurement.

### Refresh and cache

Claude and Codex refresh independently, and **Refresh now** requests both.
Codex readings refresh every five minutes, and the latest readings are cached in
`codex-usage.json` in the widget's settings folder.

- If a refresh fails, the **Usage** and **Stats** tabs label the retained data as
  the last reading.
- A failed stats read doesn't hide valid limit readings, and doesn't affect Claude.
- The cache holds no login tokens or credentials.

Live limit and activity reads, and native provider switching, were verified on Mac
with Codex 0.153.4. Automated tests on both platforms cover the protocol, failed
and partial responses, missing values, changing windows, and the switching
controls. Native Windows verification is a release check.

## Optional: Show pending approvals

Codex hooks report approvals, helper start/stop events, and tool lifetimes. They
improve helper tracking when a child transcript is not available yet.

1. In the tray or menu-bar menu, click **Connect Codex status**.
2. In Codex, use the `/hooks` interface to review and trust the
   **Widget session status** hooks.
3. Start or resume a Codex session.

For a source installation, the following command installs the same hooks:

```sh
python -m smith_agents.codex_hooks install
```

The installer merges its handlers into `CODEX_HOME/hooks.json` (by default,
`~/.codex/hooks.json`), keeps your existing hooks, and saves the original file as
`hooks.json.before-widget`. It doesn't change hook trust, approval policies,
terminal settings, or VS Code settings. You can turn off individual handlers from
`/hooks`. For a source installation, keep the environment at its installed path,
and reconnect if you move it.

The hooks report lifecycle and pending-permission events to a local snapshot,
`widget-events/events.sqlite`:

- Snapshots hold process identity, session and model metadata, task lifecycle,
  bounded tool input/output previews, and final messages. They're never uploaded.
  The store retains at most 256 recent tool identities per session and removes
  snapshots older than seven days during capture.
- The hooks don't answer prompts or make approval decisions. The widget shows
  **Answer in Codex**, and you approve or deny in Codex itself.
- An unrelated tool running in parallel, or a helper finishing, can't clear the
  parent session's pending approval.
- Snapshots from a reused process ID are ignored.

Hook support and trust depend on your Codex client and any administrator policy.
Without hooks, file-based detection keeps working. The snapshot path is kept
separate from the transcript reader because
[Codex doesn't treat transcript formats as a stable interface](https://learn.chatgpt.com/docs/hooks).

## Window controls

**Open window** brings the agent's host window to the front, and **Hide window**
minimizes it while the agent keeps running:

- These controls don't switch to a particular terminal tab or extension
  conversation.
- When more than one window matches, the widget doesn't bring any of them forward.
- On macOS, window controls require Accessibility access.
- On Windows, the widget matches only windows owned by the agent's process or its
  parent processes, not any app with the same project title.

Codex rows show **End in Codex** instead of an option to stop the process. The
extension can host several agents in one process, so stopping the process would
end other sessions too. To stop one agent, close or interrupt its conversation in
Codex.

## Compatibility and validation

The following table lists the setups that Smith Agents supports and how each one
was checked:

| Setup | Support | Validation |
| --- | --- | --- |
| Local CLI in a macOS terminal, including VS Code's terminal | Automatic monitoring, with optional hooks | A live Codex 0.153.4 terminal session was detected. |
| macOS VS Code extension | Automatic monitoring, with optional hooks | A live bundled Codex 0.153.4 session was detected, and the native widget smoke test passed. |
| Local Windows CLI or VS Code extension | Shared scanner, hooks, and native window backend | Automated compatibility tests pass. Native Windows verification is still required. |
| Other local terminal apps | Monitoring uses Codex's open files, whatever the terminal | Window controls depend on the process tree and readable window titles. |
| WSL, SSH, containers, and remote extension hosts | Needs a bridge running in that environment | Not supported. |

`tests/test_codex_sessions.py` covers CLI and extension record formats,
pagination, partial writes, compaction, context arithmetic, parent and helper
relationships, four threads in one process, closed sessions, the read-only index
fallback, hook merging, permission isolation, and mixed-provider rendering. CI runs
the suite and the native smoke test on Windows and macOS after each push.

Other Codex versions and remote environments aren't claimed as compatible until
they pass their own native checks.

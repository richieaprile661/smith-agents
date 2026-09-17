# Hermes Agent

Smith Agents can display local [Hermes Agent](https://hermes-agent.nousresearch.com/)
chats alongside Claude Code and Codex. No plugin or hook installation is needed.

Start Hermes and the widget on the same computer. The widget reads
`~/.hermes/runtime/active_sessions.json` and `~/.hermes/state.db`. For a custom
home or a named profile, launch the widget with `HERMES_HOME` set to that
profile's directory. One Hermes home is monitored per widget process.

Each chat gets its own card and the Hermes portrait logo. The card name uses
the project folder, with the chat title shown below when several sessions share
that folder. A chat launched from the home directory, or with no known folder,
uses its chat title instead. Cards also show the model, project folder, latest
request, reply, and tool names when available.
**Go to session** uses the existing host-window matching; it cannot select an
individual chat inside a shared desktop window. Ambiguous windows may need to
be opened manually. **End in Hermes** leaves ending a chat to Hermes itself.

## Usage

Select the Hermes logo to see saved session tokens and estimated USD cost in
both the header and the attached edge drawer as square grids. Each grid has
100 squares; its label gives the tokens or estimated USD cost per square.
The scale increases by powers of ten when needed, and partial squares round
down. These are cumulative saved amounts, not quota percentages. Unknown
readings show an empty grid and a dash. Click the drawer for numeric details in Usage.
The arrows browse saved sessions and their model/provider/task breakdowns;
Stats shows the selected session's API calls, tool calls, and messages.
The default selection prefers a live session, otherwise the newest saved one.

Readings refresh locally every 30 seconds. The latest 100 saved session rows
and up to 100 model records per session are available; a `+` on the page count
indicates older records were omitted. Compression-created session IDs remain
separate saved records; these are not combined into a conversation lifetime total.
Token totals are input plus output. Cache and reasoning counters are separate
breakdowns and are not added again. Missing counters show a dash. Costs are
Hermes's saved estimates, not verified bills or remaining account balances.
An unavailable database retains the last reading with a visible stale notice.
Older Hermes versions may not record model breakdowns or all counters.

## Compatibility and limits

- Requires a Hermes version that writes the active-session registry, including
  its `process_start_time` field. CLI, TUI, and desktop leases are recognized.
  Older versions without that registry will not appear.
- A lease must match a running Hermes process and its creation time. Historical
  database rows, stale leases, and ambiguous ownership do not create live cards.
- The SQLite reader uses read-only mode and bounded queries. Missing, busy, or
  incompatible databases leave verified sessions visible as **Activity unknown**.
- **Recent activity** means Hermes saved a message in the last 90 seconds.
  **Last reply saved** requires an assistant message with an explicit `stop`
  finish reason. **Quiet · check Hermes** is not proof of a pending approval.
  Streaming and in-memory work can be ahead of the saved conversation.
- Context tokens and capacity remain unknown. Cumulative session usage and
  message token counts are not treated as context-window usage.
- Hermes account limits, verified billing, approval handling, and delegated
  helper discovery are not integrated. All three agents' cards appear together.
- Remote Hermes instances (SSH, WSL, containers, hosted agents) are not detected.
  Only the selected local Hermes home is read; other profiles are not scanned.

The widget does not import or launch Hermes, call models, read credentials,
or change its configuration, registry, or conversation database. System and
reasoning messages are excluded from the queried fields.

The adapter follows the upstream
[active-session registry](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/active_sessions.py)
and [database schema](https://github.com/NousResearch/hermes-agent/blob/main/hermes_state_common.py)
inspected on 2026-09-17. These are internal formats and can change. Validation
uses synthetic registries, databases, and process fixtures. Local live-session
detection and saved request, reply, model, and tool readings were also verified.

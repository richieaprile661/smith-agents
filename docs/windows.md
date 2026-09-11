# Windows VS Code bridge

From the source checkout, install the widget and configure the optional bridge:

```powershell
python -m pip install -e .
python tools/configure_vscode_context.py
```

The installer backs up VS Code user settings, preserves any existing executable
wrapper, and sets `claudeCode.claudeProcessWrapper` to a generated native `.exe`.
It uses the current console Python interpreter and this checkout, so keep both
in place. To select another interpreter, pass `--executable C:/path/to/python.exe`;
`pythonw.exe` and the widget GUI shim cannot carry the protocol streams.
For a different VS Code profile or Insiders, pass `--settings` with its settings
file. Settings must be valid JSON (comments/trailing commas are not supported).
The default bridge directory is `%USERPROFILE%/.claude/widget-context`, or
`widget-context` under `CLAUDE_CONFIG_DIR` when set.

Reopen the Claude chat or reload its VS Code window when idle, then finish a
response. Already-running Claude processes cannot acquire the bridge in place.
New wrapped chats may start in Manual permission mode, as on Mac.

If VS Code reports `ModuleNotFoundError: No module named
'claude_widget.vscode_context'`, its generated launcher still uses the package
name from before Smith Agents. The compatibility module in current releases
forwards that import to the current bridge. From your updated Smith Agents
checkout, rerun these commands to install it and regenerate the launcher with
the current package path and Python interpreter:

```powershell
python -m pip install -e .
python tools/configure_vscode_context.py
```

Use the same `--settings` and `--directory` options as the original setup if you
customized them. Reconfiguration preserves the original wrapper and unrelated
settings. Reload the VS Code window when idle afterward. Regeneration is also
needed if you moved the checkout or removed the launcher's Python interpreter.

The bridge forwards the protocol, arguments, working directory, and environment.
It saves only the main session's reported context capacity and model metadata;
capacity is not guessed from a model name. Subagent capacities still require
Claude's status-line feed, as on Mac.

Pending permission requests use a token-authenticated server bound only to
`127.0.0.1`, with a randomly assigned port. Request contents remain in memory;
only endpoint metadata is written to disk. Mac continues to use Unix sockets.
The yellow agent panel shows **Allow…** and **Deny** directly for a supported
pending request, without expanding it. **Allow…**, **Approval needed**, or the
drawer's **Review & allow…** opens a scrollable, selectable review containing
full tool input. **Allow once** sends the exact input without changing persistent
permissions. **Deny** rejects it; Enter, Escape, Cancel, and closing the review
leave it unanswered. Responses are matched to session, request, and fingerprint;
the first answer in VS Code or the widget wins. Questions remain in VS Code.

**End session…** rechecks the selected session after confirmation. It stops only
its verified `claude.exe` process, checking creation time on the same Windows
handle used to terminate it. VS Code, terminal hosts, shared wrappers, and
processes shared with other sessions or listed subagents are not terminated.
Stop shared agents individually inside Claude. Other independent sessions in
the project remain running.

Each agent row distinguishes **Main agent** from **Subagent** and reports its
host window as **in front**, **in background**, **hidden**, or **unknown**.
Background means another window has focus; it does not mean the agent stopped
working. Hidden means minimized or hidden by the operating system. Unknown
means the widget cannot reliably identify or inspect the window.
Subagents report their **parent window**, and their Open/Hide actions use that
window. The indicator describes the host window, not the selected chat or
terminal tab; sessions sharing one window share its window state.

To restore the previous wrapper while preserving other current settings:

```powershell
python tools/configure_vscode_context.py --remove
```

Reopen chats after removal. The installer refuses to overwrite a wrapper setting
changed by another tool or the user after installation.

Validation: `python -m unittest discover -s tests -v` covers native executable
launching, stream forwarding, Unicode/quoting, capacities, relay decisions and
cancellations, installer restoration, and Windows review controls. Run
`python -m smith_agents --demo --smoke-test` for the native widget smoke test.
Mac runtime tests remain in the Windows/macOS CI matrix.

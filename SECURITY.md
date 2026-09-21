# Security policy

Smith Agents reads local session files and, for usage readings, the credentials
that Claude Code and Codex already keep on your computer. It sends Claude's
existing login only to Anthropic's usage endpoint, and nothing else leaves your
machine. A bug in that handling is a security issue, and so is anything that
lets another local process or a malicious session transcript make the widget
misbehave.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use GitHub's private report form instead: open the repository's
[Security tab](https://github.com/richieaprile661/smith-agents/security) and
choose **Report a vulnerability**. Only the maintainer sees the report.

Include the platform, the widget version, what the widget was connected to,
and the steps to reproduce. Leave credentials, tokens, and private session
content out of the report.

## What to expect

- Acknowledgement within seven days.
- A fix, or an explanation of why no fix is needed, as soon as it is ready.
  Fixes ship through the same one-line installer as any other update.
- Credit in the changelog if you want it.

## Supported versions

Only the latest version on `master` receives fixes. The installer always
installs that version.

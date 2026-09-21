# Contributing

Thanks for looking. Bug reports, fixes, and figure work are all welcome.

## Before you open an issue

Use the bug report form. It asks for the platform, the agent and app you use,
the theme, and the steps, which is what a fix needs. Strip private project
names, conversations, and credentials from screenshots and logs.

For a security problem, follow [SECURITY.md](SECURITY.md) instead.

## Working on the code

[docs/development.md](docs/development.md) has the code layout, how the figures
are drawn, and how the README images are rendered. The short version:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m smith_agents --demo      # sample data, no sign-in
.venv/bin/python -m unittest discover -s tests
```

On Windows, use `py` in place of `.venv/bin/python`.

The repository ships the installer wheel in `release/`. After changing anything
under `smith_agents/` or `claude_widget/`, rebuild it, or the release test fails:

```sh
python -m build --wheel --outdir release
```

CI runs the tests on Windows and macOS, at 100% and Retina display scale. A
pixel that only lands at 2× will fail on Windows, so keep drawing code scale
aware.

## Pull requests

- One change per pull request, with the tests that show it.
- Write the commit subject as a sentence about what the widget now does, not
  what the diff touches. The history is the changelog's source.
- Add a line to [CHANGELOG.md](CHANGELOG.md) under the current version.
- Don't commit design explorations. `output/` and `design/` are ignored for a
  reason.

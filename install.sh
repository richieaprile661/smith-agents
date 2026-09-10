#!/bin/sh
# Install Smith Agents into a private virtual environment on macOS.
set -eu

if [ "$(uname -s)" != Darwin ]; then
    echo 'This installer is for macOS. Use install.ps1 on Windows.' >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo 'Install Python 3.10 or newer from python.org, then run this command again.' >&2
    exit 1
fi

smith_install_dir=${SMITH_AGENTS_INSTALL_DIR:-"$HOME/Library/Application Support/Smith Agents"}
smith_package=${SMITH_AGENTS_PACKAGE:-https://github.com/richieaprile661/smith-agents/archive/refs/heads/master.zip}
mkdir -p "$smith_install_dir"
python3 -m venv "$smith_install_dir/venv"
smith_python="$smith_install_dir/venv/bin/python"
"$smith_python" -m pip install --upgrade "$smith_package"
"$smith_python" -c 'import smith_agents'

smith_app=$("$smith_python" -m smith_agents.macos_launcher "$smith_install_dir")
printf 'Installed: %s\n' "$smith_app"
echo 'To reopen, search for Smith Agents in Spotlight, or open it from your Applications folder.'
echo 'You can also drag the app onto your Dock.'
if [ "${SMITH_AGENTS_NO_LAUNCH:-0}" != 1 ]; then
    open "$smith_app"
fi

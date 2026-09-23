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
# Install the published release, not master: the wheel is pinned by version and checksum.
smith_version=1.1.5
smith_sha256=8c0a0fa8f9c2b6a580ec244df6bf757b5caafd7021a9d75549bbfa1bb6350261
smith_wheel=smith_agents-$smith_version-py3-none-any.whl
mkdir -p "$smith_install_dir"
if [ -n "${SMITH_AGENTS_PACKAGE:-}" ]; then
    smith_package=$SMITH_AGENTS_PACKAGE
else
    smith_download=$(mktemp -d)
    trap 'rm -rf "$smith_download"' EXIT
    smith_package="$smith_download/$smith_wheel"
    curl -fsSL -o "$smith_package" "https://github.com/richieaprile661/smith-agents/releases/download/v$smith_version/$smith_wheel"
    if [ "$(shasum -a 256 "$smith_package" | cut -d ' ' -f 1)" != "$smith_sha256" ]; then
        echo "The downloaded Smith Agents $smith_version package failed its checksum. Nothing was installed." >&2
        exit 1
    fi
fi
python3 -m venv "$smith_install_dir/venv"
smith_python="$smith_install_dir/venv/bin/python"
# Reinstall even when the version matches, so a repeat run repairs the environment.
"$smith_python" -m pip install --upgrade --force-reinstall "$smith_package"
"$smith_python" -c 'import smith_agents'

smith_app=$("$smith_python" -m smith_agents.macos_launcher "$smith_install_dir")
printf 'Installed: %s\n' "$smith_app"
echo 'To reopen, search for Smith Agents in Spotlight, or open it from your Applications folder.'
echo 'You can also drag the app onto your Dock.'
echo 'On first launch, choose Open Accessibility Settings to enable window controls, or Not Now to set them up later.'
if [ "${SMITH_AGENTS_NO_LAUNCH:-0}" != 1 ]; then
    open "$smith_app"
fi

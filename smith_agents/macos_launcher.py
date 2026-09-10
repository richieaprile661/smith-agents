"""Create a Finder/Spotlight launcher for a Python installation of Smith Agents."""
import argparse
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import tempfile

from PIL import Image
from . import __version__


MARKER = "SmithAgentsManagedLauncher"


def create_launcher(python, install_dir, applications_dir):
    python = Path(python).absolute()
    install_dir = Path(install_dir).absolute()
    applications_dir = Path(applications_dir).expanduser().absolute()
    applications_dir.mkdir(parents=True, exist_ok=True)
    install_dir.mkdir(parents=True, exist_ok=True)
    bundle = applications_dir / "Smith Agents.app"
    for name in ("Smith Agents.app", "Smith Agents Launcher.app"):
        bundle = applications_dir / name
        if not bundle.exists():
            break
        try:
            info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
        except (OSError, ValueError):
            continue
        if info.get(MARKER) is True:
            break
    else:
        raise FileExistsError("Existing apps occupy both Smith launcher names; choose another applications directory.")

    with tempfile.TemporaryDirectory(prefix=".smith-launcher-", dir=applications_dir) as temporary:
        staging = Path(temporary) / bundle.name
        contents = staging / "Contents"
        executable = contents / "MacOS" / "Smith Agents"
        resources = contents / "Resources"
        executable.parent.mkdir(parents=True)
        resources.mkdir()
        info = {"CFBundleName": bundle.stem, "CFBundleDisplayName": bundle.stem,
                "CFBundleIdentifier": "io.github.richieaprile661.smith-agents.launcher",
                "CFBundleExecutable": executable.name, "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
                "CFBundleIconFile": "smith-agents.icns", "LSUIElement": True,
                "NSHighResolutionCapable": True, MARKER: True}
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        command = shlex.join([str(python), "-m", "smith_agents"])
        log = shlex.quote(str(install_dir / "launch.log"))
        executable.write_text('#!/bin/sh\nexec ' + command + ' "$@" >>' + log + ' 2>&1\n')
        executable.chmod(0o755)
        with Image.open(Path(__file__).parent / "assets/smith-agents.ico") as icon:
            icon.convert("RGBA").resize((1024, 1024), Image.Resampling.LANCZOS).save(
                resources / "smith-agents.icns", format="ICNS")
        backup = Path(temporary) / "previous.app"
        if bundle.exists():
            bundle.rename(backup)
        try:
            staging.rename(bundle)
        except OSError:
            if backup.exists():
                backup.rename(bundle)
            raise
    return bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("install_dir", type=Path)
    parser.add_argument("--applications-dir", type=Path,
                        default=Path(os.environ.get("SMITH_AGENTS_APPLICATIONS_DIR") or Path.home() / "Applications"))
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("This launcher is for macOS.")
    bundle = create_launcher(sys.executable, args.install_dir, args.applications_dir)
    registry = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
    subprocess.run([registry, "-f", str(bundle)], stdout=subprocess.DEVNULL, check=True)
    print(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

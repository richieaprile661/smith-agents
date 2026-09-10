"""Install the VS Code capacity bridge while preserving the existing launcher."""
import argparse
import json
import os
import tempfile
from pathlib import Path
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smith_agents.context_bridge import atomic_json

KEY = "claudeCode.claudeProcessWrapper"


def configure(settings_path, directory, executable, remove=False):
    if sys.platform not in ("darwin", "win32"):
        raise RuntimeError("The VS Code widget launcher supports macOS and Windows.")
    settings = json.loads(settings_path.read_text(encoding="utf-8-sig")) if settings_path.exists() else {}
    state_path = directory / "vscode-bridge.json"
    state = json.loads(state_path.read_text(encoding="utf-8-sig")) if state_path.exists() else None
    launcher = directory / ("vscode-launcher.exe" if sys.platform == "win32" else "vscode-launcher.sh")
    if state and settings.get(KEY) != state["installed"]:
        raise RuntimeError("VS Code launcher changed since installation; refusing to overwrite it.")
    if remove:
        if not state:
            return
        if state["original"] is None:
            settings.pop(KEY, None)
        else:
            settings[KEY] = state["original"]
        atomic_json(settings_path, settings)
        state_path.unlink()
        return
    original = state["original"] if state else settings.get(KEY)
    if original and not isinstance(original, str):
        raise ValueError("The existing VS Code launcher must be an executable path.")
    command = [str(executable), "--vscode-context-bridge"]
    if original:
        command.append(original)
    directory.mkdir(parents=True, exist_ok=True)
    if not state:
        backup = directory / "vscode-settings-backup.json"
        backup.write_bytes(settings_path.read_bytes() if settings_path.exists() else b"{}\n")
        backup.chmod(0o600)
    if sys.platform == "win32":
        write_windows_launcher(directory, executable, original)
    else:
        launcher.write_text("#!/bin/sh\nexec " + shlex.join(command) + ' "$@"\n', encoding="utf-8")
        launcher.chmod(0o700)
    atomic_json(state_path, {"original": original, "installed": str(launcher)})
    settings[KEY] = str(launcher)
    atomic_json(settings_path, settings)


def write_windows_launcher(directory, executable, original):
    # Use the same native console launcher as pip. No cmd.exe quoting or GUI
    # pythonw streams: the extension passes the Claude executable as argv[1].
    from distlib.scripts import ScriptMaker
    executable = Path(executable).resolve()
    if executable.name.lower() not in ("python.exe", "python3.exe"):
        raise ValueError("On Windows --executable must be the console python.exe, not pythonw or the widget GUI shim.")
    source_root = str(Path(__file__).resolve().parent.parent)
    script = ("#!python\n"
              "import sys\n"
              "sys.path.insert(0, " + repr(source_root) + ")\n"
              "from smith_agents.vscode_context import main\n"
              "sys.exit(main(" + repr([original] if original else []) + " + sys.argv[1:]))\n")
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "vscode-launcher.py"
        source.write_text(script, encoding="utf-8")
        maker = ScriptMaker(temporary, str(directory))
        maker.executable = str(executable)
        maker.clobber = True
        maker.make(source.name)


def default_settings():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "Code/User/settings.json"
    return Path.home() / "Library/Application Support/Code/User/settings.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=default_settings())
    parser.add_argument("--directory", type=Path, default=Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "widget-context")
    parser.add_argument("--executable", type=Path, default=Path(sys.executable) if sys.platform == "win32" else None)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    if args.executable is None:
        parser.error("--executable is required on macOS")
    configure(args.settings, args.directory, args.executable.resolve(), args.remove)
    print("VS Code capacity bridge removed." if args.remove else
          "VS Code capacity bridge installed. Reopen Claude's chat to use the new launcher.")

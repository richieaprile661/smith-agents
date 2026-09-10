"""Install/remove the local context feed, preserving Claude's existing status lines."""
import argparse
import json
from pathlib import Path
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smith_agents.context_bridge import atomic_json


def configure(claude_dir, executable, remove=False):
    settings_path = claude_dir / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    directory = claude_dir / "widget-context"
    config_path = directory / "bridge.json"
    keys = {"main": "statusLine", "subagents": "subagentStatusLine"}
    commands = {kind: shlex.join([str(executable), "--context-bridge", str(directory), kind])
                for kind in keys}
    saved = json.loads(config_path.read_text()) if config_path.exists() else None
    if remove:
        if not saved:
            return
        for kind, key in keys.items():
            if (settings.get(key) or {}).get("command") == saved["installed"][kind]:
                original = saved["original"].get(key)
                if original is None:
                    settings.pop(key, None)
                else:
                    settings[key] = original
        atomic_json(settings_path, settings)
        config_path.unlink()
        return
    if saved and any((settings.get(key) or {}).get("command") != saved["installed"][kind]
                     for kind, key in keys.items()):
        raise RuntimeError("Status-line settings changed since installation; remove the old bridge before reinstalling.")
    original = saved["original"] if saved else {key: settings.get(key) for key in keys.values()}
    if not saved:
        atomic_json(directory / "settings-backup.json", settings)
    for kind, key in keys.items():
        settings[key] = dict(original.get(key) or {}, type="command", command=commands[kind])
    atomic_json(config_path, {"original": original, "installed": commands,
                            "commands": {kind: (original.get(key) or {}).get("command")
                                         for kind, key in keys.items()}})
    atomic_json(settings_path, settings)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude")
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    configure(args.claude_dir, args.executable.resolve(), args.remove)
    print("Context feed removed." if args.remove else "Context feed installed; existing status-line output preserved.")

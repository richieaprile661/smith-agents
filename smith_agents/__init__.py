"""Smith Agents - a floating console for Claude and Codex usage and sessions.

The Pillow renderer and controller are shared. Each operating system provides
its own window, tray/menu bar, credential reader, and process services.
"""
__version__ = "1.1.0"

__all__ = ["__version__", "main"]


def main():
    """Prepare demo isolation before importing the renderer's configuration."""
    import os
    import sys
    import tempfile
    from .branding import config_override
    if len(sys.argv) > 1 and sys.argv[1] == "--codex-hook":
        from .codex_hooks import main as codex_hook_main
        return codex_hook_main(["capture", *sys.argv[2:]])
    if len(sys.argv) > 1 and sys.argv[1] == "--vscode-context-bridge":
        from .vscode_context import main as vscode_bridge_main
        return vscode_bridge_main(sys.argv[2:])
    if "--context-bridge" in sys.argv:
        from .context_bridge import main as bridge_main
        index = sys.argv.index("--context-bridge")
        return bridge_main(sys.argv[index + 1], sys.argv[index + 2])
    if "--version" in sys.argv:
        print(__version__)
        return 0
    if "--demo" in sys.argv and not config_override():
        os.environ["SMITH_AGENTS_CONFIG_DIR"] = tempfile.mkdtemp(prefix="smith-agents-demo-")
    try:
        from .app import main as _main
        return _main()
    except ImportError as exc:
        message = ("A required component is missing: %s.\n\n"
                   "For a source installation, run:\n%s -m pip install -e .\n\n"
                   "For a downloaded app, download and unzip a fresh copy."
                   % (getattr(exc, "name", None) or "dependency", sys.executable))
        print(message, file=sys.stderr)
        try:
            from .runtime import backend
            backend().show_error(message)
        except (ImportError, RuntimeError):
            pass
        return 1

"""Keep existing VS Code launchers working after the package rename.

Generated Windows executables embed this module path. Delegate to the current
bridge so those executables also receive protocol and shutdown fixes.
"""
from smith_agents.vscode_context import main

__all__ = ["main"]

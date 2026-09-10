"""PyInstaller entry point; all runtime assets remain inside the package."""
from smith_agents import main

if __name__ == "__main__":
    raise SystemExit(main())

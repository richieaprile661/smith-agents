"""Write the Windows app icon from the same artwork as the Mac app's.

Run after changing the app icon artwork; the .ico ships in the package, and
the Start menu and login shortcuts point at it.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    sys.path.insert(0, str(ROOT))
    from smith_agents.artwork import WINDOWS_ICON_SIZES, windows_icon
    target = ROOT / "smith_agents" / "assets" / "smith-agents.ico"
    windows_icon().save(target, format="ICO", sizes=[(size, size) for size in WINDOWS_ICON_SIZES])
    print(target)


if __name__ == "__main__":
    main()

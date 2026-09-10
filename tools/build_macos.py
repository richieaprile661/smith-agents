"""Build a self-contained, unsigned app for the current Mac architecture."""
import os
from pathlib import Path
import subprocess
import sys
import shutil

ROOT = Path(__file__).resolve().parent.parent


def main():
    if sys.platform != "darwin":
        raise SystemExit("Build the Mac app on macOS.")
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    # The project already supplies the artwork. Reuse it as an ICNS resource.
    from PIL import Image
    icon = build / "smith-agents.icns"
    image = Image.open(ROOT / "smith_agents/assets/smith-agents.ico").convert("RGBA")
    image.resize((1024, 1024), Image.Resampling.LANCZOS).save(icon, format="ICNS")
    env = dict(os.environ)
    env.setdefault("PYINSTALLER_CONFIG_DIR", str(build / "pyinstaller-cache"))
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--onedir",
        "--name", "Smith Agents", "--icon", str(icon),
        "--osx-bundle-identifier", "io.github.richieaprile661.claude-usage-widget",
        "--paths", str(ROOT), "--collect-data", "smith_agents",
        "--hidden-import", "smith_agents.platform_darwin",
        "--hidden-import", "AppKit", "--hidden-import", "Foundation",
        "--hidden-import", "PyObjCTools.AppHelper", "--hidden-import", "psutil",
        "--exclude-module", "tkinter", "--exclude-module", "pystray",
        "--exclude-module", "smith_agents.platform_win32",
        "--specpath", str(build), "--distpath", str(ROOT / "dist"),
        "--workpath", str(build / "pyinstaller"), str(ROOT / "tools/mac_launcher.py"),
    ], cwd=ROOT, env=env, check=True)
    # Finalize before signing: macOS requires Info.plist to be covered too.
    import plistlib
    bundle = ROOT / "dist/Smith Agents.app"
    plist = bundle / "Contents/Info.plist"
    with plist.open("rb") as handle:
        info = plistlib.load(handle)
    info.update(LSUIElement=True, NSHighResolutionCapable=True,
                CFBundleDisplayName="Smith Agents", CFBundleName="Smith Agents",
                CFBundleShortVersionString="1.1.0", CFBundleVersion="1.1.0",
                NSHumanReadableCopyright="MIT — richieaprile661")
    with plist.open("wb") as handle:
        plistlib.dump(info, handle)
    notices = bundle / "Contents/Resources/Licenses"
    notices.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "LICENSE", notices / "Smith-Agents-MIT.txt")
    for font_license in (ROOT / "smith_agents/fonts").glob("OFL-*.txt"):
        shutil.copyfile(font_license, notices / font_license.name)
    # Retain the license files supplied by each bundled runtime distribution.
    from importlib.metadata import distribution
    for name in ("pillow", "psutil", "pyobjc-core", "pyobjc-framework-Cocoa"):
        dist = distribution(name)
        for entry in dist.files or ():
            if entry.is_absolute() or ".." in entry.parts:
                continue
            if any(word in entry.name.lower() for word in ("license", "copying", "copyright")):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    target = notices / name / str(entry)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
    python_license = Path(sys.base_prefix) / "lib" / ("python%d.%d" % sys.version_info[:2]) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, notices / "Python-LICENSE.txt")
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(bundle)], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                    str(bundle), str(ROOT / "dist/Smith-Agents-macOS.zip")], check=True)
    print("Built:", bundle)
    print("Local testing build: ad-hoc signed, not notarized for public distribution.")


if __name__ == "__main__":
    main()

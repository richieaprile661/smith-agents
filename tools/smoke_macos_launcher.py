"""Open, quit, and reopen the installed Mac app using Launch Services."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time


def run(app, install_dir, timeout=30):
    with tempfile.TemporaryDirectory(prefix="smith-launcher-check-") as directory:
        try:
            for cycle in (1, 2):
                config = Path(directory) / str(cycle)
                log = config / "widget.log"
                # A normal Finder/Spotlight launch: the second open must work
                # after quitting, without forcing a parallel instance with -n.
                subprocess.run(["open", str(app), "--env", "SMITH_AGENTS_CONFIG_DIR=" + str(config),
                                "--args", "--demo", "--smoke-test"], check=True, timeout=10)
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    result = log.read_text() if log.exists() else ""
                    if "smoke test failed" in result:
                        raise RuntimeError(result)
                    match = re.search(r"exit pid=(\d+)", result)
                    if "smoke test passed" in result and match:
                        try:
                            os.kill(int(match[1]), 0)
                        except ProcessLookupError:
                            print("App open / quit cycle %d passed" % cycle, flush=True)
                            break
                    time.sleep(.2)
                else:
                    raise TimeoutError("App cycle %d did not finish within %ds" % (cycle, timeout))
        except Exception:
            for log in [*Path(directory).glob("*/widget.log"), install_dir / "launch.log"]:
                if log.exists():
                    print(str(log) + "\n" + log.read_text(errors="replace")[-12000:], flush=True)
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path.home() / "Applications/Smith Agents.app")
    parser.add_argument("--install-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.app, args.install_dir)

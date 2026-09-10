"""Make one usage request and print connection status, never credentials."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smith_agents.core import UsageError, build_metrics, fetch_usage


def main():
    try:
        payload, _oauth = fetch_usage()
        metrics = build_metrics(payload)
    except UsageError as exc:
        print("Connection status:", exc.kind)
        print(str(exc))
        return 2
    print("Connected to Claude. Received %d usage limits." % len(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""claude-env services :: list the live local UI servers and their ports/URLs.

Because the UI servers pick a free port when their preferred one is busy, this is
how you find out *which port is what* at any moment.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # repo root / $CLAUDE_ENV_HOME
from lib.services import list_live


def main() -> int:
    live = list_live()
    if not live:
        print("No claude-env UI servers are running.")
        print("  start one:  claude-env approvals-ui   |   claude-env dashboard serve")
        return 0
    print(f"{'SERVICE':<14}{'URL':<30}{'PID':<8}STARTED (UTC)")
    for e in live:
        print(f"{e.get('name',''):<14}{e.get('url',''):<30}"
              f"{str(e.get('pid','')):<8}{str(e.get('started_at',''))[:19]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

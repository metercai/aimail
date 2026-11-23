#!/usr/bin/env python3
"""Print the paused flag (or binding count) of the AIMail binding(s) under a home."""
import glob
import json
import sys


def main() -> int:
    home = sys.argv[1] if len(sys.argv) > 1 else "/root/.aimail"
    mode = sys.argv[2] if len(sys.argv) > 2 else "paused"
    paths = sorted(glob.glob(home + "/systems/*/*/agentmail.json"))
    if mode == "count":
        print(len(paths))
        return 0
    if not paths:
        print("no-binding")
        return 0
    print(json.load(open(paths[0])).get("paused"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

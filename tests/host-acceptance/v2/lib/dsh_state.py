#!/usr/bin/env python3
"""Print the paused flag (or binding count) of the AIMail binding(s) under a home."""
import glob
import json
import sys


def summarize(outdir: str) -> int:
    """Aggregate per-turn snapshots written by dsh_activate.py into one line."""
    import glob
    states = []
    for f in sorted(glob.glob(outdir + "/state_*.json")):
        try:
            states.append(json.load(open(f)))
        except Exception:
            pass
    max_b = max((s.get("bindings") or 0 for s in states), default=0)
    # 'pause' is proven if ANY turn observed paused=True; the last turn may have unbound it
    paused = any(s.get("paused") is True for s in states)
    after = states[-1].get("bindings") if states else None
    line = f"max_bindings={max_b} paused_seen={paused} final_bindings={after} turns={len(states)}"
    print(line)
    open(outdir + "/journey_state", "w").write(line + "\n")
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[2] == "summarize":
        return summarize(sys.argv[1])
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

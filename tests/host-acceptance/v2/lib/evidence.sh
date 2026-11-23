#!/usr/bin/env bash
# Evidence capture: every L2 run leaves a self-describing directory so a green
# (or red) is auditable without re-running.
#
#   EV_DIR/run.json      host, versions, gateway pin, timings
#   EV_DIR/results.txt   the PASS/RED/GAP rows of every stage this host ran
#   EV_DIR/host.log      host tail (sanitised)
#   EV_DIR/binding.json  binding without the key
set -uo pipefail

ev_init() {
  EV_ROOT="${EV_ROOT:-$PWD/evidence}"
  EV_DIR="${EV_DIR:-$EV_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-${HOST_NAME:-unknown}-${CASE:-x}}"
  mkdir -p "$EV_DIR"
  export EV_DIR
}

ev_meta() {  # ev_meta key value
  python3 - "$EV_DIR/run.json" "$1" "$2" <<'PY'
import json, os, sys
p, k, v = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(p)) if os.path.exists(p) else {}
d[k] = v
json.dump(d, open(p, "w"), indent=2, sort_keys=True)
PY
}

ev_log() {  # ev_log <src> [max lines]
  local src="$1" n="${2:-200}"
  [ -f "$src" ] && tail -n "$n" "$src" | sed -E 's/(api[_-]?key|token|raw_key)"?[:=][[:space:]]*"?[A-Za-z0-9_\-\.]{8,}/\1=[REDACTED]/gi' >"$EV_DIR/host.log" 2>/dev/null || true
}

ev_results() {  # persist the rows this host produced (RESULT_LINES is per-host reset by the runner)
  [ -n "${EV_DIR:-}" ] || return 0
  printf '%s\n' "${RESULT_LINES:-}" >"$EV_DIR/results.txt" 2>/dev/null || true
}

ev_binding() {  # ev_binding <path>
  [ -f "$1" ] && python3 - "$1" "$EV_DIR/binding.json" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
d = json.load(open(src))
for k in list(d):
    if "key" in k.lower():
        d[k] = "<redacted>"
json.dump(d, open(dst, "w"), indent=2, sort_keys=True)
PY
}

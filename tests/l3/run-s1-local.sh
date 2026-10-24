#!/usr/bin/env bash
# run-s1-local.sh — Scenario 1 (Single Agent Dedicated Address) **post-release local regression**.
#
# Why only run locally: aimail-advanced is closed-source, cannot enter GitHub Actions runner.
# Therefore, the post-release regression for scenario 1 does not enter the L3 cloud matrix; it only runs on the local machine (linux) using two typical hosts
# (hermes / dsh) to execute real user chains, starting from "get activation code" each time.
#
# Parts consistent with the runner: official host installation form, the agent's own tool gate, and the real apply chain.
# Localization: the host uses a **clean snapshot** (copied from an existing official installation on the local machine, source directory is read-only),
#             Do not re-download the 3.6G host tree; the gateway is a local advanced real binary (closed-source).
#
#      + email signature (agent_signature) is effective (whoami.rs:44-52, welcome approve UPSERT read-back point)
#   bash tests/l3/run-s1-local.sh --cli <published CLI asset path> [--adv-bin <advanced>] \
#        [--hosts "hermes dsh"] [--evidence /tmp/s1-local]
# Exit codes: 0 all PASS · 1 has FAIL · 2 CANNOT JUDGE
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

CLI="${AIMAIL_BIN:-}"
ADV_BIN="${ADV_BIN:-}"
HOSTS="hermes dsh"
EVIDENCE="${S1_EVIDENCE:-/tmp/s1-local}"

while [ $# -gt 0 ]; do
  case "$1" in
    --cli) CLI="${2:-}"; shift 2 ;;
    --adv-bin) ADV_BIN="${2:-}"; shift 2 ;;
    --hosts) HOSTS="${2:-}"; shift 2 ;;
    --evidence) EVIDENCE="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$CLI" ] || [ ! -x "$CLI" ]; then
  echo "✗ Requires --cli <published CLI asset path> (post-release regression must use published assets, not local build)" >&2
  exit 2
fi
if [ -z "$ADV_BIN" ]; then
  for c in "$REPO/../aimail-advanced/target/release/aimail-advanced" \
           "$REPO/../aimail-advanced/target/debug/aimail-advanced"; do
    [ -x "$c" ] && { ADV_BIN="$c"; break; }
  done
fi
if [ -z "$ADV_BIN" ] || [ ! -x "$ADV_BIN" ]; then
  echo "✗ Cannot find aimail-advanced binary (--adv-bin); closed-source items only local" >&2
  exit 2
fi

echo "════════════════════════════════════════════════════════════"
echo " Scenario 1 post-release regression (local)"
echo "   CLI      : $CLI  ($( sha256sum "$CLI" | cut -c1-12 )…)"
echo "   advanced : $ADV_BIN  ($( sha256sum "$ADV_BIN" | cut -c1-12 )…)"
echo "   hosts    : $HOSTS"
echo "   evidence : $EVIDENCE"
echo "════════════════════════════════════════════════════════════"

overall=0
declare -a rows=()
for h in $HOSTS; do
  log="$EVIDENCE/$h/run.log"
  mkdir -p "$EVIDENCE/$h"
  python3 "$HERE/run-s1-local.py" --host "$h" --adv-bin "$ADV_BIN" --aimail-bin "$CLI" \
      --evidence "$EVIDENCE" > "$log" 2>&1
  rc=$?
  fails=$(python3 - "$EVIDENCE/$h/cases.json" <<'PY' 2>/dev/null || echo "?"
import json,sys
d=json.load(open(sys.argv[1]))
print(sum(1 for c in d["cases"] if not c["ok"]))
PY
)
  case "$rc" in
    0) verdict=PASS ;;
    2) verdict="CANNOT JUDGE" ;;
    *) verdict=FAIL ;;
  esac
  rows+=("$(printf '  %-8s %-14s fail=%s  %s' "$h" "$verdict" "${fails:-?}" "$log")")
  tail -3 "$log" | sed 's/^/    /'
  [ "$rc" -gt "$overall" ] && overall=$rc
done

echo
printf '%s\n' "${rows[@]}"
echo
if [ "$overall" -eq 0 ]; then
  echo "SCENARIO-1 LOCAL REGRESSION: PASS"
else
  echo "SCENARIO-1 LOCAL REGRESSION: FAIL/CANNOT-JUDGE (see $EVIDENCE)"
fi
exit "$overall"

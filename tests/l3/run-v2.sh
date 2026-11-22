#!/usr/bin/env bash
# L3 V2 gate wrapper.
#
# Owner doctrine (2026-11-23):
#   L3 = published SDK, installed BY THE AGENT during the journey; closed-loop
#        regression only (code → prompt → install → activate → bind → welcome →
#        identity card + signature).
#   Cross-platform representativeness: dsh (tssdk) + hermes (pysdk).
#   hermes is NOT migrated yet → executed only with --include-legacy and always
#   reported as SKIP_LEGACY (never counted as green).
#
#   run-v2.sh [--cell platform/agent] [--include-legacy]
#
# Per-cell evidence is written next to the L3 logs (gateway pin + versions).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PINS="$ROOT/tests/agent-env/pins.env"

PLATFORMS="${PLATFORMS:-linux-amd64 linux-arm64 macos-arm64 windows-amd64}"
CELL=""; INCLUDE_LEGACY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --cell) CELL="$2"; shift 2 ;;
    --include-legacy) INCLUDE_LEGACY=1; shift ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done

pin() { grep -E "^$1=" "$PINS" | head -1 | cut -d= -f2-; }
NPM_VER="$(npm view agent-aimail version 2>/dev/null || echo unknown)"
GW_VER="$(pin GATEWAY_VERSION)"
echo "── L3 V2 gate  published SDK=$NPM_VER  gateway=$GW_VER  dsh=$(pin DSH_VERSION)  pi=$(pin PI_VERSION)"

PASS=0; FAIL=0; SKIP=0
run_cell() {
  local plat="$1" agent="$2" kind="$3"   # kind: v2 | legacy
  echo
  echo "== cell $plat/$agent ($kind)"
  if [ "$kind" = legacy ] && [ "$INCLUDE_LEGACY" != 1 ]; then
    echo "SKIP_LEGACY $plat/$agent — pysdk not migrated to SSE-V2 (run with --include-legacy to execute)"
    SKIP=$((SKIP+1)); return 0
  fi
  local log="/tmp/l3-${plat}-${agent}.log"
  if L3_PLATFORM="$plat" L3_AGENT="$agent" bash "$HERE/run-l3.sh" >"$log" 2>&1; then
    echo "PASS $plat/$agent"
    if [ "$kind" = legacy ]; then SKIP=$((SKIP+1)); else PASS=$((PASS+1)); fi
  else
    echo "FAIL $plat/$agent (see $log)"
    [ "$kind" = legacy ] && SKIP=$((SKIP+1)) || FAIL=$((FAIL+1))
  fi
}

if [ -n "$CELL" ]; then
  p="${CELL%%/*}"; a="${CELL##*/}"
  case "$a" in
    dsh|pi) run_cell "$p" "$a" v2 ;;
    hermes) run_cell "$p" "$a" legacy ;;
    *) echo "unknown agent $a"; exit 2 ;;
  esac
else
  for p in $PLATFORMS; do run_cell "$p" dsh v2; done
  for p in $PLATFORMS; do run_cell "$p" hermes legacy; done
fi

echo
echo "──────────────────────────────────────────────────────────"
printf 'L3 V2 gate: PASS=%d FAIL=%d SKIP_LEGACY=%d (published sdk=%s, gateway=%s)\n' "$PASS" "$FAIL" "$SKIP" "$NPM_VER" "$GW_VER"
[ "$FAIL" -gt 0 ] && { echo "VERDICT: FAIL"; exit 1; } || { echo "VERDICT: PASS"; exit 0; }

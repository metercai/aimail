#!/usr/bin/env bash
# @level L3
# @scenario 1
# L3 V2 gate wrapper.
#
# Owner doctrine (2026-11-23):
#   L3 = published SDK, installed BY THE AGENT during the journey; closed-loop
#        regression only (code → prompt → install → activate → bind → welcome →
#        identity card + signature).
#   L3 covers dsh (tssdk) only, on 4 platforms; pi/openclaw are covered by L2 on the
#   local platform. hermes (pysdk) is not migrated to SSE-V2 and its CLI install path
#   is retired ⇒ reported as SKIP_LEGACY, never executed, never counted as green.
#
#   run-v2.sh [--cell platform/agent] [--include-legacy (obsolete, kept for compat)]
#
# Per-cell evidence is written next to the L3 logs (gateway pin + versions).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PINS="$ROOT/tests/agent-env/pins.env"

PLATFORMS="${PLATFORMS:-linux-amd64 linux-arm64 macos-arm64 windows-amd64}"
# the local wrapper knows where the checkout is; CI passes AIMAIL_REPO explicitly
AIMAIL_REPO="${AIMAIL_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export AIMAIL_REPO
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
[ "$INCLUDE_LEGACY" = 1 ] && echo "note: --include-legacy is obsolete — the legacy CLI install path was retired (2026-11-23); hermes is reported as SKIP_LEGACY only"
skip_legacy() {   # never counted as green
  local plat="$1" agent="$2" why="$3"
  echo
  echo "SKIP_LEGACY $plat/$agent — $why"
  SKIP=$((SKIP+1))
}
run_cell() {
  local plat="$1" agent="$2"
  echo
  echo "== cell $plat/$agent (v2)"
  local log="/tmp/l3-${plat}-${agent}.log"
  if L3_PLATFORM="$plat" L3_AGENT="$agent" bash "$HERE/run-l3.sh" >"$log" 2>&1; then
    echo "PASS $plat/$agent"
    PASS=$((PASS+1))
  else
    echo "FAIL $plat/$agent (see $log)"
    FAIL=$((FAIL+1))
  fi
}

if [ -n "$CELL" ]; then
  p="${CELL%%/*}"; a="${CELL##*/}"
  case "$a" in
    dsh) run_cell "$p" "$a" ;;
    pi|openclaw) echo "note: $a is L2 scope (not part of the L3 matrix)" ;;
    hermes) skip_legacy "$p" "$a" "pysdk not migrated to SSE-V2; the CLI install path is retired — L3 is dsh-only" ;;
    *) echo "unknown agent $a"; exit 2 ;;
  esac
else
  for p in $PLATFORMS; do run_cell "$p" dsh; done
  for p in $PLATFORMS; do skip_legacy "$p" hermes "pysdk not migrated to SSE-V2; the CLI install path is retired — L3 is dsh-only"; done
fi

echo
echo "──────────────────────────────────────────────────────────"
printf 'L3 V2 gate: PASS=%d FAIL=%d SKIP_LEGACY=%d (published sdk=%s, gateway=%s)\n' "$PASS" "$FAIL" "$SKIP" "$NPM_VER" "$GW_VER"
[ "$FAIL" -gt 0 ] && { echo "VERDICT: FAIL"; exit 1; } || { echo "VERDICT: PASS"; exit 0; }

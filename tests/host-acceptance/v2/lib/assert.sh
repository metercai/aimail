#!/usr/bin/env bash
# Result vocabulary + counters for the L2/L3 harness.
#
#   PASS          assertion met
#   RED           product defect (carries the smallest evidence line)
#   GAP           fixture/environment defect (must be fixed by QA, not counted as product red)
#   SKIP_LEGACY   host not migrated to SSE-V2 (hermes/deerflow pysdk) — printed, never counted as green
#
# Exit code: RED>0 or GAP>0 -> non-zero. SKIP_LEGACY alone -> zero (but printed).
set -uo pipefail

N_PASS=${N_PASS:-0}; N_RED=${N_RED:-0}; N_GAP=${N_GAP:-0}; N_SKIP=${N_SKIP:-0}
RESULT_LINES=${RESULT_LINES:-}

_row() { printf '%-6s %-28s %-24s %s\n' "$1" "$2" "${3:-}" "${4:-}"; RESULT_LINES="${RESULT_LINES}${RESULT_LINES:+$'\n'}$(printf '%-6s %-28s %-24s %s' "$1" "$2" "${3:-}" "${4:-}")"; }

pass() { N_PASS=$((N_PASS+1)); _row PASS "$CASE" "${1:-}"; }
red()  { N_RED=$((N_RED+1));   _row RED  "$CASE" "${1:-}"; }
gap()  { N_GAP=$((N_GAP+1));   _row GAP  "$CASE" "${1:-}"; }
skip_legacy() { N_SKIP=$((N_SKIP+1)); _row SKIP_LEGACY "$CASE" "${1:-}"; }

assert_file() { [ -f "$1" ] && pass "$2" || red "$2 (missing $1)"; }
assert_eq()   { [ "$1" = "$2" ] && pass "${3:-}" || red "${3:-} ($1 != $2)"; }
assert_ne()   { [ "$1" != "$2" ] && pass "${3:-}" || red "${3:-} ($1 == $2)"; }
assert_grep() { grep -qaE "$1" "$2" 2>/dev/null && pass "${3:-}" || red "${3:-} (no /$1/ in $2)"; }
assert_not_grep() { ! grep -qaE "$1" "$2" 2>/dev/null && pass "${3:-}" || red "${3:-} (found /$1/ in $2)"; }

summary() {
  echo
  echo "──────────────────────────────────────────────────────────"
  printf 'L2/L3 matrix: PASS=%d RED=%d GAP=%d SKIP_LEGACY=%d\n' "$N_PASS" "$N_RED" "$N_GAP" "$N_SKIP"
  [ "$N_SKIP" -gt 0 ] && echo "  SKIP_LEGACY = pysdk hosts (hermes/deerflow) not migrated — excluded from the verdict"
  if [ "$N_RED" -gt 0 ] || [ "$N_GAP" -gt 0 ]; then
    echo "VERDICT: FAIL (RED=$N_RED GAP=$N_GAP)"; return 1
  fi
  echo "VERDICT: PASS"; return 0
}

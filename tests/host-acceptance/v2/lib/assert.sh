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
#: `N/A` — the obligation does not apply to this host (contract §16 C17 证明范围 / 附 E 的
#: `admission-only`). It is printed, never silently dropped, and it counts as neither green nor red:
#: a scoped-out obligation is a documentation fact, not a product result. Do NOT use it for a check
#: that was skipped because the fixture could not run it — that is GAP.
N_NA=${N_NA:-0}
na_scope() { N_NA=$((N_NA+1)); _row N/A "$CASE" "${1:-}"; }
#: `RISK-ACCEPTED` — a known, measured defect that the OWNER has signed off for this release.
#: Two rules, both enforced by review (not by code): (a) it must carry the signature reference,
#: (b) it is for "host cannot confirm, risk accepted" only — never for a fixture gap or a bug we
#: could fix. It is printed with the measured evidence, and counts as neither green nor red.
N_RISK=${N_RISK:-0}
risk_accepted() { N_RISK=$((N_RISK+1)); _row RISK-ACCEPTED "$CASE" "${1:-}"; }

assert_file() { [ -f "$1" ] && pass "$2" || red "$2 (missing $1)"; }
assert_eq()   { [ "$1" = "$2" ] && pass "${3:-}" || red "${3:-} ($1 != $2)"; }
assert_ne()   { [ "$1" != "$2" ] && pass "${3:-}" || red "${3:-} ($1 == $2)"; }
assert_grep() { grep -qaE "$1" "$2" 2>/dev/null && pass "${3:-}" || red "${3:-} (no /$1/ in $2)"; }
assert_not_grep() { ! grep -qaE "$1" "$2" 2>/dev/null && pass "${3:-}" || red "${3:-} (found /$1/ in $2)"; }

summary() {
  echo
  echo "──────────────────────────────────────────────────────────"
  printf 'L2/L3 matrix: PASS=%d RED=%d GAP=%d SKIP_LEGACY=%d N/A=%d RISK-ACCEPTED=%d\n' "$N_PASS" "$N_RED" "$N_GAP" "$N_SKIP" "$N_NA" "$N_RISK"
  [ "$N_SKIP" -gt 0 ] && echo "  SKIP_LEGACY = pysdk hosts (hermes/deerflow) not migrated — excluded from the verdict"
  [ "$N_NA" -gt 0 ] && echo "  N/A = obligation does not apply to that host (contract §16 C17 证明范围 / 附 E admission-only) — listed, not counted"
  if [ "$N_RED" -gt 0 ] || [ "$N_GAP" -gt 0 ]; then
    echo "VERDICT: FAIL (RED=$N_RED GAP=$N_GAP)"; return 1
  fi
  echo "VERDICT: PASS"; return 0
}

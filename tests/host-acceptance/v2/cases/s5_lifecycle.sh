#!/usr/bin/env bash
# S5 — lifecycle: pause marks the binding paused; unbind removes it.
set -uo pipefail
CASE="S5 lifecycle"
out="$1"
state="$(cat "$out/journey_state" 2>/dev/null || echo '')"

if [ "${LIFECYCLE:-0}" != "1" ]; then
  gap "lifecycle stage not requested (LIFECYCLE=0)"
  return 0
fi
[ -n "$state" ] || { gap "journey state missing (see $out/rc)"; return 0; }

paused="$(sed -n 's/.*paused_seen=\([A-Za-z]*\).*/\1/p' <<<"$state")"
final="$(sed -n 's/.*final_bindings=\([0-9]*\).*/\1/p' <<<"$state")"

maxb="$(sed -n 's/.*max_bindings=\([0-9]*\).*/\1/p' <<<"$state")"
[ "$paused" = "True" ] && pass "pause marked the binding (paused=True)" || red "pause did not mark the binding (paused=$paused)"
if [ "${final:-9}" -lt "${maxb:-0}" ]; then
  pass "unbind removed the binding ($maxb -> $final)"
else
  red "unbind did not remove a binding (final=$final, max=$maxb)"
fi

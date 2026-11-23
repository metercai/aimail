#!/usr/bin/env bash
# S5 — lifecycle: pause marks the binding paused, unbind removes it.
set -uo pipefail
CASE="S5 lifecycle"
out="$1"

if [ ! -f "$out/paused" ]; then
  gap "lifecycle stage not run (LIFECYCLE=0)"
  return 0
fi
p="$(cat "$out/paused" 2>/dev/null)"
[ "$p" = "True" ] || [ "$p" = "true" ] && pass "pause set paused=true" || red "pause did not mark the binding (paused=$p)"

n="$(cat "$out/bindings_after_unbind" 2>/dev/null || echo '')"
[ -z "$n" ] && gap "unbind result not captured" || { [ "$n" = "0" ] && pass "unbind removed the binding" || red "binding still present after unbind (n=$n)"; }

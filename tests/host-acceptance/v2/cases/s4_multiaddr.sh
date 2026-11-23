#!/usr/bin/env bash
# S4 — multi-address: tssdk hosts (dsh/openclaw) keep two bindings and two streams;
# pi is single-address (second bind must be rejected).
set -uo pipefail
CASE="S4 multi-address"
out="$1"; host="${2:-}"

if [ "$host" = "pi" ]; then
  n="$(cat "$out/bindings_count" 2>/dev/null || echo 2)"
  [ "$n" -le 1 ] && pass "second address rejected (single-address model)" \
                 || red "second binding written on pi (n=$n) — C1 violated"
  return 0
fi

n="$(cat "$out/bindings_count" 2>/dev/null || echo '')"
if [ -z "$n" ]; then
  gap "second-address stage not run (S4_ADDR not provided)"
elif [ "$n" -ge 2 ]; then
  pass "two bindings coexist (n=$n)"
else
  red "only $n binding(s) after the second activation"
fi

if grep -qaE "SSE reconcile: [2-9] gateway stream|reconcile: [2-9] stream" "$out/host.log" 2>/dev/null; then
  pass "two aggregate streams reconciled"
else
  gap "stream count for two addresses not observed in the log tail"
fi

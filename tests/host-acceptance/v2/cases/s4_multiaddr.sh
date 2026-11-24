#!/usr/bin/env bash
# S4 — multi-address: tssdk hosts (dsh/openclaw) keep two bindings; pi is single-address.
set -uo pipefail
CASE="S4 multi-address"
out="$1"; host="${2:-}"
state="$(cat "$out/journey_state" 2>/dev/null || echo '')"

if [ -z "$state" ]; then
  gap "journey state missing (see $out/rc)"
  return 0
fi
maxb="$(sed -n 's/.*max_bindings=\([0-9]*\).*/\1/p' <<<"$state")"

# Host capability decides which branch applies. `HOST_ADDRESS_MODEL` is exported by the
# host adapter (pi/hermes = single, dsh/openclaw/deerflow = multi); the earlier hardcoded
# `host = pi` test mis-labelled any other single-address host as a multi-address one.
_model="${HOST_ADDRESS_MODEL:-}"
if [ -z "$_model" ]; then
  case "$host" in pi|hermes) _model=single ;; *) _model=multi ;; esac
fi
if [ "$_model" = "single" ]; then
  [ "${maxb:-0}" -le 1 ] && pass "second address rejected (single-address model, $host)" \
                        || red "second binding written on $host (max=$maxb) — C1 violated"
  return 0
fi

if [ -z "${S4_ADDR:-}" ]; then
  gap "second-address stage not requested (S4_ADDR empty)"
elif [ "${maxb:-0}" -ge 2 ]; then
  pass "two bindings coexist (max=$maxb)"
else
  red "only $maxb binding(s) after the second activation"
fi

# one aggregate stream per (AgentSystemId, Gateway) covers all of its addresses,
# so the evidence is "from N binding(s)" with N>=2 (not N separate streams).
grep -qaE "from [2-9] binding|SSE reconcile: [2-9] gateway stream" "$out/host.log" 2>/dev/null \
  && pass "aggregate stream reconciled from 2+ bindings" \
  || gap "reconcile line for 2 bindings not in the log tail"

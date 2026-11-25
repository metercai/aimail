#!/usr/bin/env bash
# S4 — multi-address: multi-address hosts keep two bindings; single-address hosts refuse the second.
#
# Contract v2.3 mapping:
#   * C1  — the second address is refused before any gateway call (code not consumed).
#   * C18 — the refusal names the FIRST unmet layer of §11.2; for a host that cannot wake a
#           different session that layer is `wake` (the SDK emits `failed: "wake"`).
#   * M is not "has two bindings": §11.2 requires anchors + isolation + wake; the wake half is
#     exercised by `s6` (a reply must land back in its own session), so s4 alone never proves M.
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
  # C1 is not merely "one binding survived": the host must REFUSE the second address in a
  # typed way (so the one-time code is demonstrably not consumed). A run where the second
  # bind was never attempted cannot prove C1 — treat it as RED, because it would equally
  # hide a host that silently shadows the first address.
  if [ "${maxb:-0}" -gt 1 ]; then
    red "second binding written on $host (max=$maxb) — C1 violated"
  # Accept the raw codes OR the natural-language paraphrase: the tool result reaches the
  # log through the model, which routinely restates it ("already bound … only supports one
  # address"). What must NOT be accepted is a successful second bind — that is the silent
  # shadowing this case exists to catch.
  # C18 wants the layer: `failed=<layer>` or `failed[0]=…` in the structured result. The log is
  # written by the model, which may restate the answer instead of quoting it — so a paraphrase is
  # accepted, but reported as the weaker evidence it is (never silently counted as C18).
  elif grep -qaE 'failed[^a-zA-Z]{0,8}(wake|L3)' "$out"/*.log 2>/dev/null; then
    pass "second address rejected with the failed layer (C1 + C18: failed=wake), code not consumed ($host)"
  elif grep -qaiE "SINGLE_ADDRESS_HOST|second-address|only supports one address|already bound" "$out"/*.log 2>/dev/null; then
    pass "second address rejected (typed refusal, code not consumed) — C18 layer not visible in the model-written log (paraphrased)"
  else
    red "no typed second-address refusal in the evidence — the host either shadowed the first address silently or never attempted the bind (C1 unproven)"
  fi
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

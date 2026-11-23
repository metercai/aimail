#!/usr/bin/env bash
# S3 — delivery. Troubleshooting ladder (owner rule): internal-host sender first,
# then whitelist, then the inbound ledger (emails), then the pull queue
# (pending_deliveries). SSE-V2 does not change the inbound flow; it only changes the
# final push/pull transport, so an empty ledger means inbound never completed.
set -uo pipefail
CASE="S3 delivery"
out="$1"

# The probe is sent by the host adapter asynchronously; on openclaw it can land AFTER the
# journey returns (measured 2026-11-23: inject.log with `emails=0` appeared post-hoc while
# the case had already reported GAP). Wait for the evidence, then judge — do not GAP early.
for _ in $(seq 1 90); do
  [ -f "$out/inject.log" ] && grep -qa "emails=" "$out/inject.log" && break
  sleep 2
done
[ -f "$out/inject.log" ] || { gap "no injection evidence after 180s — journey never reached the mail stage"; return 0; }
grep -qa "emails=" "$out/inject.log" || { gap "injection started but the ledger probe never finished (see $out/inject.log)"; return 0; }

if grep -qa "whitelist failed" "$out/inject.log"; then
  red "whitelist entry missing: $(grep -a 'whitelist failed' "$out/inject.log" | head -1)"
else
  pass "sender whitelisted before inbound"
fi

emails="$(sed -n 's/.*emails=\([0-9]*\).*/\1/p' "$out/inject.log" | tail -1)"
if [ -z "$emails" ]; then
  gap "inbound ledger not captured"
elif [ "$emails" -ge 1 ]; then
  pass "inbound stored (emails=$emails) — the inbound flow completed"
else
  red "inbound never stored (emails=0) — the inbound flow did not complete (not a pull/SSE issue)"
  return 0
fi

if grep -qa "acked_after_poll=" "$out/inject.log"; then
  pass "queued then acked (host accepted the mail)"
else
  red "$(grep -a 'observed' "$out/inject.log" | head -1)"
fi

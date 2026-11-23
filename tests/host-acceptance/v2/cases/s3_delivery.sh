#!/usr/bin/env bash
# S3 — delivery. Troubleshooting ladder (owner rule): internal-host sender first,
# then whitelist, then the inbound ledger (emails), then the pull queue
# (pending_deliveries). SSE-V2 does not change the inbound flow; it only changes the
# final push/pull transport, so an empty ledger means inbound never completed.
set -uo pipefail
CASE="S3 delivery"
out="$1"

[ -f "$out/inject.log" ] || { gap "no injection evidence — journey never reached the mail stage"; return 0; }

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

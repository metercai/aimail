#!/usr/bin/env bash
# S3 — delivery: the host side injects a whitelisted inbound mail and watches the
# gateway queue transition (row appears -> row disappears = acked after accept).
set -uo pipefail
CASE="S3 delivery"
out="$1"

if [ ! -f "$out/inject.log" ]; then
  gap "no injection evidence — the journey never reached the mail stage"
  return 0
fi
if grep -qa "acked_after_poll=" "$out/inject.log"; then
  pass "inbound queued then acked (host accepted the mail)"
elif grep -qa "observed max=0" "$out/inject.log"; then
  red "inbound never queued (gateway refused it): $(grep -a 'observed' "$out/inject.log" | head -1)"
else
  red "inbound queued but not acked: $(grep -a 'observed' "$out/inject.log" | head -1)"
fi
if grep -qa "sent" "$out/inject.log"; then pass "smtp injection accepted by the gateway"; else gap "smtp inject did not run"; fi

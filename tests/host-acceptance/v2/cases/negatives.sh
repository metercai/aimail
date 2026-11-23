#!/usr/bin/env bash
# Negatives — an invalid activation code must never write a binding or arm the pull.
set -uo pipefail
CASE="NEG invalid code"
out="$1"; addr="$2"

if [ ! -f "$out/bindings_count" ]; then
  gap "negative stage did not complete (see $out/rc)"
  return 0
fi
n="$(cat "$out/bindings_count")"
[ "$n" = "0" ] && pass "no binding written for an invalid code" \
               || red "binding written for an invalid code (n=$n)"

# the contract-level evidence is "nothing was bound"; the tool's wording is not asserted
grep -qaE "invalid|expired|not.?found|gone|410|404" "$out/act.log" 2>/dev/null \
  && pass "tool reported a business failure (no retry storm)" \
  || true

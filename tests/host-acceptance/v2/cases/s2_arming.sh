#!/usr/bin/env bash
# S2 — arming: after the host restarts with a V2 binding, the aggregate pull must arm.
# The boot that precedes the bind legitimately logs "no agent-scope binding", so the
# assertion is ordering-based: the arming line must come after the last such complaint.
set -uo pipefail
CASE="S2 arming"
out="$1"
log="$out/host.log"
[ -f "$log" ] || { gap "no host log captured"; return 0; }

assert_grep "polling enabled|pull scope" "$log" "pull armed"
if awk '/no agent-scope binding/{last=NR} /polling enabled|pull scope/{arm=NR} END{exit !(arm>last)}' "$log"; then
  pass "armed after the last missing-binding boot"
else
  red "arming line precedes/absent after the last 'no agent-scope binding'"
fi

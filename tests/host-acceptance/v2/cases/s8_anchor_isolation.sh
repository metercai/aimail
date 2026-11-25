#!/usr/bin/env bash
# s8 — contract C16 host side: anchor isolation (§9.2 I2).
#
# Rule: no delivery may cross anchors. When the session recorded for this mail/thread belongs to a
# DIFFERENT anchor than the one the address is bound to now, E1/E2 must be abandoned, the mail must
# be re-ranked INSIDE the current anchor, and the abandonment must be recorded (`anchor-moved`) —
# a silent re-rank is indistinguishable from "there never was a direct reply".
#
# Evidence file (host hook `host_s8_anchor_isolation`, optional):
#   /out/s8_anchor.json = { "acked": true, "moved": true, "edge": "thread",
#                           "moved_from": "<foreign>", "rewritten": 1,
#                           "foreign_session": "<key>", "used": "<key>", "leaks": false }
#   * rewritten      — how many thread pointers were pointed at the foreign anchor; 0 = the scenario
#                      was never set up, so the case is a GAP (never a pass)
#   * moved/edge     — the product's own marker on the dispatch line
#   * foreign_session / landed — where the mail actually went
#   * leaks         — the mail landed in the OTHER anchor's session: the exact defect C16 forbids
set -uo pipefail

_dir="${1:-}"
[ -n "$_dir" ] || { echo "usage: s8_anchor_isolation.sh <journey-dir>"; return 2 2>/dev/null || exit 2; }
_f="$_dir/s8_anchor.json"
CASE="S8 anchor isolation"   # never inherit the previous label

# A host with no allocatable session has no anchor to isolate (pi: session identity belongs to the
# CLI). Same capability bit the C8 allocation half uses — see contract §16 C8 证明范围 + 附 E.
if [ "${HOST_CAN_ALLOCATE_SESSION:-1}" = "0" ]; then
  na_scope "S8 anchor isolation — host cannot allocate sessions (contract §11 创建/§16 C8 证明范围): no second anchor exists to cross"
  return 0 2>/dev/null || exit 0
fi

if [ ! -f "$_f" ]; then
  gap "S8 anchor isolation — no s8_anchor.json: host hook 'host_s8_anchor_isolation' not implemented, C16 unproven"
  return 0 2>/dev/null || exit 0
fi

# Pipe-delimited: a space-separated line collapses EMPTY fields and then the "used" comparison
# silently compares the wrong pair (s7 hit exactly this).
IFS='|' read -r _s8ok _s8moved _s8edge _s8from _s8rw _s8foreign _s8used _s8leak _s8err < <(python3 - "$_f" <<'PYJ'
import json, sys
d = json.load(open(sys.argv[1]))
vals = ["1" if d.get("acked") else "0",
        "1" if d.get("moved") else "0",
        d.get("edge") or "-",
        d.get("moved_from") or "-",
        str(d.get("rewritten", 0)),
        d.get("foreign_session") or "-",
        d.get("used") or "-",
        "1" if d.get("leaks") else "0",
        (d.get("error") or "-")]
print("|".join(str(v).replace("|", "/").replace("\n", " ") for v in vals))
PYJ
) || { gap "S8 anchor isolation — s8_anchor.json unparsable"; return 0 2>/dev/null || exit 0; }

if [ "$_s8rw" = "0" ] || [ "$_s8rw" = "None" ]; then
  gap "S8 anchor isolation — no thread pointer was pointed at a foreign anchor (${_s8err}); the scenario was never set up, C16 unproven"
  return 0 2>/dev/null || exit 0
fi

[ "$_s8ok" = "1" ] && pass "S8 anchor isolation — the reply was acked after re-ranking (not left pending)" \
                   || red "S8 anchor isolation — the reply was NOT accepted; C16 wants a re-rank, not an endless pending (${_s8err})"

[ "$_s8moved" = "1" ] && pass "S8 anchor isolation — the abandonment is recorded as 'anchor-moved' (edge=${_s8edge}, from=${_s8from})" \
                      || red "S8 anchor isolation — no 'anchor-moved' marker on the dispatch line: a silent re-rank is unobservable to ops"

case "$_s8edge" in
  direct|thread) pass "S8 anchor isolation — the marker names which edge was abandoned (${_s8edge})" ;;
  *) red "S8 anchor isolation — marker does not name the abandoned edge (edge='${_s8edge}')" ;;
esac

# The one assertion that detects the defect: the OTHER anchor's session must not have received it.
if [ "$_s8leak" = "1" ]; then
  red "S8 anchor isolation — the mail landed in the OTHER anchor's session (${_s8foreign}); I2 violated"
elif [ -n "$_s8used" ] && [ "$_s8used" != "-" ] && [ "$_s8used" != "$_s8foreign" ]; then
  pass "S8 anchor isolation — delivery went to a session of the current anchor (${_s8foreign} -> ${_s8used})"
else
  red "S8 anchor isolation — nothing landed (used='${_s8used}'); the mail was acked and dropped, or not written at all"
fi

return 0 2>/dev/null || exit 0

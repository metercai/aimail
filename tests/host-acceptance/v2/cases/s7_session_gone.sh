#!/usr/bin/env bash
# s7 — contract C17 host side: the session-gone downgrade (§9.3).
#
# Rule: when E1/E2 resolve to a session that no longer exists (deleted by the user / archived), the
# host MUST NOT leave the mail pending forever. It re-attaches the thread pointer to a NEW session in
# the same anchor, delivers, acks, and records the downgrade as `session-gone`.
#
# Evidence file (host hook `host_s7_session_gone`, optional):
#   /out/s7_gone.json = { "acked": true, "deleted": "<key>", "used": "<key>",
#                         "reason": "…session-gone…", "root_ok": true }
#   * acked    — the reply was accepted after the downgrade (not left pending)
#   * deleted  — the session key that was removed
#   * used     — the session key the reply actually landed in
#   * reason   — the recorded reason; MUST name session-gone so ops can see the redirect
#   * root_ok  — the thread root survived the re-attach (E2 lineage preserved)
#
# A host without the hook reports GAP: the obligation is tracked in GATE.md and never silently green.
set -uo pipefail

_dir="${1:-}"
[ -n "$_dir" ] || { echo "usage: s7_session_gone.sh <journey-dir>"; return 2 2>/dev/null || exit 2; }
_f="$_dir/s7_gone.json"
CASE="S7 session-gone"   # never inherit the previous label

if [ ! -f "$_f" ]; then
  gap "S7 session-gone — no s7_gone.json: host hook 'host_s7_session_gone' not implemented, C17 unproven"
  return 0 2>/dev/null || exit 0
fi

# Pipe-delimited on purpose: a space-separated line collapses EMPTY fields, so `used: ""` silently
# shifted into the next variable and the "used a new session" check compared the wrong pair. That is
# exactly the parsing pitfall GATE.md warns about; here it was hiding a dropped mail.
IFS='|' read -r _gok _gdel _guse _greason _groot < <(python3 - "$_f" <<'PYJ'
import json,sys
d=json.load(open(sys.argv[1]))
vals=["1" if d.get("acked") else "0", d.get("deleted","-"), d.get("used","-"),
      d.get("reason","-"), "1" if d.get("root_ok") else "0"]
print("|".join(str(v).replace("|", "/").replace("\n", " ") for v in vals))
PYJ
) || { gap "S7 session-gone — s7_gone.json unparsable"; return 0 2>/dev/null || exit 0; }

[ "$_gok" = "1" ] && pass "S7 session-gone — reply was acked (not left pending)" \
                   || red "S7 session-gone — reply was NOT accepted after the downgrade; C17 wants a redirect, not an endless pending"
# An EMPTY `used` must not pass: the first version of this check compared "" against the deleted key,
# found them different, and reported "used a new session" while nothing had been written at all — a
# false green on the one assertion that detects message loss.
if [ -n "$_guse" ] && [ "$_guse" != "-" ] && [ "$_guse" != "$_gdel" ]; then
  pass "S7 session-gone — delivery used a new session ($_gdel -> $_guse)"
elif [ -z "$_guse" ] || [ "$_guse" = "-" ]; then
  red "S7 session-gone — no session received the reply (used=''); the mail was acked and dropped"
else
  red "S7 session-gone — delivery reused the deleted session key ($_guse); C17 redirect not performed"
fi
case "$_greason" in
  *session-gone*) pass "S7 session-gone — the downgrade is recorded as 'session-gone'" ;;
  *) red "S7 session-gone — downgrade reason '$_greason' does not name session-gone; the redirect is unobservable to ops" ;;
esac
[ "$_groot" = "1" ] && pass "S7 session-gone — the thread root survived the re-attach" \
                     || red "S7 session-gone — thread root changed on re-attach; E2 lineage lost"

return 0 2>/dev/null || exit 0

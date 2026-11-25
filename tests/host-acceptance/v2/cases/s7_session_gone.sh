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

# Contract §16 C17 scopes the *proof* to hosts that can tell whether a session still exists
# (附 E `投递确认: probe`). An `admission-only` host's dispatch surface only says "accepted", so it
# can never report "the target was gone": C17 is N/A there, not a failure. The host adapter declares
# its capability; anything unknown is a fixture GAP (never a silent pass).
# Three values, because "we cannot prove it" and "it does not apply" are different claims:
#   probe            — the host can be asked whether a session exists ⇒ judge normally;
#   unconfirmed      — no confirmation surface. C17 is STILL judged: a measured ack-with-nothing-
#                      written is a drop, and "we cannot see it" must not become "we do not look"
#                      (that is exactly how a measured loss would be laundered into N/A);
#   no-session-model — the host has no session the inbound path could allocate or re-attach
#                      (session identity belongs to the CLI) ⇒ the obligation is vacuous.
case "${HOST_DELIVERY_CONFIRMATION:-probe}" in
  no-session-model)
    na_scope "S7 session-gone — host has no allocatable session model (contract §16 C17 适用范围): the obligation is vacuous"
    return 0 2>/dev/null || exit 0
    ;;
  probe|unconfirmed|"") : ;;
  *)
    gap "S7 session-gone — unknown HOST_DELIVERY_CONFIRMATION='${HOST_DELIVERY_CONFIRMATION}' (expected probe|unconfirmed|no-session-model)"
    return 0 2>/dev/null || exit 0
    ;;
esac

# Owner ruling (2026-11-25): hosts without a public landing-confirmation API will not get one from
# the host side, so "ack without landing" is an ACCEPTED RISK for those hosts. It must be signed
# (HOST_RISK_ACCEPTED carries the reference) and it is printed with whatever evidence exists — the
# check is not deleted, and the row is neither green nor red.
if [ "${HOST_DELIVERY_CONFIRMATION:-probe}" = "unconfirmed" ] && [ -n "${HOST_RISK_ACCEPTED:-}" ]; then
  _detail="signed: ${HOST_RISK_ACCEPTED}"
  if [ -f "$_f" ]; then
    _detail="$_detail; measured: $(python3 -c '
import json,sys
d=json.load(open(sys.argv[1]))
print("acked=%s used=%r reason=%r" % (d.get("acked"), d.get("used"), d.get("reason")))
' "$_f" 2>/dev/null || echo "s7_gone.json unreadable")"
  else
    _detail="$_detail; measured: no s7_gone.json (host hook absent)"
  fi
  risk_accepted "S7 session-gone — no public landing API; owner accepted 'acked but possibly not landed' ($_detail)"
  return 0 2>/dev/null || exit 0
fi

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

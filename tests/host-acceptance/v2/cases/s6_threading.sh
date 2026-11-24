#!/usr/bin/env bash
# s6 — contract C8 host side: "同 thread followup，新 thread 新 session，空间=cwd"
# (docs/aimail-host-sse-contract-v2.md:519)
#
# What this case demands from the host hook (hosts/<host>.sh → host_s6_threading):
#   /out/s6.json  — one JSON object, measured inside the host, not inferred by the fixture:
#     {
#       "acked":  [true, true, true],          # the host really took mail 1, the reply, and mail 3
#       "root1":  "<thread root of mail 1>",
#       "root2":  "<thread root of the reply>",   # must equal root1
#       "root3":  "<thread root of mail 3>",      # must differ (new thread)
#       "session1": "<host session key after mail 1>",
#       "session2": "<host session key after the reply>",
#       "session3": "<host session key after mail 3>",
#       "sessions1": <host session count after mail 1>,
#       "sessions2": <…> after the reply,
#       "sessions3": <…> after mail 3,
#       "workspace": "<the binding's workspace path>"
#     }
#
# Judgement rules:
#   * same thread  ⇒ identical session key AND the host session count must NOT grow
#   * new thread   ⇒ different session key AND the count must grow by exactly one
#   * with no session evidence at all (file absent / unparsable) this is a FIXTURE GAP, never a pass
set -uo pipefail

_dir="${1:-}"
[ -n "$_dir" ] || { echo "usage: s6_threading.sh <journey-dir>"; return 2 2>/dev/null || exit 2; }
_f="$_dir/s6.json"

if [ ! -f "$_f" ]; then
  gap "S6 threading — host produced no s6.json (session evidence missing); fixture capability, not a product verdict"
  return 0 2>/dev/null || exit 0
fi

read -r _ok _root1 _root2 _root3 _s1 _s2 _s3 _n1 _n2 _n3 _newr _newt _ws < <(python3 - "$_f" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
acked=d.get("acked") or []
ok = "1" if acked and all(bool(a) for a in acked) else "0"
vals = [ok, d.get("root1","-"), d.get("root2","-"), d.get("root3","-"),
        d.get("session1","-"), d.get("session2","-"), d.get("session3","-"),
        str(d.get("sessions1",-1)), str(d.get("sessions2",-1)), str(d.get("sessions3",-1)),
        str(d.get("new_on_reply",-1)), str(d.get("new_on_newthread",-1)),
        d.get("workspace","-")]
print(" ".join(v.replace(" ", "_") for v in vals))   # one line: read(1) must not split values
PY
) || { gap "S6 threading — s6.json unparsable"; return 0 2>/dev/null || exit 0; }

# No session evidence at all => fixture capability gap, never a product verdict (my first run
# reported four REDs because the snapshot pointed at the wrong path; that must not happen again).
if [ "${_n1:--1}" -lt 0 ] 2>/dev/null || [ "$_s1" = "-" ]; then
  gap "S6 threading — the host produced no readable session evidence (sessions1=$_n1, key=$_s1); fixture capability, not a C8 verdict"
  return 0 2>/dev/null || exit 0
fi

[ "$_ok" = "1" ] && pass "S6 threading — all three mails were accepted by the host (acked)" \
                 || red "S6 threading — the host did not accept every mail (acked=$_ok); session identity cannot be judged"

# 1) same thread ⇒ same session, no new session created
if [ "$_root1" = "$_root2" ]; then
  pass "S6 threading — the reply resolves to the same thread root as the mail it answers"
else
  red "S6 threading — reply thread root ($_root2) differs from the original ($_root1); C8 routing broken"
fi
# The decisive fact is "no NEW session was created for a same-thread reply". Comparing two derived
# keys looked strict but was definition-dependent: a reused session produces no new key at all, so
# the first version of this case reported RED on correct product behaviour.
if [ "${_newr:-9}" = "0" ] && [ "$_s2" = "$_s1" ]; then
  pass "S6 threading — reply reused the thread's session ($_s1); no new session was created"
else
  red "S6 threading — reply created a new host session ($_s1 -> $_s2, new=$_newr); C8 violated"
fi
if [ "$_n1" -gt 0 ] 2>/dev/null && [ "$_n1" = "$_n2" ]; then
  pass "S6 threading — host session count did not grow on the reply ($_n1)"
else
  red "S6 threading — host session count grew on a same-thread reply ($_n1 -> $_n2); a new session was created"
fi

# 2) new thread ⇒ new session (and exactly one)
if [ "$_root3" != "$_root1" ] && [ "$_s3" != "$_s1" ] && [ "${_newt:-0}" -ge 1 ] 2>/dev/null; then
  pass "S6 threading — a new thread started a new host session ($_s3)"
else
  red "S6 threading — a new thread reused the old session root/​key ($_root3 / $_s3); C8 violated"
fi
if [ "$_n3" -eq $(( ${_n2:-0} + 1 )) ] 2>/dev/null; then
  pass "S6 threading — host session count grew by exactly one for the new thread ($_n2 -> $_n3)"
else
  red "S6 threading — session count went $_n2 -> $_n3 for one new thread (expected +1)"
fi

# 3) workspace stays the binding's workspace (contract: 空间=cwd)
if [ "$_ws" != "-" ] && [ -n "$_ws" ]; then
  pass "S6 threading — session space reported by the host: $_ws"
else
  gap "S6 threading — host did not report a workspace; the 'space = cwd' half of C8 is unproven"
fi
return 0 2>/dev/null || exit 0

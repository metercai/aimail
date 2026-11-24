#!/usr/bin/env bash
# Hermes host adapter (pysdk / SSE-V2). ONE container, driven in TWO phases.
#
#   host_journey <out> <addr> <code> <token>
#     phase 1 : install dev wheel -> prepare profile -> activation/lifecycle turns
#     host    : whitelist + inject the delivery probe  (host is DOWN here on purpose)
#     phase 2 : host patch -> boot the messaging gateway -> stream attaches and drains
#
# Why the split: a mail queued while the host is down is the honest shape of "restart, then
# drain" (contract §8.3). Injecting after the stream attached raced with the gateway's
# push-on-insert and produced "armed but never delivered" (measured 2026-11-24).
#
# Measured host facts encoded here (Hermes 0.23.x, aimail-host-hermes:latest):
#   * plugin code runs in the host PROJECT VENV (/opt/hermes/.venv), not the launcher
#     python — installing into only one makes registration fail ("No module named aimail");
#     `hermes plugins doctor <dir>` is the self-check (registrations: N tool(s)).
#   * discovery on this version is DIRECTORY based (<home>/plugins/<name>/).
#   * the `-z` turn runs through a privilege-drop shim that rebuilds the environment:
#     AIMAIL_HOME is dropped, so the agent writes under its own HOME (/opt/data/.aimail).
#     The gateway must be pointed at that same directory.
#   * no host source patch is needed: the host discovers the pip-installed plugin through
#     its entry point and resolves the plugin platform name when it parses config
#     (measured 2026-11-24 on the pinned 0.23.2 host: unpatched, pip-only =>
#     "Connecting to aimail... / aimail connected / Gateway running with 1 platform(s)").
#   * tool handlers are called positionally and must return TEXT (see hermes_plugin/tools).
#   * BasePlatformAdapter.__init__(config, platform) / _mark_connected(*, listener_base).
set -uo pipefail
HOST_NAME=hermes
HOST_ADDRESS_MODEL=single          # one profile = one address (contract §11.2)
HOST_FRAME_EVIDENCE=0              # no dsh-style event frames; judge on journey_state
HERMES_IMAGE="${HERMES_IMAGE:-aimail-host-hermes:latest}"
V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_hermes_bindings_count() {  # count from the evidence the container left behind
  cat "$1/bindings_count" 2>/dev/null || echo 0
}

host_journey() {
  local out="$1" addr="$2" code="$3" token="$4"
  mkdir -p "$out"
  local whl name
  whl="$(basename "${SDK_FILE:?SDK_FILE unset — build the wheel first}")"
  name="l2hermes-$$"

  docker run -d --rm --network host --name "$name" \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "AC=$code" -e "GWURL=${GW_URL}" \
    -e "AH2=${S4_ADDR:-}" -e "AC2=${S4_CODE:-}" -e "LIFECYCLE=${LIFECYCLE:-0}" \
    -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" \
    -v "${GW_WORK}:/gw:ro" -v "$out:/out" \
    --entrypoint bash "$HERMES_IMAGE" -lc 'sleep infinity' >/dev/null \
    || { echo "container did not start" >"$out/rc"; return 0; }

  # ── phase 1: install + profile + activation turns (no gateway yet) ─────────────────
  docker exec "$name" bash -lc '
    set -u
    WHL="'"$whl"'"
    PY="$(ls -d /opt/hermes/tools/python-*/bin/python3 | head -1)"
    echo "interpreter=$PY" > /out/interpreter
    VPY=/opt/hermes/.venv/bin/python
    UV="$(ls -d /opt/hermes/tools/uv-*/uv 2>/dev/null | head -1)"
    if [ -x "$VPY" ] && [ -n "$UV" ]; then
      "$UV" pip install --python "$VPY" "/opt/sdk/$WHL" >/tmp/pip-venv.log 2>&1 || true
    fi
    "$PY" -m pip install -q --no-input --no-cache-dir --break-system-packages "/opt/sdk/$WHL" 2>/tmp/pip.log || {
      echo "wheel install failed"; tail -5 /tmp/pip.log; exit 3; }

    # Install story under test = what the wheel publicly promises: pip install + the host
    # own plugin entry point (hermes_agent.plugins). NOTHING is copied into the host
    # plugins dir any more - a directory plugin tests a mechanism users do not use, and it
    # made a host patch look necessary. Measured 2026-11-24 on the pinned host, pip-only
    # and unpatched: "Connecting to aimail... / aimail connected / Gateway running with
    # 1 platform(s)". The patch was compensating for a fixture-only discovery path.
    H=/opt/l2home; rm -rf "$H"; mkdir -p "$H"
    cp /opt/data/.hermes/config.yaml "$H/config.yaml"
    printf "plugins:\\n  enabled:\\n  - aimail\\nplatforms:\\n  aimail:\\n    enabled: true\\n" >> "$H/config.yaml"
    chown -R 10000:10000 "$H"
    export HERMES_HOME="$H" AIMAIL_HOME=/opt/data/.aimail HERMES_PROFILE_HOME="$H"

    # Two independent facts, both from the host itself:
    #   plugin_doctor  → the manifest validates and register() produced N tool registrations
    #   plugin_list    → the host discovered the package through the pip entry point
    PLUGDIR="$("$PY" -c "import aimail.hermes_plugin,os;print(os.path.dirname(aimail.hermes_plugin.__file__))")"
    { /opt/hermes/bin/hermes plugins doctor "$PLUGDIR" 2>&1 | tail -4; } > /out/plugin_doctor 2>&1
    { /opt/hermes/bin/hermes plugins list --user --plain 2>&1 | grep -iE "aimail" | head -3; } > /out/plugin_list 2>&1
    echo "mail tools: $(grep -o "registrations: [0-9][0-9]*" /out/plugin_doctor | head -1 | tr -dc "0-9")" >> /out/host.log
    echo "plugin source: $(grep -oE "entrypoint|directory|git" /out/plugin_list | head -1)" >> /out/host.log

    step() { echo "$1" > /out/step; }
    turn() { timeout 420 /opt/hermes/bin/hermes --yolo -z "$2" >"/out/turn_$1.log" 2>&1 || echo "turn_timeout_$1=1" >> "/out/turn_$1.log"; }
    bindings() {
      ls "$H"/.aimail/systems/*/*/agentmail.json 2>/dev/null  # contract-allowed: shell glob inside the host container; the canonical value is asserted by run-matrix via BINDING_FILE derived from contract/aimail-contract.json
      ls /opt/data/.aimail/systems/*/*/agentmail.json 2>/dev/null  # contract-allowed: shell glob inside the host container; the canonical value is asserted by run-matrix via BINDING_FILE derived from contract/aimail-contract.json
    }
    wait_binding() { local i=0; while [ $i -lt "$1" ]; do bindings | head -1 | grep -q . && return 0; i=$((i+1)); sleep 2; done; return 1; }

    step s1_bind
    turn bind "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"$AC\",\"email\":\"$AH\",\"gateway_url\":\"$GWURL\"}. Call it exactly once, do not retry, do not ask questions; then report the tool result verbatim and stop."
    wait_binding 90 || true
    B="$(bindings | head -1)"
    echo "binding=${B:-none}" > /out/binding_path
    if [ -n "$B" ]; then
      stat -c %a "$B" > /out/mode 2>/dev/null || echo none > /out/mode
      install -m 644 "$B" /out/binding.json
    else
      echo none > /out/mode
    fi
    bindings | wc -l > /out/bindings_count

    if [ -n "${AH2:-}" ] && [ -n "${AC2:-}" ]; then
      step s4_bind2
      turn bind2 "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"$AC2\",\"email\":\"$AH2\",\"gateway_url\":\"$GWURL\"}. Call it exactly once, do not retry, do not ask questions; then report the tool result verbatim and stop."
      sleep 5
      bindings | wc -l > /out/bindings_count_after2
    fi

    if [ "${LIFECYCLE:-0}" = "1" ]; then
      step s5_pause
      turn pause "Call the aimail_address tool now with exactly this JSON: {\"action\":\"pause\",\"email\":\"$AH\"}. Call it exactly once, do not retry; report the result verbatim."
      B="$(bindings | head -1)"
      if [ -n "$B" ]; then "$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get(\"paused\"))" "$B" > /out/paused 2>/dev/null || echo None > /out/paused; else echo None > /out/paused; fi
      step s5_unbind
      turn unbind "Call the aimail_address tool now with exactly this JSON: {\"action\":\"unbind\",\"email\":\"$AH\"}. Call it exactly once, do not retry; report the result verbatim."
      sleep 5
      bindings | wc -l > /out/bindings_count_final
    fi
    echo phase1_done > /out/phase1
  ' >"$out/phase1.log" 2>&1 || true

  # ── host: whitelist + inject while the host is DOWN (no stream attached yet) ──────
  if [ "$(cat "$out/binding_path" 2>/dev/null || echo 'binding=none')" != "binding=none" ] \
     && [ "${LIFECYCLE:-0}" != "1" ]; then
    python3 "$V2_ROOT/lib/gw.py" wl --gateway "$GW_URL" --key "$GW_ADMIN" --identity "$GW_SID" \
      --system "$GW_SID" --addr "$addr" --sender ext-sender@example.com >"$out/whitelist.json" 2>&1 || true
    grep -q '"ok": true' "$out/whitelist.json" || echo "whitelist failed: $(cat "$out/whitelist.json")" >>"$out/inject.log"
    python3 - "$addr" "$token" "$((GW_SMTP + 1))" <<'PYINJ' >>"$out/inject.log" 2>&1
import smtplib, sys
from email.message import EmailMessage
addr, token, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
m = EmailMessage(); m["From"] = "ext-sender@example.com"; m["To"] = addr
m["Subject"] = "L2 delivery probe"; m["Message-ID"] = "<l2-hermes-s3@example.com>"
m.set_content("delivery probe token " + token)
s = smtplib.SMTP("127.0.0.1", port, timeout=20); s.send_message(m); s.quit(); print("sent_before_boot")
PYINJ
  fi

  # ── phase 2: host patch + gateway boot; the stream attaches and must drain ────────
  docker exec "$name" bash -lc '
    set -u
    H=/opt/l2home
    export HERMES_HOME="$H" AIMAIL_HOME=/opt/data/.aimail HERMES_PROFILE_HOME="$H"
    # No host patch: the host discovers the pip-installed plugin itself and resolves the
    # plugin platform name when it parses config (verified 2026-11-24, unpatched host).
    : > /tmp/gw.log
    nohup /opt/hermes/bin/hermes --yolo gateway run >/tmp/gw.log 2>&1 &
    i=0
    while [ $i -lt 60 ]; do
      grep -qE "SSE reconcile|No messaging platforms enabled" /tmp/gw.log "$H"/logs/agent.log 2>/dev/null && break
      i=$((i+1)); sleep 2
    done
    echo phase2_done > /out/phase2
  ' >"$out/phase2.log" 2>&1 || true

  # ── host: watch the queue drain ───────────────────────────────────────────────────
  if [ -f "$out/phase2" ] && [ "${LIFECYCLE:-0}" != "1" ]; then
    _max=0; _final=0
    for _i in $(seq 1 900); do
      _n="$(gw_pending_rows "$addr")"; _n="${_n:-0}"
      [ "$_n" -gt "$_max" ] 2>/dev/null && _max="$_n"
      _final="$_n"
      if [ "$_max" -gt 0 ] && [ "$_final" = 0 ]; then echo "acked_after_poll=$_i" >>"$out/inject.log"; break; fi
      sleep 0.2
    done
    echo "observed max=$_max final=$_final" >>"$out/inject.log"
    echo "emails=$(gw_sql "SELECT COUNT(*) FROM emails;")" >>"$out/inject.log"
  fi

  # ── phase 3 (optional): contract C8 host side — same thread ⇒ same session ─────────
  # Runs inside the SAME container so the binding survives. Measured: the profile's session store
  # is $HERMES_HOME/sessions/sessions.json (ONE json file holding every session keyed by chat_id).
  # So "session count" must come from parsing that file — counting file paths (my first attempt)
  # measured nothing at all and produced four meaningless REDs.
  case " ${STAGES:-} " in
  *" s6 "*)
    _s6_inject() {  # $1 msg-id  $2 in-reply-to  $3 references
      python3 - "$addr" "$((GW_SMTP + 1))" "$1" "$2" "$3" <<'PY6' >>"$out/inject.log" 2>&1
import smtplib, sys
from email.message import EmailMessage
addr, port, mid, irt, refs = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
m = EmailMessage()
m["From"] = "ext-sender@example.com"; m["To"] = addr
m["Subject"] = "C8 threading probe"
m["Message-ID"] = mid
if irt:  m["In-Reply-To"] = irt
if refs: m["References"] = refs
m.set_content("threading probe " + mid)
s = smtplib.SMTP("127.0.0.1", port, timeout=20); s.send_message(m); s.quit()
print("s6_sent " + mid)
PY6
    }
    _s6_wait_ack() {
      local i n max=0
      for i in $(seq 1 600); do
        n="$(gw_pending_rows "$addr")"; n="${n:-0}"
        [ "$n" -gt "$max" ] 2>/dev/null && max="$n"
        if [ "$max" -gt 0 ] && [ "$n" = 0 ]; then return 0; fi
        sleep 0.2
      done
      return 1
    }
    _s6_sessions() { docker exec "$name" bash -lc 'cat /opt/l2home/sessions/sessions.json 2>/dev/null' 2>/dev/null; }
    _s6_count() {   # number of sessions the HOST has, parsed from its own store
      _s6_sessions | python3 -c '
import json,sys
raw=sys.stdin.read().strip()
if not raw: print(-1); raise SystemExit
try: d=json.loads(raw)
except Exception: print(-1); raise SystemExit
v=d.get("sessions") if isinstance(d,dict) and isinstance(d.get("sessions"),(dict,list)) else d
print(len(v) if hasattr(v,"__len__") else -1)
'
    }
    _s6_settle() {  # wait for the host to materialise the delivery into a session
      local want_below="$1" i c
      for i in $(seq 1 120); do
        c="$(_s6_count)"
        [ -n "$c" ] && [ "$c" -gt "$want_below" ] 2>/dev/null && return 0
        sleep 0.5
      done
      return 1
    }
    _s6_snap() {
      _s6_sessions > "$out/s6_sessions_$1.json" 2>/dev/null || :
      docker exec "$name" bash -lc 'ls -l /opt/l2home/sessions 2>/dev/null' > "$out/s6_ls_$1.txt" 2>/dev/null || :
    }

    _s6_snap 0; C0="$(_s6_count)"
    _s6_inject "<s6-a@example.com>" "" "" >/dev/null 2>&1
    if _s6_wait_ack; then A1=1; else A1=0; fi
    _s6_settle "${C0:--1}" || true
    _s6_snap 1; C1="$(_s6_count)"

    _s6_inject "<s6-reply@example.com>" "<s6-a@example.com>" "<s6-a@example.com>" >/dev/null 2>&1
    if _s6_wait_ack; then A2=1; else A2=0; fi
    sleep 5   # a same-thread reply must NOT need a new session; give the host time to (not) create one
    _s6_snap 2; C2="$(_s6_count)"

    _s6_inject "<s6-b@example.com>" "" "" >/dev/null 2>&1
    if _s6_wait_ack; then A3=1; else A3=0; fi
    _s6_settle "${C2:--1}" || true
    _s6_snap 3; C3="$(_s6_count)"

    python3 - "$out" "$A1" "$A2" "$A3" "$C0" "$C1" "$C2" "$C3" <<'PY6JSON' > "$out/s6.json" 2>"$out/s6.err" || true
import json, os, sys
out, a1, a2, a3, c0, c1, c2, c3 = sys.argv[1:9]
def keys(i):
    p = os.path.join(out, "s6_sessions_%s.json" % i)
    if not os.path.exists(p): return None
    raw = open(p, encoding="utf-8", errors="replace").read().strip()
    if not raw: return None
    try: d = json.loads(raw)
    except Exception: return None
    v = d.get("sessions") if isinstance(d, dict) and isinstance(d.get("sessions"), (dict, list)) else d
    if isinstance(v, dict): return sorted(v.keys())
    if isinstance(v, list):
        ids = []
        for e in v:
            if isinstance(e, dict):
                ids.append(str(e.get("chat_id") or e.get("id") or e.get("session_id") or "?"))
            else: ids.append(str(e))
        return sorted(ids)
    return None
k0, k1, k2, k3 = keys(0), keys(1), keys(2), keys(3)
def new(before, after):
    if before is None or after is None: return []
    return [x for x in after if x not in before]
def key(before, after):
    n = new(before, after)
    return n[0] if n else "-"
new1, new2, new3 = new(k0, k1), new(k1, k2), new(k2, k3)
doc = {
  "acked": [a1 == "1", a2 == "1", a3 == "1"],
  "root1": "s6-a@example.com", "root2": "s6-a@example.com", "root3": "s6-b@example.com",
  # session2 = the session that handled the reply: the newly created one if the host (wrongly)
  # opened a new one, otherwise the thread's own session (i.e. it was reused).
  "session1": new1[0] if new1 else "-",
  "session2": new2[0] if new2 else (new1[0] if new1 else "-"),
  "session3": new3[0] if new3 else "-",
  "new_on_reply": len(new2),
  "new_on_newthread": len(new3),
  "sessions1": int(c1) if c1.lstrip("-").isdigit() else -1,
  "sessions2": int(c2) if c2.lstrip("-").isdigit() else -1,
  "sessions3": int(c3) if c3.lstrip("-").isdigit() else -1,
  "workspace": "",
}
try:
    b = json.load(open(os.path.join(out, "binding.json")))
    doc["workspace"] = b.get("workspace_path") or ""
except Exception: pass
print(json.dumps(doc, indent=2))
PY6JSON
    ;;
  esac

  # collect logs from the still-running container, then drop it
  docker exec "$name" bash -lc 'cp /opt/l2home/logs/agent.log /out/agent.log 2>/dev/null || true; cp /opt/l2home/logs/gateway.log /out/gateway.log 2>/dev/null || true; tail -n 200 /tmp/gw.log >> /out/host.log 2>/dev/null || true; tail -n 300 /out/agent.log >> /out/host.log 2>/dev/null || true; for f in /out/turn_*.log; do [ -f "$f" ] && cat "$f" >> /out/host.log; done; true' >/dev/null 2>&1 || true
  docker rm -f "$name" >/dev/null 2>&1 || true

  if grep -q "SSE reconcile: [1-9]" "$out/agent.log" 2>/dev/null; then
    echo armed > "$out/gateway_state"
  elif grep -q "No messaging platforms enabled" "$out/host.log" 2>/dev/null; then
    echo no_platforms > "$out/gateway_state"
  else
    echo unknown > "$out/gateway_state"
  fi
  local c1 c2 c3 mx
  c1="$(_hermes_bindings_count "$out")"
  c2="$(cat "$out/bindings_count_after2" 2>/dev/null || echo "$c1")"
  c3="$(cat "$out/bindings_count_final" 2>/dev/null || echo "$c2")"
  mx="$c1"; [ "${c2:-0}" -gt "$mx" ] 2>/dev/null && mx="$c2"
  [ "${c3:-0}" -gt "$mx" ] 2>/dev/null && mx="$c3"
  printf 'max_bindings=%s paused_seen=%s final_bindings=%s\n' \
    "$mx" "$(cat "$out/paused" 2>/dev/null || echo False)" "$c3" > "$out/journey_state"
  return 0
}

host_s1_activate() { host_journey "$1" "$2" "$3" "${4:-NONE}"; }
host_s3_delivery() { :; }
host_s6_threading() { :; }   # evidence is produced inside the s1 journey (same container ⇒ binding survives)
host_stop() { docker rm -f "l2hermes-$$" >/dev/null 2>&1 || true; }

host_negative() {
  local jour="$1" addr="$2" out
  # Logs live in their own subdir; `bindings_count` (the name cases/negatives.sh reads)
  # belongs to THIS stage and is copied to the journey dir at the end.
  out="$jour/neg"; mkdir -p "$out"
  docker run --rm --network host --name "l2hermes-neg-$$" \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "GWURL=${GW_URL}" \
    -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" -v "$out:/out" \
    --entrypoint bash "$HERMES_IMAGE" -lc '
      set -u
      PY="$(ls -d /opt/hermes/tools/python-*/bin/python3 | head -1)"
      VPY=/opt/hermes/.venv/bin/python
      UV="$(ls -d /opt/hermes/tools/uv-*/uv 2>/dev/null | head -1)"
      [ -x "$VPY" ] && [ -n "$UV" ] && "$UV" pip install --python "$VPY" /opt/sdk/*.whl >/dev/null 2>&1
      "$PY" -m pip install -q --no-input --no-cache-dir --break-system-packages /opt/sdk/*.whl 2>/dev/null
      H=/opt/l2neg; rm -rf "$H"; mkdir -p "$H/plugins/aimail"
      cp /opt/data/.hermes/config.yaml "$H/config.yaml"
      cp "$("$PY" -c "import aimail.hermes_plugin,os;print(os.path.dirname(aimail.hermes_plugin.__file__))")/plugin.yaml" "$H/plugins/aimail/plugin.yaml"
      printf "from aimail.hermes_plugin import register\n__all__ = [\"register\"]\n" > "$H/plugins/aimail/__init__.py"
      printf "plugins:\n  enabled:\n  - aimail\n" >> "$H/config.yaml"
      chown -R 10000:10000 "$H"
      export HERMES_HOME="$H" AIMAIL_HOME=/opt/data/.aimail
      timeout 420 /opt/hermes/bin/hermes --yolo -z "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"addr-0000-deadbeef\",\"email\":\"$AH\",\"gateway_url\":\"$GWURL\"}. Call it exactly once, do not retry; report the tool result verbatim and stop." > /opt/act.log 2>&1 || true
      sleep 3
      ls "$H"/.aimail/systems/*/*/agentmail.json 2>/dev/null | wc -l > /out/bindings_count  # contract-allowed: shell glob inside the host container; the canonical value is asserted by run-matrix via BINDING_FILE derived from contract/aimail-contract.json
      tail -n 150 /opt/act.log > /out/act.log 2>/dev/null || true
    ' >"$out/rc" 2>&1
  rm -f "$jour/bindings_count" 2>/dev/null || true
  { cp "$out/bindings_count" "$jour/bindings_count"; } 2>/dev/null || echo 0 > "$jour/bindings_count"
  return 0
}

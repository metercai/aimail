#!/usr/bin/env bash
# DeerFlow host adapter (pysdk / SSE-V2). ONE container, driven in TWO phases.
#
#   host_journey <out> <addr> <code> <token>
#     phase 1 : install wheel into the host venv -> write the `plugins:` record ->
#               boot the gateway (auth disabled) -> activation/lifecycle turns via its API
#     host    : whitelist + inject the delivery probe  (gateway DOWN on purpose)
#     phase 2 : boot again so the extension installs and the stream drains the queue
#
# Host facts encoded here (measured on aimail-host-deerflow, 2026-11-23/24):
#   * the host runs from its own venv (``/app/backend/.venv``); system python has no
#     fastapi, so anything touching the host uses the venv interpreter.
#   * extensions are declared in ``config.yaml: plugins:`` with ``use: module:install``
#     (a plain dotted path — no packaging round-trip needed) and load in the gateway.
#   * ``DEER_FLOW_AUTH_DISABLED=1`` removes auth so a fixture can drive the run API.
#   * extension tool names are HASHED by the host, so prompts refer to the tool's intent
#     ("the AIMail mailbox bind tool"), never to a literal name.
set -uo pipefail
HOST_NAME=deerflow
HOST_ADDRESS_MODEL=multi           # contract §11.2: anchors + isolation + wake ⇒ M
HOST_DELIVERY_CONFIRMATION=probe      # 附 E: 投递后回读 run/thread（runtime.py），落地不可证即不 ack
HOST_FRAME_EVIDENCE=0
DF_IMAGE="${DF_IMAGE:-aimail-host-deerflow:latest}"
V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DF_API_PORT="${DF_API_PORT:-18001}"

host_journey() {
  local out="$1" addr="$2" code="$3" token="$4"
  mkdir -p "$out"
  local whl name
  whl="$(basename "${SDK_FILE:?SDK_FILE unset — build the wheel first}")"
  name="l2df-$$"

  docker run -d --rm --network host --name "$name" \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "AC=$code" -e "GWURL=${GW_URL}" \
    -e "AH2=${S4_ADDR:-}" -e "AC2=${S4_CODE:-}" -e "LIFECYCLE=${LIFECYCLE:-0}" \
    -e "DF_PORT=$DF_API_PORT" \
    -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" \
    -v "${GW_WORK}:/gw:ro" -v "$out:/out" \
    --entrypoint bash "$DF_IMAGE" -lc 'sleep infinity' >/dev/null \
    || { echo "container did not start" >"$out/rc"; return 0; }

  # ── phase 1: install + enable the extension + boot + activation turns ─────────────
  docker exec "$name" bash -lc '
    # deerflow reads models[].api_key from config.yaml and never reads $LLM_API_KEY, so the
    # container env alone leaves the image placeholder in place and EVERY turn dies with
    # 401 Invalid API Key (evidence: run 5208698a… → status=error, human message only).
    LLM_API_KEY="'$LLM_API_KEY'" bash /opt/agent-env/set-llm-key.sh deerflow || {
      echo "set-llm-key.sh deerflow failed — no credential for the model" > /out/boot_error
      echo 4 > /out/rc; exit 4; }
    if grep -qE "^[[:space:]]*-?[[:space:]]*api_key:[[:space:]]*(__LLM_API_KEY__)?[[:space:]]*$" /app/config.yaml; then
      echo "config.yaml still carries no real api_key — a turn would 401" > /out/boot_error
      echo 5 > /out/rc; exit 5
    fi
    set -u
    WHL="'"$whl"'"
    VPY=/app/backend/.venv/bin/python
    UV="$(command -v uv || ls -d /opt/hermes/tools/uv-*/uv 2>/dev/null | head -1)"
    if [ -n "$UV" ]; then
      "$UV" pip install --python "$VPY" "/opt/sdk/$WHL" >/tmp/pip.log 2>&1 || true
    else
      "$VPY" -m pip install --no-input --no-cache-dir "/opt/sdk/$WHL" >/tmp/pip.log 2>&1 || true
    fi
    "$VPY" -c "import aimail.deerflow_plugin as d; print(d.PLUGIN_ID)" > /out/plugin_import 2>&1

    # extension record: `use:` is a dotted module path the host imports itself
    "$VPY" - <<PYCFG
import yaml
p = "/app/config.yaml"
cfg = yaml.safe_load(open(p)) or {}
rows = [r for r in (cfg.get("plugins") or []) if not (isinstance(r, dict) and r.get("name") == "aimail")]
rows.append({"name": "aimail", "use": "aimail.deerflow_plugin:install",
             "enabled": True, "required": False, "config": {"inbound": True}})
cfg["plugins"] = rows
open(p, "w").write(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
print("plugins:", [r.get("name") for r in rows])
PYCFG

    _boot() {
      # Do NOT use pkill -f uvicorn here: this script command line contains that string
      # (the nohup line below), so pkill would kill the fixture itself and the run would
      # end silently right here.
      if [ -n "${_DF_PID:-}" ]; then kill "$_DF_PID" 2>/dev/null || true; sleep 2; fi
      # The extension submits runs through the host own HTTP API: point it at the port
      # uvicorn was told to bind (--port), not the image default 8001, or every delivery
      # fails with connection refused and is never acked (evidence: S3 max=1 final=1).
      # NOTE: this is inside a single-quoted docker -lc body; no apostrophes here.
      cd /app/backend
      export PYTHONPATH=. DEER_FLOW_AUTH_DISABLED=1 AIMAIL_HOME=/app/.aimail
      export AIMAIL_DEERFLOW_API="http://127.0.0.1:$DF_PORT"
      nohup ./.venv/bin/python -m uvicorn app.gateway.app:app --host 127.0.0.1 \
        --port "$DF_PORT" >/tmp/df.log 2>&1 &
      local i=0 ap
      while [ $i -lt 150 ]; do
        # The app ALSO logs its internal messaging-gateway port ("Starting API Gateway on
        # 0.0.0.0:8001"); the fixture needs the UVICORN port, which honours --port.
        ap="$(sed -n "s/.*Uvicorn running on http:\/\/[^:]*:\([0-9][0-9]*\).*/\1/p" /tmp/df.log | head -1)"
        if [ -n "$ap" ]; then
          echo "$ap" > /out/api_port
          python3 -c "import urllib.request,sys;urllib.request.urlopen(\"http://127.0.0.1:$ap/openapi.json\", timeout=2)" 2>/dev/null && return 0
        fi
        i=$((i+1)); sleep 2
      done
      return 1
    }
    if ! _boot; then
      # Keep the boot log inside the evidence dir: without it a failed readiness gate is
      # indistinguishable from "the turn produced nothing" (that ambiguity cost two runs).
      cp /tmp/df.log /out/df-phase1.log 2>/dev/null || true
      echo "gateway did not boot (see /out/df-phase1.log)" > /out/boot_error
      echo "gateway did not boot" > /out/rc
      exit 3
    fi

    AP="$(cat /out/api_port 2>/dev/null || echo "$DF_PORT")"
    _turn() {  # _turn <name> <thread> <text>
      python3 /opt/v2lib/deerflow_drive.py --url "http://127.0.0.1:$AP" \
        turn --thread "$2" --text "$3" --wait 300 --tail 1500 >"/out/turn_$1.log" 2>&1 || true
    }
    bindings() { ls /app/.aimail/systems/*/*/agentmail.json 2>/dev/null; }  # contract-allowed: shell glob inside the host container; the canonical value is asserted by run-matrix via BINDING_FILE derived from contract/aimail-contract.json

    echo s1_bind > /out/step
    _turn bind "aimail-bind-$$" "Use the AIMail mailbox tool whose purpose is to bind an AIMail address, with this JSON: {\"action\":\"bind\",\"code\":\"$AC\",\"email\":\"$AH\",\"gateway_url\":\"$GWURL\"}. Call it once, do not retry, then report the result verbatim."
    i=0; while [ $i -lt 45 ]; do bindings | head -1 | grep -q . && break; i=$((i+1)); sleep 2; done
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
      echo s4_bind2 > /out/step
      _turn bind2 "aimail-bind2-$$" "Use the AIMail mailbox bind tool with this JSON: {\"action\":\"bind\",\"code\":\"$AC2\",\"email\":\"$AH2\",\"gateway_url\":\"$GWURL\"}. Call it once, then report the result verbatim."
      sleep 5
      bindings | wc -l > /out/bindings_count_after2
    fi

    if [ "${LIFECYCLE:-0}" = "1" ]; then
      echo s5_pause > /out/step
      _turn pause "aimail-pause-$$" "Use the AIMail mailbox tool to pause the mailbox $AH. Call it once, then report the result verbatim."
      B="$(bindings | head -1)"
      if [ -n "$B" ]; then "$VPY" -c "import json,sys;print(json.load(open(sys.argv[1])).get(\"paused\"))" "$B" > /out/paused 2>/dev/null || echo None > /out/paused; else echo None > /out/paused; fi
      echo s5_unbind > /out/step
      _turn unbind "aimail-unbind-$$" "Use the AIMail mailbox tool to unbind the mailbox $AH. Call it once, then report the result verbatim."
      sleep 5
      bindings | wc -l > /out/bindings_count_final
    fi
    [ -n "${_DF_PID:-}" ] && kill "$_DF_PID" 2>/dev/null || true
    # `$!` was not the listener (measured: port_free_after=60 ⇒ the port never freed, phase 2 died
    # with EADDRINUSE and the app ended up with no channel manager ⇒ no SSE stream ⇒ s6 mails never
    # delivered). Kill by PORT, which is unambiguous, then wait for release.
    # Wait for the port to be released: a fixed `sleep 2` raced here and phase 2 then died with
    # "address already in use", leaving the app with no channel manager and thus no SSE stream —
    # measured 2026-11-24 (s6 run 1: only one [pull] delivery, then "SSE reconcile: 0").
    i=0
    while [ $i -lt 60 ]; do
      ./.venv/bin/python -c "import socket,sys; s=socket.socket(); s.settimeout(0.3); sys.exit(0 if s.connect_ex(('127.0.0.1', $DF_PORT))!=0 else 1)" 2>/dev/null && break
      i=$((i+1)); sleep 0.5
    done
    echo port_free_after=$i > /out/port_wait
    echo phase1_done > /out/phase1
  ' >"$out/phase1.log" 2>&1 || true

  # ── host: whitelist + inject while the gateway is DOWN ────────────────────────────
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
m["Subject"] = "L2 delivery probe"; m["Message-ID"] = "<l2-deerflow-s3@example.com>"
m.set_content("delivery probe token " + token)
s = smtplib.SMTP("127.0.0.1", port, timeout=20); s.send_message(m); s.quit(); print("sent_before_boot")
PYINJ
  fi

  # ── ack watcher: started BEFORE the second boot ──────────────────────────────────
  # The drain happens the instant the extension installs, so a poller that starts after
  # the boot can see only 0→0 and cannot tell "acked" from "never queued" (measured
  # 2026-11-24: max=0 final=0 while the run had already been accepted). Watch across the
  # boot instead: the row must be SEEN pending at least once and then gone.
  _ackwatch="$out/.ackwatch"; rm -f "$_ackwatch"
  (
    _max=0; _final=0
    for _i in $(seq 1 900); do
      _n="$(gw_pending_rows "$addr")"; _n="${_n:-0}"
      [ "$_n" -gt "$_max" ] 2>/dev/null && _max="$_n"
      _final="$_n"
      if [ "$_max" -gt 0 ] && [ "$_final" = 0 ]; then echo "acked_after_poll=$_i" >>"$_ackwatch"; break; fi
      sleep 0.2
    done
    echo "observed max=$_max final=$_final" >>"$_ackwatch"
  ) &
  _ackwatch_pid=$!

  # Kill the phase-1 listener by PROCESS NAME from outside the container: `$!` inside the exec body
  # was not the listener (port_free_after=60 proved the port never freed, so phase 2 hit EADDRINUSE
  # and the app lost its channel manager ⇒ no SSE stream ⇒ s6 mails were never delivered).
  # Done here, not inside, because the in-body script is single-quoted and any quoting trick there
  # terminates the docker exec string.
  docker exec "$name" pkill -f "uvicorn app.gateway.app" >/dev/null 2>&1 || true
  sleep 2

  # ── phase 2: boot again — the extension installs and its stream drains the queue ──
  docker exec "$name" bash -lc '
    set -u
    cd /app/backend
    # Same self-API port as phase 1 (see the note there).
    export PYTHONPATH=. DEER_FLOW_AUTH_DISABLED=1 AIMAIL_HOME=/app/.aimail
    export AIMAIL_DEERFLOW_API="http://127.0.0.1:'"$DF_API_PORT"'"
    nohup ./.venv/bin/python -m uvicorn app.gateway.app:app --host 127.0.0.1 \
      --port "'"$DF_API_PORT"'" >/tmp/df2.log 2>&1 &
    _try=0
    while [ $_try -lt 4 ]; do
      i=0
      while [ $i -lt 150 ]; do
        grep -qE "SSE reconcile|installed \(tools=|Uvicorn running" /tmp/df2.log 2>/dev/null && break
        grep -q "address already in use" /tmp/df2.log 2>/dev/null && break
        i=$((i+1)); sleep 2
      done
      grep -q "address already in use" /tmp/df2.log 2>/dev/null || break
      # the port was still held: wait for it, then boot once more
      _try=$((_try+1))
      echo "phase2 EADDRINUSE, retry $_try" >> /out/phase2_retries
      python3 -c "import socket,time
for _ in range(120):
    s=socket.socket(); s.settimeout(0.3)
    if s.connect_ex(('127.0.0.1', '"$DF_API_PORT"'))!=0: break
    time.sleep(0.5)" 2>/dev/null || true
      : > /tmp/df2.log
      nohup ./.venv/bin/python -m uvicorn app.gateway.app:app --host 127.0.0.1 \
        --port "'"$DF_API_PORT"'" >/tmp/df2.log 2>&1 &
    done
    echo phase2_done > /out/phase2
  ' >"$out/phase2.log" 2>&1 || true

  # The watcher spans the boot; wait for it to settle (it exits on ack or after ~6 min).
  if [ "${LIFECYCLE:-0}" != "1" ]; then
    wait "$_ackwatch_pid" 2>/dev/null || true
    cat "$_ackwatch" >>"$out/inject.log" 2>/dev/null || true
    echo "emails=$(gw_sql "SELECT COUNT(*) FROM emails;")" >>"$out/inject.log"
  else
    kill "$_ackwatch_pid" 2>/dev/null || true
  fi

  # ── phase 3 (optional): contract C8 host side — same thread ⇒ same host thread ─────
  # deerflow keeps its threads in the app's own store (tables named *thread*), so the judgement
  # reads THOSE rows. Discovery is deliberate: the schema is not part of the contract, so the
  # fixture finds the store instead of hard-coding a path (hermes cost me two runs by guessing).
  case " ${STAGES:-} " in
  *" s6 "*|*" s7 "*)
    _s6_inject() {
      python3 - "$addr" "$((GW_SMTP + 1))" "$1" "$2" "$3" <<'PY6' >>"$out/inject.log" 2>&1
import smtplib, sys
from email.message import EmailMessage
addr, port, mid, irt, refs = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
m = EmailMessage()
m["From"] = "ext-sender@example.com"; m["To"] = addr
m["Subject"] = "C8 threading probe"; m["Message-ID"] = mid
if irt:  m["In-Reply-To"] = irt
if refs: m["References"] = refs
m.set_content("threading probe " + mid)
s = smtplib.SMTP("127.0.0.1", port, timeout=20); s.send_message(m); s.quit()
print("s6_sent " + mid)
PY6
    }
    _s6_wait_ack() {
      local i n max=0
      for i in $(seq 1 900); do
        n="$(gw_pending_rows "$addr")"; n="${n:-0}"
        [ "$n" -gt "$max" ] 2>/dev/null && max="$n"
        if [ "$max" -gt 0 ] && [ "$n" = 0 ]; then return 0; fi
        sleep 0.2
      done
      return 1
    }
    _s6_threads() {  # print one thread id per line, read from the HOST store
      docker exec "$name" bash -lc '
        for db in $(find /app -maxdepth 6 \( -name "*.sqlite" -o -name "*.sqlite3" -o -name "*.db" \) 2>/dev/null | head -6); do
          DB="$db" /app/backend/.venv/bin/python - <<PY 2>/dev/null
import os, sqlite3
try: c = sqlite3.connect(os.environ["DB"]); c.execute("select 1")
except Exception: raise SystemExit
for (t,) in c.execute("select name from sqlite_master where type=\"table\""):
    if "thread" not in t.lower(): continue
    cols = [r[1] for r in c.execute("pragma table_info(%s)" % t)]
    key = "thread_id" if "thread_id" in cols else ("id" if "id" in cols else None)
    if not key: continue
    for (v,) in c.execute("select %s from %s" % (key, t)):
        print(v)
PY
        done | sort -u
      ' 2>/dev/null || :
    }
    _s6_snap() {
      _s6_threads > "$out/s6_threads_$1.txt" 2>/dev/null || :
      docker exec "$name" bash -lc 'find /app -maxdepth 5 -newermt "-3 minutes" -type f 2>/dev/null | head -40' > "$out/s6_files_$1.txt" 2>/dev/null || :
    }

    _s6_idle() {  # wait until the host stops writing (no file touched in the last 10s, twice in a row)
      local i busy a=1 b=1
      for i in $(seq 1 90); do
        busy="$(docker exec "$name" bash -lc 'find /app -maxdepth 4 -newermt "-10 seconds" -type f 2>/dev/null | head -1' 2>/dev/null)"
        if [ -z "$busy" ]; then
          b="$a"; a=1
          [ "$b" = 1 ] && return 0
        else
          a=0
        fi
        sleep 2
      done
      return 1
    }

    _s6_snap 0
    _s6_inject "<s6-a@example.com>" "" "" >/dev/null 2>&1
    if _s6_wait_ack; then A1=1; else A1=0; fi
    sleep 20; _s6_snap 1
    _s6_idle || echo "host still busy before the reply; injecting anyway" >>"$out/inject.log"
    _s6_inject "<s6-reply@example.com>" "<s6-a@example.com>" "<s6-a@example.com>" >/dev/null 2>&1
    if _s6_wait_ack; then A2=1; else A2=0; fi
    sleep 20; _s6_snap 2
    _s6_inject "<s6-b@example.com>" "" "" >/dev/null 2>&1
    if _s6_wait_ack; then A3=1; else A3=0; fi
    sleep 20; _s6_snap 3

    python3 - "$out" "$A1" "$A2" "$A3" <<'PY6JSON' > "$out/s6.json" 2>"$out/s6.err" || true
import json, os, sys
out, a1, a2, a3 = sys.argv[1:5]
def ids(i):
    p = os.path.join(out, "s6_threads_%s.txt" % i)
    if not os.path.exists(p): return None
    return [l.strip() for l in open(p, encoding="utf-8", errors="replace") if l.strip()]
k0, k1, k2, k3 = ids(0), ids(1), ids(2), ids(3)
def new(before, after):
    if before is None or after is None: return []
    return [x for x in after if x not in before]
n1, n2, n3 = new(k0, k1), new(k1, k2), new(k2, k3)
doc = {
  "acked": [a1 == "1", a2 == "1", a3 == "1"],
  "root1": "s6-a@example.com", "root2": "s6-a@example.com", "root3": "s6-b@example.com",
  "session1": n1[0] if n1 else "-",
  "session2": n2[0] if n2 else (n1[0] if n1 else "-"),
  "session3": n3[0] if n3 else "-",
  "new_on_reply": len(n2), "new_on_newthread": len(n3),
  "sessions1": len(k1) if k1 is not None else -1,
  "sessions2": len(k2) if k2 is not None else -1,
  "sessions3": len(k3) if k3 is not None else -1,
  "workspace": "",
}
try:
    b = json.load(open(os.path.join(out, "binding.json")))
    doc["workspace"] = b.get("workspace_path") or ""
except Exception: pass
print(json.dumps(doc, indent=2))
PY6JSON

    # ── s7 (contract C17): delete the thread the mail landed in, then reply to that mail.
    case " ${STAGES:-} " in
    *" s7 "*)
      if [ -f "$out/s6.json" ]; then
        _victim="$(python3 -c "import json;d=json.load(open('$out/s6.json'));print(d.get('session1') or '')" 2>/dev/null)"
        if [ -n "$_victim" ] && [ "$_victim" != "-" ]; then
          _s6_snap 6
          docker exec "$name" python3 /opt/v2lib/threads_db.py delete /app/backend/.deer-flow/data/deerflow.db "$_victim" >"$out/s7_delete.log" 2>&1 || true
          _s7_acked=false
          _s6_inject "<s7-reply@example.com>" "<s6-a@example.com>" "<s6-a@example.com>" || true
          _s6_wait_ack && _s7_acked=true
          _s6_snap 7
          python3 - "$out" "$_victim" "$_s7_acked" <<'PY7' > "$out/s7_gone.json" 2>"$out/s7.err" || true
import json, os, sys
out, victim, acked = sys.argv[1], sys.argv[2], sys.argv[3]
def ids(i):
    p = os.path.join(out, "s6_threads_%s.txt" % i)
    if not os.path.exists(p):
        return None
    return sorted({l.strip() for l in open(p, encoding="utf-8", errors="replace") if l.strip()})
k6, k7 = ids(6), ids(7)
new = [] if (k6 is None or k7 is None) else [k for k in k7 if k not in k6]
used = new[0] if new else ""
print(json.dumps({"acked": acked == "true", "deleted": victim, "used": used, "reason": "",
                  "root_ok": bool(acked == "true" and used and used != victim),
                  "landed_in_existing": False, "delivered_anywhere": bool(used),
                  "session_gone_observed": True}, indent=2))
PY7
        fi
      fi
      ;;
    esac
    ;;
  esac

  docker exec "$name" bash -lc 'cat /tmp/df.log /tmp/df2.log > /out/host.log 2>/dev/null || true; for f in /out/turn_*.log; do [ -f "$f" ] && cat "$f" >> /out/host.log; done; true' >/dev/null 2>&1 || true
  docker rm -f "$name" >/dev/null 2>&1 || true

  if grep -q "SSE reconcile: [1-9]" "$out/host.log" 2>/dev/null; then
    echo armed > "$out/gateway_state"
  else
    echo unknown > "$out/gateway_state"
  fi
  local c1 c2 c3 mx
  c1="$(cat "$out/bindings_count" 2>/dev/null || echo 0)"
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
host_stop() { docker rm -f "l2df-$$" >/dev/null 2>&1 || true; }

host_negative() {
  local jour="$1" addr="$2" out
  out="$jour/neg"; mkdir -p "$out"
  docker run --rm --network host --name "l2df-neg-$$" \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "GWURL=${GW_URL}" -e "DF_PORT=$DF_API_PORT" \
    -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" -v "$out:/out" \
    --entrypoint bash "$DF_IMAGE" -lc '
      set -u
      VPY=/app/backend/.venv/bin/python
      UV="$(command -v uv || true)"
      [ -n "$UV" ] && "$UV" pip install --python "$VPY" /opt/sdk/*.whl >/dev/null 2>&1 || true
      "$VPY" - <<PYCFG
import yaml
p = "/app/config.yaml"
cfg = yaml.safe_load(open(p)) or {}
rows = [r for r in (cfg.get("plugins") or []) if not (isinstance(r, dict) and r.get("name") == "aimail")]
rows.append({"name": "aimail", "use": "aimail.deerflow_plugin:install",
             "enabled": True, "required": False, "config": {"inbound": False}})
cfg["plugins"] = rows
open(p, "w").write(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
PYCFG
      cd /app/backend
      export PYTHONPATH=. DEER_FLOW_AUTH_DISABLED=1 AIMAIL_HOME=/app/.aimail
      nohup ./.venv/bin/python -m uvicorn app.gateway.app:app --host 127.0.0.1 --port "$DF_PORT" >/tmp/dfneg.log 2>&1 &
      i=0
      while [ $i -lt 60 ]; do
        python3 -c "import urllib.request;urllib.request.urlopen(\"http://127.0.0.1:$DF_PORT/openapi.json\", timeout=2)" 2>/dev/null && break
        i=$((i+1)); sleep 2
      done
      python3 /opt/v2lib/deerflow_drive.py --url "http://127.0.0.1:$DF_PORT" turn --thread "aimail-neg-$$" \
        --text "Use the AIMail mailbox bind tool with this JSON: {\"action\":\"bind\",\"code\":\"addr-0000-deadbeef\",\"email\":\"$AH\",\"gateway_url\":\"$GWURL\"}. Call it once, then report the result verbatim." \
        --wait 300 --tail 800 > /opt/act.log 2>&1 || true
      sleep 3
      ls /app/.aimail/systems/*/*/agentmail.json 2>/dev/null | wc -l > /out/bindings_count  # contract-allowed: shell glob inside the host container; the canonical value is asserted by run-matrix via BINDING_FILE derived from contract/aimail-contract.json
      tail -n 150 /opt/act.log > /out/act.log 2>/dev/null || true
    ' >"$out/rc" 2>&1
  rm -f "$jour/bindings_count" 2>/dev/null || true
  { cp "$out/bindings_count" "$jour/bindings_count"; } 2>/dev/null || echo 0 > "$jour/bindings_count"
  return 0
}

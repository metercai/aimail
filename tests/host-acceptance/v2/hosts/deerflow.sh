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
HOST_ADDRESS_MODEL=multi           # contract §11.1: many addresses per gateway
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
    bindings() { ls /app/.aimail/systems/*/*/agentmail.json 2>/dev/null; }

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

  # ── phase 2: boot again — the extension installs and its stream drains the queue ──
  docker exec "$name" bash -lc '
    set -u
    cd /app/backend
    # Same self-API port as phase 1 (see the note there).
    export PYTHONPATH=. DEER_FLOW_AUTH_DISABLED=1 AIMAIL_HOME=/app/.aimail
    export AIMAIL_DEERFLOW_API="http://127.0.0.1:'"$DF_API_PORT"'"
    nohup ./.venv/bin/python -m uvicorn app.gateway.app:app --host 127.0.0.1 \
      --port "'"$DF_API_PORT"'" >/tmp/df2.log 2>&1 &
    i=0
    while [ $i -lt 150 ]; do
      grep -qE "SSE reconcile|installed \(tools=|Uvicorn running" /tmp/df2.log 2>/dev/null && break
      i=$((i+1)); sleep 2
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
      ls /app/.aimail/systems/*/*/agentmail.json 2>/dev/null | wc -l > /out/bindings_count
      tail -n 150 /opt/act.log > /out/act.log 2>/dev/null || true
    ' >"$out/rc" 2>&1
  rm -f "$jour/bindings_count" 2>/dev/null || true
  { cp "$out/bindings_count" "$jour/bindings_count"; } 2>/dev/null || echo 0 > "$jour/bindings_count"
  return 0
}

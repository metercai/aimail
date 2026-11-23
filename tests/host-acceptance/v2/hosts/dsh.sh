#!/usr/bin/env bash
# DSH host adapter (containerised, pinned image) — ONE host lifetime for the whole journey.
#
#   host_journey <out> <addr> <code> <token>
#     install dev build -> boot -> activate via in-host prompt -> binding
#     -> restart (arm) -> wait for the host to drain the injected mail (ack)
#   The host side injects the mail once <out>/ready appears; this function blocks
#   until the container exits, so S1/S2/S3 all observe the same host process.
set -uo pipefail
HOST_NAME=dsh
DSH_IMAGE="${DSH_IMAGE:-aimail-host-dsh:latest}"
V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

host_journey() {
  local out="$1" addr="$2" code="$3" token="$4"
  mkdir -p "$out"
  local prompt="Activate and bind my AIMail mailbox address $addr with activation code $code at gateway ${GW_URL} using aimail_address."
  docker run --rm --network host --name "l2dsh-$$" \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "AC=$code" -e "APROMPT=$prompt" -e "GWURL=${GW_URL}" \
    -e "TOKEN=$token" -e "GWSMTP=$((GW_SMTP + 1))" -e "AH2=${S4_ADDR:-}" -e "AC2=${S4_CODE:-}" -e "LIFECYCLE=${LIFECYCLE:-0}" \
    -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" \
    -v "${GW_WORK}:/gw:ro" -v "$out:/out" \
    --entrypoint bash "$DSH_IMAGE" -lc '
      set -u
      export HOME=/root
      TGZ="$(ls /opt/sdk/*.tgz | head -1)"
      dsh plugin --profile web add "$TGZ" >/tmp/plugin.log 2>&1 || { echo "plugin add failed"; tail -5 /tmp/plugin.log; exit 3; }
      dsh --profile web --dump-config >/tmp/tree.txt 2>/dev/null || true
      grep -c "^- id: tool-mail" /tmp/tree.txt > /out/mail_rows 2>/dev/null || echo 0 > /out/mail_rows
      export DSH_PERMISSION_MODE=danger-full-access DSH_HOME=/root/.dsh AIMAIL_HOME=/root/.aimail
      export DEEPSEEK_API_KEY="${LLM_API_KEY:-}"
      setsid nohup dsh web --port 39123 --no-open >/tmp/web.log 2>&1 &
      URL=""
      for i in $(seq 1 120); do
        URL=$(grep -aoE "http://127\.0\.0\.1:[0-9]+/\?token=[A-Za-z0-9_-]+" /tmp/web.log | head -1)
        [ -n "$URL" ] && break; sleep 1
      done
      [ -n "$URL" ] || { echo "no web url"; tail -20 /tmp/web.log; exit 4; }
      python3 /opt/v2lib/dsh_activate.py "$URL" /root/ws "$APROMPT" >/tmp/act.log 2>&1 || true
      for i in $(seq 1 60); do ls /root/.aimail/systems/*/*/agentmail.json >/dev/null 2>&1 && break; sleep 2; done
      B=$(ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | head -1)
      [ -n "$B" ] && { stat -c %a "$B" > /out/mode; install -m 644 "$B" /out/binding.json; }
      echo "binding=${B:-none}"
      # arm: restart so the plugin reconciles the stream from the new binding
      pkill -f "dsh web --port 39123" 2>/dev/null; sleep 3
      setsid nohup dsh web --port 39123 --no-open >>/tmp/web2.log 2>&1 &
      sleep 20
      # signal the host side that the mailbox is live and waiting for mail
      date +%s > /out/ready
      # wait for the pull to drain (ack removes the pending row)
      python3 - <<PY
import sqlite3, os, time
db="/gw/storage/aimail.db"; addr=os.environ["AH"]; seen=0
for i in range(120):
    try:
        n=sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
            "select count(*) from pending_deliveries where email=?", (addr,)).fetchone()[0]
    except Exception:
        n=-1
    seen=max(seen,n)
    if seen>0 and n==0:
        print("acked_after_s=%d" % (i*2)); break
    time.sleep(2)
else:
    print("not_acked seen=%d" % seen)
PY
      # ── S4: second address in the same host lifetime (tssdk hosts are multi-address)
      if [ -n "${AH2:-}" ] && [ -n "${AC2:-}" ]; then
        DSH_KEEPALIVE_TICKS=8 DSH_READ_SECS=150 python3 /opt/v2lib/dsh_activate.py "$URL" /root/ws \
          "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"$AC2\",\"email\":\"$AH2\",\"gateway_url\":\"$GWURL\"}. Do not ask questions; just call it and report the result." \
          >/tmp/act2.log 2>&1 || true
        for i in $(seq 1 45); do
          n=$(ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | wc -l)
          [ "$n" -ge 2 ] && break; sleep 2
        done
        ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | wc -l > /out/bindings_count
        tail -n 80 /tmp/act2.log >> /out/act.log 2>/dev/null || true
      fi
      # ── S5: lifecycle — pause must stop delivery, unbind must remove the binding
      if [ "${LIFECYCLE:-0}" = "1" ]; then
        DSH_KEEPALIVE_TICKS=8 DSH_READ_SECS=120 python3 /opt/v2lib/dsh_activate.py "$URL" /root/ws \
          "Call the aimail_address tool now with exactly this JSON: {\"action\":\"pause\",\"email\":\"$AH\"}. Do not ask questions; just call it and report the result." >/tmp/act3.log 2>&1 || true
        sleep 8
        python3 /opt/v2lib/dsh_state.py /root/.aimail paused > /out/paused 2>/dev/null || echo unknown > /out/paused
        DSH_KEEPALIVE_TICKS=8 DSH_READ_SECS=120 python3 /opt/v2lib/dsh_activate.py "$URL" /root/ws \
          "Call the aimail_address tool now with exactly this JSON: {\"action\":\"unbind\",\"email\":\"$AH\"}. Do not ask questions; just call it and report the result." >/tmp/act4.log 2>&1 || true
        sleep 8
        ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | wc -l > /out/bindings_after_unbind
        tail -n 60 /tmp/act3.log /tmp/act4.log >> /out/act.log 2>/dev/null || true
      fi
      tail -n 300 /tmp/web.log /tmp/web2.log > /out/host.log 2>/dev/null || true
      tail -n 200 /tmp/act.log > /out/act.log 2>/dev/null || true
    ' >"$out/rc" 2>&1 &
  local pid=$!
  # host side: wait for readiness, then whitelist + inject the inbound mail
  for i in $(seq 1 240); do [ -f "$out/ready" ] && break; sleep 2; done
  if [ -f "$out/ready" ]; then
    python3 "$V2_ROOT/lib/gw.py" wl --gateway "$GW_URL" --key "$GW_ADMIN" --identity "$GW_SID" \
      --system "$GW_SID" --addr "$addr" --sender ext-sender@example.com >"$out/whitelist.json" 2>&1 || true
    grep -q '"ok": true' "$out/whitelist.json" || echo "whitelist failed: $(cat "$out/whitelist.json")" >>"$out/inject.log"
    python3 - "$addr" "$token" "$GW_SMTP" <<'PY' >>"$out/inject.log" 2>&1
import smtplib, sys
from email.message import EmailMessage
addr, token, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
m = EmailMessage(); m["From"] = "ext-sender@example.com"; m["To"] = addr
m["Subject"] = "L2 delivery probe"; m["Message-ID"] = "<l2-s3@example.com>"
m.set_content("delivery probe token " + token)
s = smtplib.SMTP("127.0.0.1", port, timeout=20); s.send_message(m); s.quit(); print("sent")
PY
    # observe the queue transition from the host side (the pull may drain in <2s)
    _max=0; _final=0
    for _i in $(seq 1 150); do
      _n="$(gw_pending_rows "$addr")"; _n="${_n:-0}"
      [ "$_n" -gt "$_max" ] 2>/dev/null && _max="$_n"
      _final="$_n"
      if [ "$_max" -gt 0 ] && [ "$_final" = 0 ]; then echo "acked_after_poll=$_i" >>"$out/inject.log"; break; fi
      sleep 0.2
    done
    echo "observed max=$_max final=$_final" >>"$out/inject.log"
    echo "emails=$(gw_sql "SELECT COUNT(*) FROM emails;")" >>"$out/inject.log"
  fi
  wait "$pid"
  return 0
}

host_s1_activate() { host_journey "$1" "$2" "$3" "${4:-NONE}"; }
host_s3_delivery() { :; }   # delivery happens inside host_journey
host_stop() { docker rm -f "l2dsh-$$" >/dev/null 2>&1 || true; }

# Negatives: an invalid code must not write a binding and must not arm.
host_negative() {
  local out="$1" addr="$2"
  docker run --rm --network host \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "GWURL=${GW_URL}" \
    -e "APROMPT=Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"addr-0000-deadbeef\",\"email\":\"$addr\",\"gateway_url\":\"${GW_URL}\"}. Do not ask questions; just call it and report the result." \
    -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" -v "$out:/out" \
    --entrypoint bash "$DSH_IMAGE" -lc '
      set -u
      export HOME=/root
      TGZ="$(ls /opt/sdk/*.tgz | head -1)"
      dsh plugin --profile web add "$TGZ" >/tmp/plugin.log 2>&1 || { echo "plugin add failed"; exit 3; }
      export DSH_PERMISSION_MODE=danger-full-access DSH_HOME=/root/.dsh AIMAIL_HOME=/root/.aimail
      export DEEPSEEK_API_KEY="${LLM_API_KEY:-}"
      setsid nohup dsh web --port 39124 --no-open >/tmp/web.log 2>&1 &
      URL=""
      for i in $(seq 1 120); do
        URL=$(grep -aoE "http://127\.0\.0\.1:[0-9]+/\?token=[A-Za-z0-9_-]+" /tmp/web.log | head -1)
        [ -n "$URL" ] && break; sleep 1
      done
      [ -n "$URL" ] || { echo "no web url"; exit 4; }
      DSH_KEEPALIVE_TICKS=6 DSH_READ_SECS=90 python3 /opt/v2lib/dsh_activate.py "$URL" /root/ws "$APROMPT" >/tmp/neg.log 2>&1 || true
      sleep 10
      ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | wc -l > /out/bindings_count
      tail -n 120 /tmp/neg.log > /out/act.log 2>/dev/null || true
    ' >"$out/rc" 2>&1
  return 0
}

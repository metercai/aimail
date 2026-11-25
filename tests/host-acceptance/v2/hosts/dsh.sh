#!/usr/bin/env bash
# DSH host adapter — ONE host lifetime and ONE session for the whole journey.
#
#   host_journey <out> <addr> <code> <token>
#     install dev build -> boot -> (bind1 [, bind2] [, pause] [, unbind]) in the SAME
#     session -> restart (arm) -> wait for the injected mail to be acked.
#   Optional (env): S4_ADDR/S4_CODE (second address), LIFECYCLE=1 (pause/unbind).
set -uo pipefail
HOST_NAME=dsh
HOST_DELIVERY_CONFIRMATION=probe      # 附 E: 会话注册表可查 + resume 失败即 session-gone ⇒ C17 可证
DSH_IMAGE="${DSH_IMAGE:-aimail-host-dsh:latest}"
V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

host_journey() {
  local out="$1" addr="$2" code="$3" token="$4"
  mkdir -p "$out"
  S6FLAG=0; S7FLAG=0; S8FLAG=0
  case " ${STAGES:-} " in *" s6 "*) S6FLAG=1 ;; esac
  case " ${STAGES:-} " in *" s7 "*) S7FLAG=1 ;; esac
  case " ${STAGES:-} " in *" s8 "*) S8FLAG=1 ;; esac
  docker run --rm --network host --name "l2dsh-$$" \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "AC=$code" -e "GWURL=${GW_URL}" \
    -e "TOKEN=$token" -e "GWSMTP=$((GW_SMTP + 1))" \
    -e "AH2=${S4_ADDR:-}" -e "AC2=${S4_CODE:-}" -e "LIFECYCLE=${LIFECYCLE:-0}" \
    -e "S6=$S6FLAG" -e "S7=$S7FLAG" -e "S8=$S8FLAG" \
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
      : > /tmp/prompts.txt
      printf "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"%s\",\"email\":\"%s\",\"gateway_url\":\"%s\"}. Do not ask questions; just call it and report the result.\n" "$AC" "$AH" "$GWURL" >> /tmp/prompts.txt
      if [ -n "$AH2" ] && [ -n "$AC2" ]; then
        printf "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"%s\",\"email\":\"%s\",\"gateway_url\":\"%s\"}. Do not ask questions; just call it and report the result.\n" "$AC2" "$AH2" "$GWURL" >> /tmp/prompts.txt
      fi
      if [ "${LIFECYCLE:-0}" = "1" ]; then
        printf "Call the aimail_address tool now with exactly this JSON: {\"action\":\"pause\",\"email\":\"%s\"}. Do not ask questions; just call it and report the result.\n" "$AH" >> /tmp/prompts.txt
        printf "Call the aimail_address tool now with exactly this JSON: {\"action\":\"unbind\",\"email\":\"%s\"}. Do not ask questions; just call it and report the result.\n" "$AH" >> /tmp/prompts.txt
      fi
      DSH_STATE_DIR=/out DSH_TURN_GAP_SECS="${TURN_GAP:-100}" DSH_READ_SECS=420 DSH_KEEPALIVE_TICKS=1 \
        python3 /opt/v2lib/dsh_activate.py "$URL" /root/ws /tmp/prompts.txt >/tmp/act.log 2>&1 || true
      # aggregate the per-turn snapshots
      python3 /opt/v2lib/dsh_state.py /out summarize > /out/journey_state 2>/dev/null || true
      stat -c %a "$(ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | head -1)" > /out/mode  # contract-allowed: in-container fixture glob; repo contract is not mounted in the host image 2>/dev/null || echo none > /out/mode
      B=$(ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | head -1)  # contract-allowed: in-container fixture glob; repo contract is not mounted in the host image
      [ -n "$B" ] && install -m 644 "$B" /out/binding.json
      echo "binding=${B:-none}"
      # arm + delivery only when the mailbox is still bound (no unbind stage)
      if [ -n "$B" ] && [ "${LIFECYCLE:-0}" != "1" ]; then
        pkill -f "dsh web --port 39123" 2>/dev/null; sleep 3
        setsid nohup dsh web --port 39123 --no-open >>/tmp/web2.log 2>&1 &
        sleep 20
        date +%s > /out/ready
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
      else
        date +%s > /out/ready
      fi
      # s6 (contract C8) — placed AFTER the if/else: the branch above differs per configuration and
      # the first attempt sat in the branch that these runs never take (measured: ready existed,
      # acked_after_s=18 came from the other branch, and s6_drive.log was never created).
      if [ "${S6:-0}" = "1" ]; then
        i=0
        while [ ! -f /out/s6_go ] && [ $i -lt 300 ]; do i=$((i+1)); sleep 1; done
        python3 /opt/v2lib/s6_drive.py --addr "$AH" --smtp-port "$GWSMTP" \
          --db /gw/storage/aimail.db --out /out \
          --session-root /root/.dsh/sessions --depth 2 >> /out/s6_drive.log 2>&1 || true
      fi
      # s7 (contract C17): delete the session that the thread mail landed in, then reply to that mail.
      # Runs after s6 because it reuses the whitelist and the same session store.
      if [ "${S7:-0}" = "1" ]; then
        python3 /opt/v2lib/s7_drive.py --addr "$AH" --smtp-port "$GWSMTP" \
          --db /gw/storage/aimail.db --out /out \
          --session-root /root/.dsh/sessions --depth 2 --settle 20 --stale-session --thread-meta \
          >> /out/s7_drive.log 2>&1 || true
      fi
      # s8 (contract C16): point the thread at a session that belongs to ANOTHER anchor, then reply.
      # The recorded session stays LIVE on purpose — only the anchor check can refuse it.
      if [ "${S8:-0}" = "1" ]; then
        python3 /opt/v2lib/s8_drive.py --addr "$AH" --smtp-port "$GWSMTP" \
          --db /gw/storage/aimail.db --out /out \
          --session-root /root/.dsh/sessions --depth 2 --settle 20 --aimail-home /root/.aimail \
          >> /out/s8_drive.log 2>&1 || true
      fi
      tail -n 400 /tmp/web.log /tmp/web2.log > /out/host.log 2>/dev/null || true
      tail -n 300 /tmp/act.log > /out/act.log 2>/dev/null || true
    ' >"$out/rc" 2>&1 &
  local pid=$!
  for i in $(seq 1 300); do [ -f "$out/ready" ] && break; sleep 2; done
  if [ -f "$out/ready" ] && [ "${LIFECYCLE:-0}" != "1" ]; then
    python3 "$V2_ROOT/lib/gw.py" wl --gateway "$GW_URL" --key "$GW_ADMIN" --identity "$GW_SID" \
      --system "$GW_SID" --addr "$addr" --sender ext-sender@example.com >"$out/whitelist.json" 2>&1 || true
    grep -q '"ok": true' "$out/whitelist.json" || echo "whitelist failed: $(cat "$out/whitelist.json")" >>"$out/inject.log"
    # Port fix (2026-11-23): the gateway's inbound listener is $((GW_SMTP + 1)); GW_SMTP
    # itself is the sink (relay peer) — `gw.sh:63` binds the gateway at smtp+1 and
    # `gw.sh:70` points [relay] at smtp. Sending to "$GW_SMTP" delivered the probe
    # straight to the sink, which spooled the original and answered 250 while the
    # gateway never saw the mail ⇒ S3 read emails=0/pending=0 and looked like the
    # gateway was relaying inbound. Container side above already used +1.
    # Attribution probe (env-gated, default off): the v2.4 gateway enforces the binding's anchor on
    # every authenticated write. If a host pulls nothing while an anchor is recorded, dropping the
    # anchor for this one run and re-injecting isolates "anchor enforcement" from every other cause.
    if [ "${AIMAIL_L2_DROP_ANCHOR:-0}" = "1" ]; then
      gw_sql "DELETE FROM address_anchor;" >/dev/null 2>&1 || true
      echo "A/B: dropped address_anchor rows, now=$(gw_sql 'SELECT COUNT(*) FROM address_anchor;')" >>"$out/inject.log"
    fi

    python3 - "$addr" "$token" "$((GW_SMTP + 1))" <<'PY' >>"$out/inject.log" 2>&1
import smtplib, sys
from email.message import EmailMessage
addr, token, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
m = EmailMessage(); m["From"] = "ext-sender@example.com"; m["To"] = addr
m["Subject"] = "L2 delivery probe"; m["Message-ID"] = "<l2-s3@example.com>"
m.set_content("delivery probe token " + token)
s = smtplib.SMTP("127.0.0.1", port, timeout=20); s.send_message(m); s.quit(); print("sent")
PY
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

  # s6: whitelist is in place now, so let the in-container driver run the three-mail probe.
  case " ${STAGES:-} " in
  *" s6 "*)
    : > "$out/s6_go"
    for _i in $(seq 1 600); do [ -f "$out/s6.json" ] && break; sleep 1; done
    [ -f "$out/s6.json" ] || echo "s6 driver produced no evidence (see s6_drive.log)" > "$out/s6_missing"
    ;;
  esac
  wait "$pid"
  return 0
}

host_s1_activate() { host_journey "$1" "$2" "$3" "${4:-NONE}"; }
host_s6_threading() { :; }   # evidence is produced inside host_journey (same container)
host_s3_delivery() { :; }
host_stop() { docker rm -f "l2dsh-$$" >/dev/null 2>&1 || true; }

# Negatives: an invalid code must not write a binding and must not arm.
host_negative() {
  local out="$1" addr="$2"
  docker run --rm --network host \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "GWURL=${GW_URL}" \
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
      printf "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"addr-0000-deadbeef\",\"email\":\"%s\",\"gateway_url\":\"%s\"}. Do not ask questions; just call it and report the result.\n" "$AH" "$GWURL" > /tmp/neg.txt
      DSH_READ_SECS=120 DSH_KEEPALIVE_TICKS=1 python3 /opt/v2lib/dsh_activate.py "$URL" /root/ws /tmp/neg.txt >/tmp/neg.log 2>&1 || true
      sleep 10
      ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | wc -l > /out/bindings_count  # contract-allowed: in-container fixture glob; the repo contract is not mounted in the host image
      tail -n 150 /tmp/neg.log > /out/act.log 2>/dev/null || true
    ' >"$out/rc" 2>&1
  return 0
}

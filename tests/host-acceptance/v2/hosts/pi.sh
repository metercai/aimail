#!/usr/bin/env bash
# Pi host adapter — single-address tssdk host (L2 = dev build pre-installed).
#
# Pi drives one prompt per process for the bind/pause/unbind turns; the binding directory
# is the shared state, so a multi-turn journey is a sequence of `pi -p` runs over the same
# HOME.
# S3 (live delivery) DOES need a process that outlives the turn, so it runs one `pi --mode
# rpc` session in the same HOME, injects through the gateway inbound port and waits for the
# pull queue to drain (acked). Before 2026-11-23 this was a registered GAP ("not wired")
# which meant pi's delivery path was not covered at all; a registered hole is still a hole.
set -uo pipefail
HOST_NAME=pi
PI_IMAGE="${PI_IMAGE:-aimail-host-pi:latest}"
V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_pi_run() {  # _pi_run <out> <prompt>
  local out="$1" prompt="$2"
  local hdir="$out/home${PI_HOME_SUFFIX:+_$PI_HOME_SUFFIX}"
  mkdir -p "$hdir"
  # persistent HOME: pi keeps the binding under /root/.aimail — a fresh container
  # per turn would lose it, so the journey shares one state directory.
  docker run --rm --network host -v "$hdir:/root" \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "PROMPT=$prompt" \
    -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" -v "$out:/out" \
    --entrypoint bash "$PI_IMAGE" -lc '
      set -u
      export HOME=/root
      python3 - <<PY
import json, os, pathlib
p = pathlib.Path("/root/.pi/agent/models.json")
p.parent.mkdir(parents=True, exist_ok=True)
try:
    d = json.loads(p.read_text())
except Exception:
    d = {"providers": {}}
provs = d.setdefault("providers", {})
if not provs:
    provs["custom-api-xiaomimimo-com"] = {
        "baseUrl": "https://api.xiaomimimo.com/v1",
        "api": "openai-completions",
        "models": [{"id": "mimo-v2.6-flash"}],
    }
for prov in provs.values():
    prov["apiKey"] = os.environ["LLM_API_KEY"]
d.setdefault("defaultModel", "custom-api-xiaomimimo-com/mimo-v2.6-flash")
p.write_text(json.dumps(d, indent=2))
PY
      TGZ="$(ls /opt/sdk/*.tgz | head -1)"
      mkdir -p /tmp/sdk && tar -xzf "$TGZ" -C /tmp/sdk
      pi install /tmp/sdk/package >/tmp/install.log 2>&1 || true
      mkdir -p /root/ws && cd /root/ws
      timeout 420 pi -p "$PROMPT" >/tmp/pi.log 2>&1 || true
      python3 /opt/v2lib/dsh_state.py /root/.aimail count > /out/bindings_seen 2>/dev/null || echo 0 > /out/bindings_seen
      B=$(ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | head -1)  # contract-allowed: fixture globs the SDK binding file; the repo contract is not mounted inside the host image
      if [ -n "$B" ]; then install -m 644 "$B" /out/binding.json; stat -c %a "$B" > /out/mode; fi
      python3 - <<PY >/out/paused_seen 2>/dev/null || echo None >/out/paused_seen
import glob, json
p = sorted(glob.glob("/root/.aimail/systems/*/*/agentmail.json"))  # contract-allowed: fixture globs the SDK binding file; the repo contract is not mounted inside the host image
print(json.load(open(p[0])).get("paused") if p else "no-binding")
PY
      tail -n 200 /tmp/pi.log > /out/last.log 2>/dev/null || true
    ' >"$out/run.log" 2>&1
}

_pi_delivery() {  # _pi_delivery <out> <addr> <token>
  # Long-lived session: `pi --mode rpc` keeps the extension (and therefore its SSE pull)
  # alive while the probe is injected. Evidence written to $out/inject.log in the format
  # cases/s3_delivery.sh reads: sent / emails= / observed max= final= / acked_after_poll=.
  local out="$1" addr="$2" token="$3"
  local hdir="$out/home"; mkdir -p "$hdir"
  : > "$out/inject.log"
  rm -f "$out/pi.fifo"; mkfifo "$out/pi.fifo"
  ( sleep 600 > "$out/pi.fifo" & ) 2>/dev/null || true
  # -i is REQUIRED: without it docker gives the container /dev/null as stdin, `pi --mode
  # rpc` sees EOF and exits at once (measured: container gone, 0 log lines). With -i the
  # fifo keeps the session alive.
  docker run --rm -i --network host --name "l2pi-live-$$" \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" \
    -v "$hdir:/root" -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" -v "$out:/out" \
    --entrypoint bash "$PI_IMAGE" -lc '
      set -u
      export HOME=/root
      mkdir -p /root/ws && cd /root/ws
      exec pi --mode rpc
    ' >"$out/pi-live.log" 2>&1 <"$out/pi.fifo" &
  local pid=$!
  # boot + extension load (the pull is armed by the extension on load; no turn needed)
  # `pi --mode rpc` is silent until it receives an rpc command (measured 2026-11-23:
  # 0 bytes of output, process alive), so a log-based readiness test can never match.
  # Give the extension its boot time, then look for a pull hint in the GATEWAY log, which
  # is the side that actually accepts the stream.
  local up=0 _i
  for _i in $(seq 1 8); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 5
  done
  if ! kill -0 "$pid" 2>/dev/null; then
    # never injected: no `observed` line, so s3 reports GAP (fixture), not a product RED
    echo "live session exited during boot (see $out/pi-live.log)" >> "$out/inject.log"
    echo "emails=" >> "$out/inject.log"
    return 0
  fi
  if grep -qaE "pull|stream|agent.scope|asid_" "${GW_WORK:-/nonexistent}/gateway.log" 2>/dev/null; then up=1; fi
  if [ "$up" != 1 ]; then
    # No gateway-side pull hint. Do NOT skip the probe (that would leave pi's delivery
    # path uncovered again) and do NOT call a missing ack a product defect either: inject,
    # and let the ack outcome plus this flag decide the attribution:
    #   acked                          -> PASS
    #   not acked + arming seen        -> RED   (the host pulled, so it should have acked)
    #   not acked + no arming evidence -> GAP   (fixture never got the client attached)
    echo "arming_hint=absent (no pull line in the gateway log; see $out/pi-live.log)" >> "$out/inject.log"
  fi
  python3 "$V2_ROOT/lib/gw.py" wl --gateway "$GW_URL" --key "$GW_ADMIN" --identity "$GW_SID" \
    --system "$GW_SID" --addr "$addr" --sender ext-sender@example.com >"$out/whitelist.json" 2>&1 || true
  grep -q '"ok": true' "$out/whitelist.json" || echo "whitelist failed: $(cat "$out/whitelist.json")" >>"$out/inject.log"
  python3 - "$addr" "$token" "$((GW_SMTP + 1))" <<'PY' >>"$out/inject.log" 2>&1
import smtplib, sys
from email.message import EmailMessage
addr, token, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
m = EmailMessage(); m["From"] = "ext-sender@example.com"; m["To"] = addr
m["Subject"] = "L2 delivery probe"; m["Message-ID"] = "<l2-s3@example.com>"
m.set_content("delivery probe token " + token)
try:
    with smtplib.SMTP("127.0.0.1", port, timeout=20) as s:
        s.sendmail("ext-sender@example.com", [addr], m.as_string())
    print("sent")
except Exception as e:
    print(f"send failed: {type(e).__name__}: {e}")
PY
  local acked=0 max=1
  for _i in $(seq 1 90); do
    local final emails
    final="$(gw_pending_rows "$addr" 2>/dev/null || echo 1)"
    emails="$(gw_sql "SELECT COUNT(*) FROM emails;" 2>/dev/null || echo 0)"
    if [ "${final:-1}" = "0" ] && [ "${emails:-0}" -ge 1 ]; then acked="$_i"; break; fi
    sleep 2
  done
  local emails final
  final="$(gw_pending_rows "$addr" 2>/dev/null || echo 1)"
  emails="$(gw_sql "SELECT COUNT(*) FROM emails;" 2>/dev/null || echo 0)"
  {
    echo "observed max=$max final=${final:-1}"
    echo "emails=${emails:-0}"
    echo "arming_seen=${up:-0}"
    [ "${acked:-0}" -gt 0 ] && echo "acked_after_poll=$acked"
  } >> "$out/inject.log"
  kill "$pid" 2>/dev/null || true
  sleep 1
  docker rm -f "l2pi-live-$$" >/dev/null 2>&1 || true
  return 0
}

host_journey() {
  local out="$1" addr="$2" code="$3" token="$4"
  mkdir -p "$out"
  _pi_run "$out" "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"$code\",\"email\":\"$addr\",\"gateway_url\":\"${GW_URL}\"}. Do not ask questions; just call it and report the result."
  local maxb; maxb="$(cat "$out/bindings_seen" 2>/dev/null || echo 0)"
  local paused="False"

  if [ -n "${S4_ADDR:-}" ] && [ -n "${S4_CODE:-}" ]; then
    _pi_run "$out" "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"${S4_CODE}\",\"email\":\"${S4_ADDR}\",\"gateway_url\":\"${GW_URL}\"}. Do not ask questions; just call it and report the result."
    local b2; b2="$(cat "$out/bindings_seen" 2>/dev/null || echo 0)"
    [ "${b2:-0}" -gt "${maxb:-0}" ] && maxb="$b2"
  fi

  if [ "${LIFECYCLE:-0}" = "1" ]; then
    _pi_run "$out" "Call the aimail_address tool now with exactly this JSON: {\"action\":\"pause\",\"email\":\"$addr\"}. Do not ask questions; just call it and report the result."
    paused="$(cat "$out/paused_seen" 2>/dev/null || echo False)"
    _pi_run "$out" "Call the aimail_address tool now with exactly this JSON: {\"action\":\"unbind\",\"email\":\"$addr\"}. Do not ask questions; just call it and report the result."
  fi

  case " ${STAGES:-} " in
    *" s3 "*) _pi_delivery "$out" "$addr" "$token" ;;   # live delivery needs the long session
  esac

  local final; final="$(cat "$out/bindings_seen" 2>/dev/null || echo "$maxb")"
  printf 'max_bindings=%s paused_seen=%s final_bindings=%s turns=na\n' "$maxb" "$paused" "$final" > "$out/journey_state"
  [ -f "$out/last.log" ] && cp "$out/last.log" "$out/host.log" 2>/dev/null || true
  # binding artefact is captured inside _pi_run (it owns the persistent HOME)
  return 0
}

host_s1_activate() { host_journey "$@"; }
host_s3_delivery() { :; }
host_stop() { :; }

host_negative() {
  local out="$1" addr="$2"
  PI_HOME_SUFFIX=neg _pi_run "$out" "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"addr-0000-deadbeef\",\"email\":\"$addr\",\"gateway_url\":\"${GW_URL}\"}. Do not ask questions; just call it and report the result."
  cp "$out/bindings_seen" "$out/bindings_count" 2>/dev/null || echo 0 > "$out/bindings_count"
  cp "$out/last.log" "$out/act.log" 2>/dev/null || true
  return 0
}

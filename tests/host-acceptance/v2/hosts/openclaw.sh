#!/usr/bin/env bash
# OpenClaw host adapter — one gateway lifetime, sequential turns over /hooks/agent.
#
#   host_journey <out> <addr> <code> <token>
#     install dev build -> config -> boot gateway -> (bind1 [, bind2] [, pause] [, unbind])
#     -> restart (arm) -> wait for the injected mail to be acked.
set -uo pipefail
HOST_NAME=openclaw
OC_IMAGE="${OC_IMAGE:-aimail-host-openclaw:latest}"
V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

host_journey() {
  local out="$1" addr="$2" code="$3" token="$4"
  mkdir -p "$out"
  local second="" lifecycle="${LIFECYCLE:-0}"
  if [ -n "${S4_ADDR:-}" ] && [ -n "${S4_CODE:-}" ]; then second="${S4_ADDR}|${S4_CODE}"; fi
  docker run --rm --network host --name "l2oc-$$" \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "AC=$code" -e "GWURL=${GW_URL}" \
    -e "SECOND=$second" -e "LIFECYCLE=$lifecycle" -e "TOKEN=$token" -e "GWSMTP=$((GW_SMTP + 1))" \
    -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" \
    -v "${GW_WORK}:/gw:ro" -v "$out:/out" \
    --entrypoint bash "$OC_IMAGE" -lc '
      set -u
      export HOME=/app
      python3 - <<PY
import json, os, pathlib
p = pathlib.Path("/app/.openclaw/openclaw.json")
c = json.loads(p.read_text()) if p.exists() else {}
provs = ((c.setdefault("models", {})).setdefault("providers", {}))
prov = provs.setdefault("custom-api-xiaomimimo-com", {
    "baseUrl": "https://api.xiaomimimo.com/v1", "api": "openai-completions",
    "models": [{"id": "mimo-v2.6-flash"}]})
prov["apiKey"] = os.environ["LLM_API_KEY"]
c.setdefault("agents", {}).setdefault("defaults", {})["model"] = "custom-api-xiaomimimo-com/mimo-v2.6-flash"
c.setdefault("plugins", {}).update({"enabled": True, "allow": ["agent-aimail"]})
c["plugins"].setdefault("entries", {})["agent-aimail"] = {"enabled": True}
c.setdefault("tools", {})["alsoAllow"] = ["send_mail","reply_mail","forward_mail","list_mail","read_mail","search_mail","delete_mail","mark_mail","download_attachment","get_mailbox_info","manage_mailbox","role_calibrator","aimail_address","aimail_system","aimail_contacts","aimail_preferences"]
c.setdefault("hooks", {}).update({"enabled": True, "token": "l2-hook-token"})
c.setdefault("gateway", {}).update({"mode": "local", "port": 39200, "auth": {"token": "l2-oc-token"}})
p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(c, indent=2))
PY
      TGZ="$(ls /opt/sdk/*.tgz | head -1)"
      openclaw plugins install "$TGZ" --force --accept-capabilities >/tmp/plugin.log 2>&1 || { echo "plugin install failed"; tail -8 /tmp/plugin.log; exit 3; }
      openclaw plugins enable agent-aimail --accept-capabilities >/tmp/enable.log 2>&1 || true
      pkill -f "openclaw gateway" 2>/dev/null; sleep 2
      setsid nohup openclaw gateway >/tmp/gw.log 2>&1 &
      for i in $(seq 1 90); do grep -qa "listening" /tmp/gw.log && break; sleep 2; done
      _turn() {
        python3 - "$1" <<PY
import json, sys, urllib.request
prompt = sys.argv[1]
body = json.dumps({"message": prompt}).encode()
req = urllib.request.Request("http://127.0.0.1:39200/hooks/agent", data=body,
    headers={"content-type": "application/json", "authorization": "Bearer l2-hook-token"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        print("turn:", r.read(200).decode())
except Exception as e:
    print("turn error:", type(e).__name__, str(e)[:160])
PY
      }
      _bind() { _turn "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"$2\",\"email\":\"$1\",\"gateway_url\":\"$GWURL\"}. Do not ask questions; just call it and report the result."; }
      _bind "$AH" "$AC"
      python3 /opt/v2lib/dsh_state.py /app/.aimail count > /out/b1 2>/dev/null || echo 0 > /out/b1
      if [ -n "$SECOND" ]; then
        SADDR="${SECOND%%|*}"; SCODE="${SECOND##*|}"
        _bind "$SADDR" "$SCODE"
        python3 /opt/v2lib/dsh_state.py /app/.aimail count > /out/b2 2>/dev/null || echo 0 > /out/b2
      fi
      if [ "$LIFECYCLE" = "1" ]; then
        _turn "Call the aimail_address tool now with exactly this JSON: {\"action\":\"pause\",\"email\":\"$AH\"}. Do not ask questions; just call it and report the result."
        python3 /opt/v2lib/dsh_state.py /app/.aimail paused > /out/paused 2>/dev/null || echo unknown > /out/paused
        _turn "Call the aimail_address tool now with exactly this JSON: {\"action\":\"unbind\",\"email\":\"$AH\"}. Do not ask questions; just call it and report the result."
        python3 /opt/v2lib/dsh_state.py /app/.aimail count > /out/b3 2>/dev/null || echo 0 > /out/b3
      fi
      B=$(ls /app/.aimail/systems/*/*/agentmail.json 2>/dev/null | head -1)
      [ -n "$B" ] && { install -m 644 "$B" /out/binding.json; stat -c %a "$B" > /out/mode; }
      python3 /opt/v2lib/dsh_state.py /app/.aimail count > /out/bindings_count 2>/dev/null || echo 0 > /out/bindings_count
      if [ -n "$B" ] && [ "$LIFECYCLE" != "1" ]; then
        pkill -f "openclaw gateway" 2>/dev/null; sleep 4
        setsid nohup openclaw gateway >/tmp/gw2.log 2>&1 &
        sleep 25
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
      tail -n 300 /tmp/gw.log /tmp/gw2.log > /out/host.log 2>/dev/null || true
    ' >"$out/rc" 2>&1 &
  local pid=$!
  for i in $(seq 1 300); do [ -f "$out/ready" ] && break; sleep 2; done
  if [ -f "$out/ready" ] && [ "${LIFECYCLE:-0}" != "1" ]; then
    python3 "$V2_ROOT/lib/gw.py" wl --gateway "$GW_URL" --key "$GW_ADMIN" --identity "$GW_SID" \
      --system "$GW_SID" --addr "$addr" --sender ext-sender@example.com >"$out/whitelist.json" 2>&1 || true
    python3 - "$addr" "$token" "$GW_SMTP" <<'PY' >>"$out/inject.log" 2>&1
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
  wait "$pid"
  # aggregate state for the cases
  local maxb final p
  maxb="$(cat "$out/b2" 2>/dev/null || cat "$out/b1" 2>/dev/null || echo 0)"
  final="$(cat "$out/b3" 2>/dev/null || cat "$out/bindings_count" 2>/dev/null || echo "$maxb")"
  p="$(cat "$out/paused" 2>/dev/null || echo None)"
  printf 'max_bindings=%s paused_seen=%s final_bindings=%s turns=na\n' "$maxb" "$p" "$final" > "$out/journey_state"
  return 0
}

host_s1_activate() { host_journey "$@"; }
host_s3_delivery() { :; }
host_stop() { docker rm -f "l2oc-$$" >/dev/null 2>&1 || true; }

host_negative() {
  local out="$1" addr="$2"
  mkdir -p "$out"
  docker run --rm --network host \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "GWURL=${GW_URL}" \
    -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" -v "$out:/out" \
    --entrypoint bash "$OC_IMAGE" -lc '
      set -u
      export HOME=/app
      python3 - <<PY
import json, os, pathlib
p = pathlib.Path("/app/.openclaw/openclaw.json")
c = json.loads(p.read_text()) if p.exists() else {}
c.setdefault("models", {}).setdefault("providers", {}).setdefault("custom-api-xiaomimimo-com", {
    "baseUrl": "https://api.xiaomimimo.com/v1", "api": "openai-completions",
    "models": [{"id": "mimo-v2.6-flash"}]})["apiKey"] = os.environ["LLM_API_KEY"]
c.setdefault("agents", {}).setdefault("defaults", {})["model"] = "custom-api-xiaomimimo-com/mimo-v2.6-flash"
c.setdefault("plugins", {}).update({"enabled": True, "allow": ["agent-aimail"]})
c["plugins"].setdefault("entries", {})["agent-aimail"] = {"enabled": True}
c.setdefault("tools", {})["alsoAllow"] = ["aimail_address","aimail_system"]
c.setdefault("hooks", {}).update({"enabled": True, "token": "l2-hook-token"})
c.setdefault("gateway", {}).update({"mode": "local", "port": 39201, "auth": {"token": "l2-oc-token"}})
p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(c, indent=2))
PY
      TGZ="$(ls /opt/sdk/*.tgz | head -1)"
      openclaw plugins install "$TGZ" --force --accept-capabilities >/tmp/plugin.log 2>&1 || { echo "install failed"; tail -8 /tmp/plugin.log; exit 3; }
      openclaw plugins enable agent-aimail --accept-capabilities >/tmp/enable.log 2>&1 || true
      pkill -f "openclaw gateway" 2>/dev/null; sleep 2
      setsid nohup openclaw gateway >/tmp/gw.log 2>&1 &
      for i in $(seq 1 90); do grep -qa "listening" /tmp/gw.log && break; sleep 2; done
      python3 - <<PY
import json, urllib.request
body = json.dumps({"message": "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"addr-0000-deadbeef\",\"email\":\"" + __import__("os").environ["AH"] + "\",\"gateway_url\":\"" + __import__("os").environ["GWURL"] + "\"}. Do not ask questions; just call it and report the result."}).encode()
req = urllib.request.Request("http://127.0.0.1:39201/hooks/agent", data=body,
    headers={"content-type": "application/json", "authorization": "Bearer l2-hook-token"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        print("turn:", r.read(160).decode())
except Exception as e:
    print("turn error:", type(e).__name__, str(e)[:120])
PY
      sleep 5
      python3 /opt/v2lib/dsh_state.py /app/.aimail count > /out/bindings_count 2>/dev/null || echo 0 > /out/bindings_count
      tail -n 120 /tmp/gw.log > /out/act.log 2>/dev/null || true
    ' >"$out/rc" 2>&1
  return 0
}

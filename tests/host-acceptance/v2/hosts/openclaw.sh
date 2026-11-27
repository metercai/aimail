#!/usr/bin/env bash
# OpenClaw host adapter — one gateway lifetime, sequential turns over /hooks/agent.
#
#   host_journey <out> <addr> <code> <token>
#     install dev build -> config -> boot gateway -> (bind1 [, bind2] [, pause] [, unbind])
#     -> restart (arm) -> wait for the injected mail to be acked.
set -uo pipefail
HOST_NAME=openclaw
# Measured 2026-11-27: two binding files coexisted (b1=1, b2=2) under
# /app/.aimail/systems/*/*/agentmail.json — the multi-address layout. Declared explicitly so the
# capability cannot be inherited from whichever host was sourced before this one.
HOST_ADDRESS_MODEL=multi
HOST_DELIVERY_CONFIRMATION=unconfirmed  # 附 E: 200＝cron job 入队，无 run/session 查询；C17 仍受判（需证据，缺则 GAP）
HOST_RISK_ACCEPTED="owner 2026-11-25: openclaw 200=入队、无 run 查询；接受 ack-未落地风险"
OC_IMAGE="${OC_IMAGE:-aimail-host-openclaw:latest}"
V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

host_journey() {
  local out="$1" addr="$2" code="$3" token="$4"
  mkdir -p "$out"
  local second="" lifecycle="${LIFECYCLE:-0}"
  if [ -n "${S4_ADDR:-}" ] && [ -n "${S4_CODE:-}" ]; then second="${S4_ADDR}|${S4_CODE}"; fi
  S6FLAG=0
  case " ${STAGES:-} " in *" s6 "*) S6FLAG=1 ;; esac
  docker run --rm --network host --name "l2oc-$$" \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "AC=$code" -e "GWURL=${GW_URL}" \
    -e "SECOND=$second" -e "LIFECYCLE=$lifecycle" -e "TOKEN=$token" -e "GWSMTP=$((GW_SMTP + 1))" \
    -e "S6=$S6FLAG" \
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
# capability-registered tools: no tools.alsoAllow (a stale allow-list hides them)
c.setdefault("hooks", {}).update({"enabled": True, "token": "l2-hook-token"})
# C8 (same thread ⇒ same host session) is OPT-IN on this host: the plugin only forwards its
# thread-derived sessionKey when the host allows request session keys and the prefix is whitelisted
# (tssdk .../openclaw/inbound.ts:274-280). Without this the host mints hook:<uuid> per email and every
# mail looks like a new session — measured 3 → 4 → 5 before this line existed.
# Gated on S6: enabling this broke DELIVERY in the s6 run (acked=false for all three mails, one
# session in the host list), so it must not silently change the s1-s5 path before that is understood.
if os.environ.get("S6") == "1":
    # The gateway REFUSES TO START unless the whitelist also carries "hook:" (verified in-container:
    # "hooks.allowedSessionKeyPrefixes must include 'hook:' when hooks.defaultSessionKey is unset").
    # With only "agent:" the gateway never came up, so nothing was pulled and all three s6 mails stayed
    # unacked — that is what the earlier "switch on ⇒ delivery stops" measurement actually was.
    c["hooks"].update({"allowRequestSessionKey": True,
                       "allowedSessionKeyPrefixes": ["agent:", "hook:"]})
c.setdefault("gateway", {}).update({"mode": "local", "port": 39200, "auth": {"token": "l2-oc-token"}})
p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(c, indent=2))
PY
      if [ "'${SDK_KIND:-ts}' " = none ]; then echo "L3: SDK install skipped (host must install the published package)" >>/tmp/plugin.log; else
      TGZ="$(ls /opt/sdk/*.tgz | head -1)"
      openclaw plugins install "$TGZ" --force --accept-capabilities >/tmp/plugin.log 2>&1 || { echo "plugin install failed"; tail -8 /tmp/plugin.log; exit 3; }
      openclaw plugins enable agent-aimail --accept-capabilities >/tmp/enable.log 2>&1 || true
      fi
      pkill -f "openclaw gateway" 2>/dev/null; sleep 2
      echo "boot: embedded agent mode (no gateway during turns)" >/tmp/gw.log
      _turn() {
        pkill -f "openclaw gateway" 2>/dev/null; sleep 2
        timeout 420 openclaw agent --local --message "$1" >/tmp/turn.log 2>&1 || true
        cat /tmp/turn.log >> /tmp/turns.log 2>/dev/null || true
        echo "turn: $(tail -3 /tmp/turn.log | tr "\n" " " | cut -c1-200)"
      }
      # openclaw exposes plugin tools through the meta-tool surface: the model must
      # dispatch by id (dispatch_action), not call aimail_address directly.
      _bind() {
        _turn "Use the dispatch_action tool with id \"aimail_address\" and args {\"action\":\"bind\",\"code\":\"$2\",\"email\":\"$1\",\"gateway_url\":\"$GWURL\"}. Copy the code character by character (it contains dashes). Do not ask questions; just dispatch it and report the result."
        # Bounded retry: a live model sometimes replies without dispatching (measured 2026-11-27,
        # where one such turn cost the whole host). The retry is recorded, never silent.
        if ! ls /app/.aimail/systems/*/*/agentmail.json >/dev/null 2>&1; then
          echo "RETRY: no binding after the first dispatch, asking once more" >> /tmp/turns.log
          _turn "Use the dispatch_action tool with id \"aimail_address\" and args {\"action\":\"bind\",\"code\":\"$2\",\"email\":\"$1\",\"gateway_url\":\"$GWURL\"}. Dispatch it now and report the result."
        fi
      }
      _bind "$AH" "$AC"
      python3 /opt/v2lib/dsh_state.py /app/.aimail count > /out/b1 2>/dev/null || echo 0 > /out/b1
      if [ -n "$SECOND" ]; then
        SADDR="${SECOND%%|*}"; SCODE="${SECOND##*|}"
        _bind "$SADDR" "$SCODE"
        python3 /opt/v2lib/dsh_state.py /app/.aimail count > /out/b2 2>/dev/null || echo 0 > /out/b2
      fi
      if [ "$LIFECYCLE" = "1" ]; then
        _turn "Use the dispatch_action tool with id \"aimail_address\" and args {\"action\":\"pause\",\"email\":\"$AH\"}. Copy the code character by character (it contains dashes). Do not ask questions; just dispatch it and report the result."
        python3 /opt/v2lib/dsh_state.py /app/.aimail paused > /out/paused 2>/dev/null || echo unknown > /out/paused
        _turn "Use the dispatch_action tool with id \"aimail_address\" and args {\"action\":\"unbind\",\"email\":\"$AH\"}. Copy the code character by character (it contains dashes). Do not ask questions; just dispatch it and report the result."
        python3 /opt/v2lib/dsh_state.py /app/.aimail count > /out/b3 2>/dev/null || echo 0 > /out/b3
      fi
      B=$(ls /app/.aimail/systems/*/*/agentmail.json 2>/dev/null | head -1)  # contract-allowed: fixture globs the SDK binding file; the repo contract is not mounted inside the host image
      [ -n "$B" ] && { install -m 644 "$B" /out/binding.json; stat -c %a "$B" > /out/mode; }
      python3 /opt/v2lib/dsh_state.py /app/.aimail count > /out/bindings_count 2>/dev/null || echo 0 > /out/bindings_count
      if [ -n "$B" ] && [ "$LIFECYCLE" != "1" ]; then
        pkill -f "openclaw gateway" 2>/dev/null; sleep 4
        setsid nohup openclaw gateway >/tmp/gw2.log 2>&1 &
        # A fixed sleep is wrong here: this image takes ~96s to finish its gateway startup
        # (measured 2026-11-27: startup-maintenance total=96064ms), so 25s made the arming line
        # look absent and S2/S3/S4-reconcile went RED for a host that arms fine. Wait for the
        # plugin line itself, bounded.
        i=0
        while [ $i -lt 120 ]; do
          grep -qaE "agent-aimail|aimail-pull" /tmp/gw2.log 2>/dev/null && break
          i=$((i+1)); sleep 2
        done
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
      # the plugin keeps a per-agent JSONL (core/log.ts) that records, per dispatch,
      # sessionKeyRequested + the sessionKey it asked the host for — without it, "did the plugin
      # even ask for session reuse?" is unanswerable from the outside.
      # core/log.ts writes to $AIMAIL_HOME/systems/<system>/<addr>/agentmail.log (not a "logs" dir —
      # my first attempt copied a path that never exists and silently produced an empty directory)
      mkdir -p /out/aimail-logs 2>/dev/null || true
      find /app/.aimail/systems -name 'agentmail.log' -exec cp {} /out/aimail-logs/ \; 2>/dev/null || true
      # The arming lines are written by the EMBEDDED agent turns (openclaw agent --local), not by
      # the gateway: measured 2026-11-27, rc held "[aimail-pull] system l2openc..." while gw2.log
      # did not exist. Grepping only the gateway logs made S2 flip green or red depending on which
      # turn happened to be the last one in /tmp/turn.log. Collect the turns instead.
      grep -a "agent-aimail\|aimail-pull\|polling enabled\|SSE reconcile" \
        /tmp/turns.log /tmp/gw.log /tmp/gw2.log > /out/host.log 2>/dev/null || true
      cp /tmp/turns.log /out/turns.log 2>/dev/null || true
      # Keep the gateway logs: without them a missing arming line cannot be classified as
      # product (plugin never armed) vs fixture (gateway never started). Measured 2026-11-27:
      # host.log held only the embedded-mode line and gw2.log was not preserved, so the S2 RED
      # was unattributable. NOTE: no apostrophes here -- this block is a single-quoted -lc body.
      cp /tmp/gw.log /out/gw.log 2>/dev/null || true
      cp /tmp/gw2.log /out/gw2.log 2>/dev/null || true
      tail -n 200 /tmp/gw.log /tmp/gw2.log >> /out/host.log 2>/dev/null || true
      # s6 (contract C8): openclaw keeps transcripts in sqlite, so the driver discovers the store
      # (session-named dirs + session tables) rather than guessing a path. Placed after the if/else
      # so it does not depend on which startup branch the adapter took (that cost dsh a run).
      if [ "${S6:-0}" = "1" ]; then
        i=0
        while [ ! -f /out/s6_go ] && [ $i -lt 300 ]; do i=$((i+1)); sleep 1; done
        python3 /opt/v2lib/s6_drive.py --addr "$AH" --smtp-port "$GWSMTP" \
          --db /gw/storage/aimail.db --out /out \
          --session-cmd "openclaw sessions --json --limit all 2>/dev/null" \
          --session-root /app --settle 25 >> /out/s6_drive.log 2>&1 || true
      fi
    ' >"$out/rc" 2>&1 &
  local pid=$!
  # 900s, not 600s: measured cold start for this image was ~15 min (gateway "http server listening" at
  # 10:29 for a 10:14 run). The old 10-minute window expired first, so `ready` was absent when the
  # whitelist block was evaluated and every later mail was rejected 550 "Sender not whitelisted".
  for i in $(seq 1 450); do [ -f "$out/ready" ] && break; sleep 2; done
  if [ -f "$out/ready" ] && [ "${LIFECYCLE:-0}" != "1" ]; then
    # One sender is not enough: the s6/s7/s8 drivers send as s6-a@/s6-b@/s6-reply@/
    # s7-a@/s7-reply@/s8-a@/s8-reply@. A single --sender left every driver mail bouncing
    # with 550 "Sender not whitelisted" (measured 2026-11-27), and repeating the flags in
    # ONE invocation does not help: argparse keeps only the last. Loop instead.
    : >"$out/whitelist.json"
    for _snd in ext-sender@example.com s6-a@example.com s6-b@example.com s6-reply@example.com s7-a@example.com s7-reply@example.com s8-a@example.com s8-reply@example.com; do
      python3 "$V2_ROOT/lib/gw.py" wl --gateway "$GW_URL" --key "$GW_ADMIN" \
        --identity "$GW_SID" --system "$GW_SID" --addr "$addr" --sender "$_snd" \
        >>"$out/whitelist.json" 2>&1 || true
    done
    # Same port fix as hosts/dsh.sh: the inbound listener is $((GW_SMTP + 1)), GW_SMTP is
    # the sink. See the comment there and REQ1-ingress-evidence.md §3.
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

  # The handover MUST obey the same gate as the whitelist above: with LIFECYCLE=1 (second boot) the
  # whitelist is skipped, and releasing s6 anyway sent three mails the gateway rejected with
  # "550 Sender not whitelisted" — which then looked like a C8 gap in the product.
  case " ${STAGES:-} " in
  *" s6 "*)
    if [ -f "$out/whitelist.json" ] && [ "${LIFECYCLE:-0}" != "1" ]; then
    : > "$out/s6_go"
    for _i in $(seq 1 900); do [ -f "$out/s6.json" ] && break; sleep 1; done
    [ -f "$out/s6.json" ] || echo "s6 driver produced no evidence (see s6_drive.log)" > "$out/s6_missing"
    fi
    ;;
  esac
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
host_s6_threading() { :; }   # evidence is produced inside host_journey (same container)
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
# capability-registered tools: no allow-list
c.setdefault("hooks", {}).update({"enabled": True, "token": "l2-hook-token"})
c.setdefault("gateway", {}).update({"mode": "local", "port": 39201, "auth": {"token": "l2-oc-token"}})
p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(c, indent=2))
PY
      TGZ="$(ls /opt/sdk/*.tgz | head -1)"
      openclaw plugins install "$TGZ" --force --accept-capabilities >/tmp/plugin.log 2>&1 || { echo "install failed"; tail -8 /tmp/plugin.log; exit 3; }
      openclaw plugins enable agent-aimail --accept-capabilities >/tmp/enable.log 2>&1 || true
      pkill -f "openclaw gateway" 2>/dev/null; sleep 2
      echo "boot: embedded agent mode (no gateway during turns)" >/tmp/gw.log
      python3 - <<PY
import json, urllib.request
body = json.dumps({"message": "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"addr-0000-deadbeef\",\"email\":\"" + __import__("os").environ["AH"] + "\",\"gateway_url\":\"" + __import__("os").environ["GWURL"] + "\"}. Copy the code character by character (it contains dashes). Do not ask questions; just call it and report the result."}).encode()
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

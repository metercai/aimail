#!/usr/bin/env bash
# dsh-turn.sh — drive ONE dsh agent turn from inside the host container.
#
# Why this exists: dsh has no `-p`-style single-shot entry. Its product surface is
# `dsh web` plus the session RPC the web UI uses, so a prompt-driven test has to
# speak that protocol (same steps as tests/l3/dsh-v2.sh, reduced to one turn):
#   start host -> token login -> session/create -> session/prompt -> wait for the turn to close
#
# Usage (inside the container): bash dsh-turn.sh <prompt-file> [port] [timeout-seconds]
# Prints the host log tail and the session transcript path; exit 0 = a turn ran.
set -uo pipefail
PROMPT_FILE="${1:?prompt file}"
PORT="${2:-39321}"
TMO="${3:-420}"
LOG=/tmp/dsh-host.log
COOKIES=/tmp/dsh-cookies.txt
WS=/tmp/ws
mkdir -p "$WS"; rm -f "$LOG" "$COOKIES"

export DSH_PERMISSION_MODE=danger-full-access   # product preset: approval=never
export AIMAIL_HOME="${AIMAIL_HOME:-/root/.aimail}"
export LLM_API_KEY="${LLM_API_KEY:-}"
export DEEPSEEK_API_KEY="${LLM_API_KEY:-}"      # the second LLM route reads this name

# 1. boot the web host; it prints a login URL carrying ?token=...
nohup dsh web --port "$PORT" --no-open > "$LOG" 2>&1 &
HOST_PID=$!
BASE="http://127.0.0.1:$PORT"
tok=""
for _ in $(seq 1 120); do
  tok="$(grep -o 'token=[A-Za-z0-9_-]*' "$LOG" 2>/dev/null | head -1 | cut -d= -f2 || true)"
  [ -n "$tok" ] && break
  kill -0 "$HOST_PID" 2>/dev/null || { echo "dsh host exited early"; tail -20 "$LOG"; exit 1; }
  sleep 0.5
done
[ -n "$tok" ] || { echo "no login token in 60s"; tail -20 "$LOG"; exit 1; }

code="$(curl -s -c "$COOKIES" -o /dev/null -w '%{http_code}' "$BASE/?token=$tok")"
[ "$code" = "303" ] || { echo "token login failed (http=$code)"; tail -20 "$LOG"; exit 1; }

rpc() { # $1=method $2=args-json
  curl -s -b "$COOKIES" -X POST "$BASE/api/$1" -H 'content-type: application/json' \
    -d "{\"type\":\"client-request\",\"rpcId\":\"rpc-$1-$(date +%s%N)\",\"method\":\"$1\",\"payload\":{\"args\":$2}}"
}

# 2. one session, one prompt
sid="$(rpc session/create "{\"request\":{\"cwd\":\"$WS\"}}" \
  | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["result"]["value"]["sessionId"])
except Exception: pass')"
[ -n "$sid" ] || { echo "session/create failed"; tail -20 "$LOG"; exit 1; }
echo "session=$sid"

req="$(python3 - "$sid" "$PROMPT_FILE" <<'PY'
import json, sys, time
sid, path = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
print(json.dumps({"requestId": f"req-{int(time.time()*1000)}", "sessionId": sid,
                  "mode": "queue", "content": [{"type": "text", "text": text}]}, ensure_ascii=False))
PY
)"
resp="$(curl -s -b "$COOKIES" -X POST "$BASE/api/session/prompt" -H 'content-type: application/json' \
  -d "{\"type\":\"client-request\",\"rpcId\":\"prompt-$(date +%s%N)\",\"method\":\"session/prompt\",\"payload\":{\"args\":{\"request\":$req}}}")"
case "$resp" in *'"accepted":true'*) ;; *) echo "prompt not accepted: $resp"; exit 1 ;; esac
echo "prompt accepted"

# 3. wait for the turn to actually START, then to close.
# A freshly queued prompt leaves turnBoundary empty, which reads as "closed" -- the naive check
# returns at once and the run looks like "the agent did nothing" (measured). So: start first
# (turns/llmMs/steps move), then wait for the boundary to close.
f="${DSH_HOME:-$HOME/.dsh}/storages/session_projcache/sessions/$sid.json"
probe() { # $1 = started|closed
python3 - "$f" "$1" <<'PYPROBE' 2>/dev/null
import json, sys
d = json.load(open(sys.argv[1]))
rows = d.get("record", {}).get("rows", {})
stats = rows.get("sessionStats", {}).get("val", {}) or {}
val = rows.get("turnBoundary", {}).get("val", {}) or {}
if sys.argv[2] == "started":
    sys.exit(0 if (int(stats.get("turns") or 0) >= 1 or int(stats.get("llmMs") or 0) > 0
                   or int(stats.get("steps") or 0) >= 1) else 1)
sys.exit(0 if val.get("openTurnStartSeq") is None and not (stats.get("pendingCalls") or {}) else 1)
PYPROBE
}
started=0
for _ in $(seq 1 90); do
  [ -f "$f" ] && probe started && { started=1; break; }
  kill -0 "$HOST_PID" 2>/dev/null || { echo "dsh host died"; tail -20 "$LOG"; exit 1; }
  sleep 1
done
[ "$started" = 1 ] && echo "turn_started=1" || { echo "turn_started=0 (prompt never picked up in 90s)"; tail -20 "$LOG"; exit 1; }

done_flag=0
for _ in $(seq 1 "$TMO"); do
  probe closed && { done_flag=1; break; }
  sleep 1
done
[ "$done_flag" = 1 ] && echo "turn_closed=1" || echo "turn_closed=0 (timeout ${TMO}s)"

# 4. leave the transcript for the harness (turnOutline holds the turn's steps)
if [ -f "$f" ]; then
  python3 - "$f" <<'PYTR' 2>/dev/null || true
import json, sys
d = json.load(open(sys.argv[1]))
rows = d["record"]["rows"]
out = rows.get("turnOutline", {}).get("val", {}) or {}
for t in (out.get("turns") or []):
    for st in (t.get("steps") or []):
        txt = st.get("text") or st.get("content") or ""
        if isinstance(txt, list):
            txt = " ".join(str(p.get("text", "")) for p in txt if isinstance(p, dict))
        if txt:
            print(str(txt)[:1500])
PYTR
  echo "--- transcript ---"
  tail -40 /tmp/dsh-transcript.txt 2>/dev/null || true
fi
exit 0

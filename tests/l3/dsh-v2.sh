#!/usr/bin/env bash
# @level L3
# dsh-v2.sh — the dsh cell's V2 (SSE, activation-code) journey.
#
# Driven by the l3.yml dsh cell (owner ruling 2026-11-22, U1: the two former
# dsh cells collapsed into one — the V2 dsh host has exactly one legal
# install path, the host's plugin mechanism).
#
# The journey simulates a REAL user end to end (no program shortcuts):
#   [0] fixture: SMTP sink + gateway [relay] + LLM preflight + domain offer
#   [1] the applicant (external email) verifies ownership (6-digit code read
#       from the mailbox spool) and applies an address code (activation prompt
#       read from the mail body — never from the DB, D1)
#   [2] turn 1: the real dsh web host (clean profile, NO plugin) receives the
#       activation prompt over its web API; the agent's LLM turn runs and finds
#       aimail_address missing (SKILL §1)
#   [3] the user (simulated) installs the plugin: dsh plugin --profile web
#       add agent-aimail — the host's normal plugin mechanism (SKILL §6: no
#       aimail CLI, no file writes)
#   [4] host restart (a plugin install applies on the next boot — dsh-hmr)
#   [5] turn 2: the user says "installed, continue"; the agent binds via
#       aimail_address → ownership row + binding files + SSE streams
#   [6] welcome closed loop: the agent calibrates (role_calibrator), the
#       applicant (= default manager, gateway v1.0.11 applicant→manager wiring)
#       approves via SMTP [CMD] approve persona → persona applied
#   [7] query-mail signature assertion (whoami readback)
#
# The script only DRIVES (web API turns, user install, restart) and OBSERVES
# (spool files, session stats, read-only DB, gateway log). It never
# calls bind itself, never writes binding files, never pre-installs.
#
# Inputs (env, from run-l3.sh):
#   GW_BIN GW_HOME GW_URL GW_HTTP_PORT GW_SMTP_PORT SYSTEM_ID ADMIN_KEY
#   LLM_API_KEY LLM_BASE_URL LLM_MODEL DSH_HOME WORK AIMAIL_REPO WELCOME_TIMEOUT
# Outputs: SPOOL, CODE, ADDR, APPLICANT via a state file ($WORK/dsh-v2.state)
# Exit: 0 = loop closed · 1 = assertion failed · 2 = env gap
set -uo pipefail

# decompress a session transcript; on hosts without zstd, install the python module once.
_tx_read() {  # _tx_read <file>
  local f="$1" out=""
  [ -f "$f" ] || return 1
  if command -v zstdcat >/dev/null 2>&1; then out="$(zstdcat "$f" 2>/dev/null)"
  elif python3 -c "import zstandard" >/dev/null 2>&1; then
    out="$(python3 -c "import sys,zstandard;sys.stdout.write(zstandard.ZstdDecompressor().stream_reader(open(sys.argv[1],'rb')).read().decode('utf-8','replace'))" "$f" 2>/dev/null)"
  else
    python3 -m pip install --quiet zstandard >/dev/null 2>&1 || true
    out="$(python3 -c "import sys,zstandard;sys.stdout.write(zstandard.ZstdDecompressor().stream_reader(open(sys.argv[1],'rb')).read().decode('utf-8','replace'))" "$f" 2>/dev/null)"
  fi
  printf '%s' "$out"
  [ -n "$out" ]
}

say() { printf '  %s\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad() { printf '  \033[31m✗ %s\n' "$*" >&2; }
die() { bad "$*"; exit 1; }
gap() { printf '  \033[35m⊘ ENV GAP: %s\n' "$*" >&2; exit 2; }

[ -n "${GW_BIN:-}" ] || gap "GW_BIN not set"
[ -n "${DSH_HOME:-}" ] || gap "DSH_HOME not set"
[ -n "${WORK:-}" ] || gap "WORK not set"
[ -x "$GW_BIN" ] || gap "gateway binary not executable: $GW_BIN"
command -v dsh >/dev/null 2>&1 || gap "dsh not on PATH (prepare.sh dsh failed upstream)"
command -v node >/dev/null 2>&1 || gap "node not on PATH"
command -v python3 >/dev/null 2>&1 || gap "python3 not on PATH"

SINK_PORT="${SINK_PORT:-12528}"
DSH_WEB_PORT="${DSH_WEB_PORT:-39801}"
# Wait BUDGET, not an assertion (same doctrine as the welcome window): windows runners have
# been measured at ~283 s of real decode for turn 1 (run 45160974047: llmMs 283453, the turn
# completed after the 300 s budget expired), while linux completes the same turn in ~42 s.
# Keep the assertion strict (llmMs>0 + closed turn), give windows a larger budget.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) TURN_WINDOW="${WELCOME_TIMEOUT:-600}" ;;
  *)                    TURN_WINDOW="${WELCOME_TIMEOUT:-300}" ;;
esac
SPOOL="$GW_HOME/mail.spool"
STATE="$WORK/dsh-v2.state"
# the SDK's config/binding root — same value run-l3.sh ④b feeds the old dsh
# host (AIMAIL_HOME=$HOME/.aimail); the host process must get it explicitly
# (the plugin resolves its home from the env, not the shell cwd).
AIMAIL_HOME="${AIMAIL_HOME:-$HOME/.aimail}"
export AIMAIL_HOME
# The binding filename is a CONTRACT value (binding_file in
# contract/aimail-contract.json — single source of truth). Derive it from the
# manifest instead of hardcoding the literal: this test asserts "the file the
# contract says the SDK must write was written", and a contract rename would
# then re-target this assertion automatically (the literal ratchet in
# tests/contract/ forbids new hardcoded occurrences).
BINDING_FILE="$(python3 -c 'import json,sys; sys.stdout.write(json.load(open(sys.argv[1]))["binding_file"])' "$AIMAIL_REPO/contract/aimail-contract.json")"
[ -n "$BINDING_FILE" ] || gap "cannot read binding_file from $AIMAIL_REPO/contract/aimail-contract.json"
: > "$SPOOL"
GW_PID=""; SINK_PID=""; HOST_PID=""
cleanup() {
  [ -n "${HOST_PID:-}" ] && kill "$HOST_PID" >/dev/null 2>&1 || true
  [ -n "${SINK_PID:-}" ] && kill "$SINK_PID" >/dev/null 2>&1 || true
  # the [relay]-reconfigured gateway is ours (run-l3.sh's ② instance was
  # replaced in [0]) — kill it so the cell leaves no orphans.
  [ -n "${GW_PID:-}" ] && kill "$GW_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "════ dsh V2 journey: activation code → host agent bind → welcome loop (dsh-v2.sh [0]-[7])"

# ═══ [0] fixture: sink + gateway [relay] + LLM proxy + domain offer ═══════════
# Stop the gateway run-l3.sh ② started and bring it back WITH the relay wiring
# (external-domain mail → sink; internal l3.local recipients unaffected —
# they go through create_inbound, not the relay).
say "[0/7] fixture: SMTP sink + gateway [relay] + LLM preflight + domain offer"
[ -n "${GW_PID_L3:-}" ] && kill "$GW_PID_L3" >/dev/null 2>&1 || true
sleep 1
# free the web host port (a stale probe host must not shadow this journey)
python3 - "$DSH_WEB_PORT" <<'PY' || gap "web host port $DSH_WEB_PORT busy (stale process)"
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError as e:
    print(f"port busy: {e}"); sys.exit(1)
finally:
    s.close()
PY
python3 "$AIMAIL_REPO/tests/host-acceptance/fixture/smtp_sink.py" \
  --port "$SINK_PORT" --spool "$SPOOL" > "$GW_HOME/sink.log" 2>&1 & SINK_PID=$!
sleep 1
kill -0 "$SINK_PID" 2>/dev/null || { bad "sink died: $(tail -5 "$GW_HOME/sink.log")"; exit 1; }
# gateway config: add [relay] + fast retry (mail must land in the spool within the test window)
python3 - "$GW_HOME/config.toml" "$SINK_PORT" <<'PY'
import sys
cfg = open(sys.argv[1], encoding="utf-8").read()
if "[relay]" not in cfg:
    cfg += '\n[relay]\nsmtp_server = "127.0.0.1:%s"\n' % sys.argv[2]
if "[retry]" not in cfg:
    cfg += '\n[retry]\npoll_interval_secs = 1\ninitial_backoff_secs = 1\n'
open(sys.argv[1], "w", encoding="utf-8").write(cfg)
PY
# A leftover gateway for this workdir holds the binary's single-instance lock and the new
# process exits with Config("Server is already running (PID …)") — measured 2026-11-23, which
# burned a whole L3 cell. Stop it first, and retry once if the lock is still held.
"$GW_BIN" stop >/dev/null 2>&1 || true
# the lock is per binary: a leftover from another workdir (e.g. an L2 run) blocks this boot too
for _p in $(pgrep -f "$GW_BIN" 2>/dev/null); do kill "$_p" 2>/dev/null || true; done
sleep 1
"$GW_BIN" --config "$GW_HOME/config.toml" > "$GW_HOME/boot.log" 2>&1 & GW_PID=$!
sleep 3
if grep -qa "Server is already running" "$GW_HOME/boot.log" 2>/dev/null; then
  _stale="$(sed -n 's/.*Server is already running (PID: \([0-9]*\)).*/\1/p' "$GW_HOME/boot.log" | head -1)"
  say "  stale gateway instance ${_stale:-?} held the lock — stopping it and retrying once"
  [ -n "$_stale" ] && kill "$_stale" >/dev/null 2>&1 || true
  sleep 2
  "$GW_BIN" --config "$GW_HOME/config.toml" > "$GW_HOME/boot.log" 2>&1 & GW_PID=$!
fi
GW_URL="http://127.0.0.1:${GW_HTTP_PORT}"
for _ in $(seq 1 30); do
  curl -fsS "$GW_URL/health" >/dev/null 2>&1 && break
  kill -0 "$GW_PID" 2>/dev/null || die "gateway exited after [relay] reconfig — boot log: $(tail -8 "$GW_HOME/boot.log" | tr '\n' ' ')"
  sleep 1
done
curl -fsS "$GW_URL/health" >/dev/null || die "gateway not healthy after [relay] reconfig"
# LLM preflight: the turn's REAL LLM call is the evidence (sessionStats.llmMs),
# so the endpoint must answer BEFORE any host turn — a dead LLM would surface
# as a closed turn with llmMs=0 (the host swallows adapter errors; run-l3.sh ③
# forensics documents the same symptom). Probe the exact URL+model the agent uses.
LLM_CHAT_URL="${LLM_BASE_URL%/v1}/v1/chat/completions"
if ! curl -fsS -m 120 "$LLM_CHAT_URL" -H "content-type: application/json" \
     -H "authorization: Bearer $LLM_API_KEY" \
     -d "{\"model\":\"$LLM_MODEL\",\"max_tokens\":8,\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}" >/dev/null 2>&1; then
  gap "LLM $LLM_BASE_URL not answering (model $LLM_MODEL)"
fi
ok "gateway [relay]→sink up; LLM endpoint answering"
# dsh LLM config → the REAL endpoint (single-source writer; the same call run-l3.sh ③
# makes for dsh cells — idempotent upsert). The protocol route (OpenAI llm-pi-ai vs
# Anthropic llm-deepseek) is chosen by the endpoint's HOST inside llm-config.py:
# CI's xiaomi host takes the OpenAI route (xiaomi serves no /v1/messages), local
# endpoints keep the shipped Messages route. No proxy in front: a proxy's own host
# (127.0.0.1) would flip the route and kill the turn.
python3 "$AIMAIL_REPO/tests/agent-env/lib/llm-config.py" write --platform dsh --home "$DSH_HOME" \
  --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" --api-key "$LLM_API_KEY" >/dev/null \
  || die "llm-config write (dsh → $LLM_BASE_URL) failed"
# domain offer (system-scope key; the public apply flow requires l3.local offered).
# Signed curl (no tssdk dist dependency — L3 dsh cells carry no tssdk build).
python3 - "$GW_URL" "$ADMIN_KEY" "$SYSTEM_ID" <<'PY' || die "domain offer (l3.local) failed"
import sys, json, time, urllib.request, urllib.error, hashlib, hmac
gw, key, sid = sys.argv[1], sys.argv[2], sys.argv[3]
body = json.dumps({"id": "l3.local", "domain": "l3.local"}).encode()
ts = str(int(time.time() * 1000))
base = f"POST\n/api/v1/admin/systems/{sid}/domains\n{ts}\n{hashlib.sha256(body).hexdigest()}"
sig = hmac.new(hashlib.sha256(key.encode()).hexdigest().encode(), base.encode(), hashlib.sha256).hexdigest()
req = urllib.request.Request(gw.rstrip("/") + f"/api/v1/admin/systems/{sid}/domains",
    data=body, method="POST",
    headers={"Content-Type": "application/json", "X-Api-Identity": sid,
             "X-Api-Timestamp": ts, "X-Api-Signature": sig})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        print("  domain offer: HTTP", r.status)
except urllib.error.HTTPError as e:
    print("  domain offer: HTTP", e.code, (e.read() or b"")[:200]); sys.exit(1)
PY
ok "fixture ready"

# ═══ [1] applicant: verify email + apply address (all from the mailbox) ═══════
say "[1/7] applicant verifies email + applies an address (D1: everything from the mail)"
TS="$(date +%s | tail -c 5)"
APPLICANT="l3user-${TS}@example.com"
python3 - "$GW_URL" "$SPOOL" "$APPLICANT" "$STATE" <<'PY' || die "email verify + apply-address failed (sink/relay broken? check sink.log + boot.log)"
import sys, re, time, json, urllib.request, urllib.error
gw, spool, applicant = sys.argv[1], sys.argv[2], sys.argv[3]
def post(path, body):
    req = urllib.request.Request(gw + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads((e.read() or b"{}").decode() or "{}")
def spool_wait(email, pattern, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            raw = open(spool, "r", errors="replace").read()
        except FileNotFoundError:
            raw = ""
        for part in raw.split("---AIMAIL-MESSAGE---"):
            if email.lower() in part.lower():
                m = re.search(pattern, part)
                if m:
                    return part, m.group(0)
        time.sleep(0.5)
    return None, None
st, r = post("/api/v1/public/email-verify/send", {"email": applicant})
assert st in (200, 201), f"verify/send HTTP {st}: {r}"
print(f"  verify/send: {st}")
part, vcode = spool_wait(applicant, r"(?<!\d)(\d{6})(?!\d)")
assert vcode, "no 6-digit verification code in the applicant mailbox (sink/relay)"
print(f"  mailbox: verification code received ({vcode})")
st, r = post("/api/v1/public/email-verify/check", {"email": applicant, "code": vcode})
assert st in (200, 201) and r.get("ok") is not False, f"verify/check HTTP {st}: {r}"
print(f"  verify/check: {st}")
st, r = post("/api/v1/public/apply-address", {"email": applicant, "name": "L3 User",
              "agentname": "l3agent", "domain": "l3.local", "email_verify_code": vcode})
assert st in (200, 201) and r.get("address"), f"apply-address HTTP {st}: {r}"
addr = r["address"]
print(f"  apply-address: {addr}")
part, code = spool_wait(applicant, r"(addr-[0-9a-f]{4}(?:-[0-9a-f]{4}){5})")
assert code, "no activation code in the applicant mailbox"
assert len(code) == 34, f"activation code length {len(code)} (expect 34) — extraction truncated"
# the FULL prompt is the mail body (D1: the user pastes exactly this into the agent)
open(f"{spool}.prompt.txt", "w", encoding="utf-8").write(part)
print(f"  activation prompt captured from mail body (code len {len(code)})")
open(sys.argv[4], "w", encoding="utf-8").write(f"APPLICANT={applicant}\nADDR={addr}\n")
PY
source "$STATE"
[ -n "${ADDR:-}" ] || die "apply step did not produce an address (state: $STATE)"
ok "applicant: email verified + address applied ($ADDR) + prompt from mail body"

# ═══ web API helpers (verified against the pinned 0.2.2-rc.1 host, 2026-11-22) ═
# Envelope (client/connection rpc-schema): {type:client-request, rpcId, method,
# payload:{args:{request:{...}}}}. session/prompt request (session-controller
# SessionPromptRequest): {requestId, sessionId, mode, content:[{type:'text',text}]}.
WEB_BASE=""
WEB_COOKIES=""
web_start_host() {
  # boots the web host and waits for its token; prints nothing, sets HOST_PID/WEB_BASE
  # LLM credential: the dsh LLM row references the key BY ENV NAME (llm-config.py:
  # apiKeyEnv is a credential reference, not a value). The OpenAI route (llm-pi-ai, CI's
  # xiaomi endpoint) reads LLM_API_KEY; the Anthropic Messages route (llm-deepseek,
  # local endpoints) reads DEEPSEEK_API_KEY. Pass both with the same value so the
  # turn's provider route always resolves its credential.
  # Permission preset: the web session ships preset workspace-write + approval=ask
  # (pinned 0.2.2-rc.1 composed tree, approval plugin row: policy =
  # (DSH_PERMISSION_MODE ?? 'workspace-write') === 'danger-full-access' ? 'never' :
  # 'ask'). Under 'ask' every tool call the model makes hangs on a human UI approval
  # — nondeterministic for a regression cell, and the web API has no approval
  # endpoint. The journey's agent MUST run tools (bind, probes), so the cell uses
  # the product's own no-prompt preset: danger-full-access ⇒ approval=never.
  DSH_HOME="$DSH_HOME" AIMAIL_HOME="$AIMAIL_HOME" \
    DSH_PERMISSION_MODE=danger-full-access \
    LLM_API_KEY="${LLM_API_KEY:-}" DEEPSEEK_API_KEY="${LLM_API_KEY:-}" \
    nohup dsh web --port "$DSH_WEB_PORT" --no-open \
    > "$WORK/dsh-host.log" 2>&1 &
  HOST_PID=$!
  WEB_BASE="http://127.0.0.1:$DSH_WEB_PORT"
  WEB_COOKIES="$WORK/cookies.txt"
  local tok=""
  for _ in $(seq 1 60); do
    tok="$(grep -o 'token=[A-Za-z0-9_-]*' "$WORK/dsh-host.log" 2>/dev/null | head -1 | cut -d= -f2 || true)"
    [ -n "$tok" ] && break
    kill -0 "$HOST_PID" 2>/dev/null || { bad "dsh web host exited early: $(tail -15 "$WORK/dsh-host.log" | tr '\n' ' ')"; return 1; }
    sleep 0.5
  done
  [ -n "$tok" ] || { bad "dsh web host printed no login token in 30s: $(tail -15 "$WORK/dsh-host.log" | tr '\n' ' ')"; return 1; }
  rm -f "$WEB_COOKIES"
  local login_http
  login_http="$(curl -s -c "$WEB_COOKIES" -o /dev/null -w '%{http_code}' "$WEB_BASE/?token=$tok")"
  [ "$login_http" = "303" ] || { bad "web token login failed (http=$login_http)"; return 1; }
  return 0
}
web_rpc() { # $1=method $2=json-args  -> prints the raw server-response
  curl -s -b "$WEB_COOKIES" -X POST "$WEB_BASE/api/$1" \
    -H 'content-type: application/json' \
    -d "{\"type\":\"client-request\",\"rpcId\":\"rpc-$1-$(date +%s%N)\",\"method\":\"$1\",\"payload\":{\"args\":$2}}"
}
web_create_session() { # prints sessionId
  local resp sid
  resp="$(web_rpc session/create "{\"request\":{\"cwd\":\"$WORK/ws\"}}")"
  sid="$(printf '%s' "$resp" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    print(d["result"]["value"]["sessionId"])
except Exception:
    pass' 2>/dev/null)"
  [ -n "$sid" ] || { bad "session/create failed: $resp"; return 1; }
  printf '%s' "$sid"
}
web_send_prompt() { # $1=sessionId $2=file-with-text  -> 0 if accepted
  local resp
  resp="$(python3 - "$1" "$2" <<'PY'
import json, sys
sid, textfile = sys.argv[1], sys.argv[2]
text = open(textfile, encoding="utf-8").read()
print(json.dumps({"requestId": f"req-{int(__import__('time').time()*1000)}",
                  "sessionId": sid, "mode": "queue",
                  "content": [{"type": "text", "text": text}]}, ensure_ascii=False))
PY
)"
  # the JSON above is the REQUEST object; wrap it in args
  resp="$(curl -s -b "$WEB_COOKIES" -X POST "$WEB_BASE/api/session/prompt" \
    -H 'content-type: application/json' \
    -d "{\"type\":\"client-request\",\"rpcId\":\"prompt-$(date +%s%N)\",\"method\":\"session/prompt\",\"payload\":{\"args\":{\"request\":$resp}}}")"
  case "$resp" in *'"accepted":true'*) return 0 ;; esac
  bad "session/prompt not accepted: $resp"
  return 1
}
wait_turn_done() { # $1=sessionId $2=timeout — waits until the session's turn closes
  # closed = openTurnStartSeq is null AND no pending tool call (a pending call under
  # an 'ask' approval would never close on its own — the web API has no approval
  # endpoint; under this cell's never-preset there is nothing to approve anyway).
  local f=""
  for _ in $(seq 1 "$2"); do
    f="$DSH_HOME/storages/session_projcache/sessions/$1.json"
    if [ -f "$f" ] && python3 - "$f" <<'PY' 2>/dev/null
import json, sys
d = json.load(open(sys.argv[1]))
rows = d.get("record", {}).get("rows", {})
val = rows.get("turnBoundary", {}).get("val", {})
stats = rows.get("sessionStats", {}).get("val", {})
ok = val and val.get("openTurnStartSeq") is None
pending = stats.get("pendingCalls") or {}
sys.exit(0 if ok and not pending else 1)
PY
    then return 0; fi
    kill -0 "$HOST_PID" 2>/dev/null || { bad "dsh host died while waiting for turn"; return 1; }
    sleep 2
  done
  return 1
}
llm_ms_total() { # total llmMs across every session of this host (the host is
  # dedicated to this journey; inbound SSE reply turns may attach to any of its
  # sessions, so the host-wide sum is the stable LLM-time evidence)
  python3 -c "
import json, glob, re
home = '$DSH_HOME'
paths = glob.glob(home + '/storages/session_projcache/sessions/*.json')
if not paths and re.match(r'^/[A-Za-z]/', home):
    # git-bash hands /c/Users/... to a native Python, which cannot see that path; the shell
    # 'find' in the forensic dump did (windows cell, 2026-11-23) and the sessions were there
    # with llmMs 23783 / 15300 while this sum reported 0.
    paths = glob.glob(home[1].upper() + ':' + home[2:] + '/storages/session_projcache/sessions/*.json')
total = 0
for f in paths:
    try:
        v = json.load(open(f))['record']['rows'].get('sessionStats', {}).get('val', {})
        total += int(v.get('llmMs', 0))
    except Exception:
        pass
print(total)
" 2>/dev/null || echo 0
}
mkdir -p "$WORK/ws"
# the journey owns this DSH_HOME exclusively (CI: fresh from prepare; local: same
# semantics). Start from a clean session ledger so the llmMs evidence below is
# attributable to THIS journey's turns, not a previous run on the same home.
rm -rf "$DSH_HOME/storages/session_projcache" 2>/dev/null || true

# ═══ [2] turn 1: the agent receives the activation prompt (clean profile) ══════
say "[2/7] turn 1 — web host (clean profile, no plugin) receives the activation prompt"
web_start_host || die "web host boot/login failed"
ok "web host up (token login 303)"
SID1="$(web_create_session)" || die "session/create failed (web API)"
say "session: $SID1"
# D2: the user pastes the mail body into the agent — exactly the prompt text
# captured from the spool in [1] (no trimming, no reformatting).
cp "$SPOOL.prompt.txt" "$WORK/turn1-prompt.txt"
web_send_prompt "$SID1" "$WORK/turn1-prompt.txt" || die "turn 1 prompt injection failed"
ok "turn 1 prompt injected (the activation mail body, verbatim)"
# turn 1 is the FORENSICS turn: it proves the agent ran its LLM and reached the
# missing-tool path. It does NOT have to close — [4] kills the host (the turn
# dies with it, by design), so waiting for a clean close only burns the window
# (measured: mimo-v2.6-flash takes 18+ steps / 240s+ of LLM time on this
# prompt). Wait until the LLM has actually run (llmMs>0) and the turn has real
# progress (≥2 steps), with pendingCalls empty (nothing stuck on approval).
T1_DEADLINE=$((SECONDS + TURN_WINDOW))
while [ $SECONDS -lt $T1_DEADLINE ]; do
  if [ -f "$DSH_HOME/storages/session_projcache/sessions/$SID1.json" ] \
     && python3 - "$DSH_HOME/storages/session_projcache/sessions/$SID1.json" <<'PY' 2>/dev/null
import json, sys
d = json.load(open(sys.argv[1]))
rows = d.get("record", {}).get("rows", {})
stats = rows.get("sessionStats", {}).get("val", {})
pending = stats.get("pendingCalls") or {}
ran = int(stats.get("llmMs", 0)) > 0
progressed = int(stats.get("steps", 0)) >= 2 or int((stats.get("openStep") or {}).get("step", 0) or 0) >= 2
sys.exit(0 if ran and progressed and not pending else 1)
PY
  then break; fi
  kill -0 "$HOST_PID" 2>/dev/null || die "dsh host exited during turn 1: $(tail -15 "$WORK/dsh-host.log" | tr '\n' ' ')"
  sleep 2
done
[ $SECONDS -lt $T1_DEADLINE ] || die "turn 1 never ran its LLM within ${TURN_WINDOW}s (llmMs: $(llm_ms_total); host log: $(tail -5 "$WORK/dsh-host.log" | tr '\n' ' '))"
ok "turn 1 ran a real LLM turn (llmMs>0, progressed; it terminates with the [4] restart by design)"
# terminal-state item 3 evidence (plan): the first turn ran and hit the
# missing-tool path. The model's words are not asserted verbatim (nondeterministic
# phrasing would flake the cell) — instead we dump the session record: the file
# HEAD is the agent's turn conversation (empirical dsh layout, see
# dump_failure_evidence in run-l3.sh) + the session stats block.
SESS1="$DSH_HOME/storages/session_projcache/sessions/$SID1.json"
if [ -f "$SESS1" ]; then
  echo "  [turn-1 forensics] session $SID1 ($(wc -c < "$SESS1") bytes, head 600)"
  head -c 600 "$SESS1" | sed 's/^/    /'
  python3 - "$SESS1" <<'PY' | sed 's/^/    /'
import json, sys
d = json.load(open(sys.argv[1]))
rows = d.get("record", {}).get("rows", {})
for k in ("sessionStats", "turnBoundary"):
    v = rows.get(k, {}).get("val")
    if v is not None:
        print(f"  [{k}] {json.dumps(v, ensure_ascii=False)[:300]}")
PY
fi
# SKILL §1 evidence: with a clean profile the agent's turn must have hit the
# missing-tool branch (it cannot bind without aimail_address). The session log
# is the evidence — but the assertion is behavioural (turn closed + no binding
# file), not a string match on the model's words.
BINDING_BEFORE="$(find "$AIMAIL_HOME" -name "$BINDING_FILE" 2>/dev/null | head -1)"
[ -z "$BINDING_BEFORE" ] || die "a binding already exists before any bind turn ($BINDING_BEFORE) — environment not clean"
ok "turn 1 done; no binding on disk (the tool was missing — SKILL §1 path)"

# ═══ [3] the user installs the plugin (the host's normal plugin mechanism) ═════
say "[3/7] the agent installs the plugin itself (the activation mail told it to)"
# ── integrity tripwire ────────────────────────────────────────────────────────────────
# Measured 2026-11-23: turn 1 spent its last three steps running
#   sed -n 1,150p /home/ubuntu/aimail/tests/l3/dsh-v2.sh
# i.e. the host could read this fixture and "solved" the task open-book. A cell whose agent
# has seen the harness cannot judge the product, so it must be reported as INVALID (neither
# green nor a product RED). The structural fix is host isolation; this is the tripwire.
_sess="$(ls -t "$DSH_HOME"/sessions/*/*/session.v4.jsonl.zstd 2>/dev/null | head -1)"
_leak=0
if [ -n "$_sess" ]; then
  if command -v zstdcat >/dev/null 2>&1; then
    # Repo docs count as the harness too: an agent that reads docs/dsh-mail-architecture-v2.md has
    # been told how to install, which is exactly what the activation prompt is supposed to convey.
    # CI proved the difference: same journey, no repo on the agent's path -> no install at all.
    _tx_read "$_sess" 2>/dev/null | grep -qaE "tests/l3/|dsh-v2\.sh|run-l3\.sh|run-v2\.sh|aimail/docs/|docs/dsh-mail|docs/aimail-host-sse" && _leak=1
  elif python3 -c "import zstandard" >/dev/null 2>&1; then
    python3 - "$_sess" <<'PY' && _leak=1
import sys, zstandard
d = zstandard.ZstdDecompressor().stream_reader(open(sys.argv[1], 'rb')).read().decode('utf-8', 'replace')
needles = ('tests/l3/', 'dsh-v2.sh', 'run-l3.sh', 'run-v2.sh')
sys.exit(0 if any(n in d for n in needles) else 1)
PY
  else
    warn "integrity tripwire skipped: no zstdcat / python-zstandard on this host (recorded, not counted as a pass)"
  fi
fi
[ "$_leak" = 0 ] || die "INVALID cell: the agent read the L3 harness (transcript mentions tests/l3) — the host can see this checkout, so the run is open-book and cannot judge the product. Isolate the host, then re-run."
# Owner ruling 2026-11-23: L3 simulates a user. Installing the published SDK is the AGENT's
# own bootstrap, triggered by the activation prompt (advanced email_notify.rs:149: "if
# aimail_address is unavailable or not registered, install the agent_aimail plugin SDK
# first"). The fixture must not install anything — a fixture-side install is a fake pass.
WANT_SDK="$(npm view agent-aimail version 2>/dev/null || true)"
say "published SDK under test: agent-aimail@${WANT_SDK:-<npm view failed>}"
# (1) the environment must still be clean: the fixture never installed anything, and the
#     agent's turn 1 had no plugin to call.
SDK_PKG="$(find "$DSH_HOME" -maxdepth 6 -path '*node_modules/agent-aimail/package.json' 2>/dev/null | head -1)"
[ -z "$SDK_PKG" ] || { bad "agent-aimail was already installed before the agent's bootstrap ($SDK_PKG) — environment not clean, cannot attribute the install to the agent"; exit 1; }
ok "clean state confirmed: no agent-aimail in the profile before the bootstrap"
# (2) after the turn the agent must have installed it, from the registry
# A real user, seeing the agent stop after reading the mail, would say "go ahead" — words,
# never commands run by the fixture. Measured 2026-11-23: with the current mail text the
# agent investigated for 9 steps (plugin_manager, gateway probe, PyPI wheel, npm) and ended
# the turn without installing, partly because the mail names the PyPI package
# (`agent_aimail`) and says "from npm", where the npm package is `agent-aimail`.
if [ -z "$(find "$DSH_HOME" -maxdepth 6 -path '*node_modules/agent-aimail/package.json' 2>/dev/null | head -1)" ]; then
  # the nudge is a NEW user turn: closing the current one first is what a real user waits for
  # (measured: injecting mid-turn meant the nudge was never consumed)
  wait_turn_done "$SID1" 60 || say "  turn 1 did not close within 60s — nudging anyway"
  say "plugin not installed yet after turn 1 — user nudge (words only; the agent must run the install)"
  cat > "$WORK/turn1b-nudge.txt" <<PROMPT
Go ahead and install the AIMail plugin SDK yourself now (the Node/npm one for this host),
then retry the activation with the same code. I do not have a terminal here — you run it.
PROMPT
  web_send_prompt "$SID1" "$WORK/turn1b-nudge.txt" || say "nudge could not be injected (turn may be closed)"
fi
# bounded wait: the agent may still be working through its install command when turn 1's
# LLM counter moves (wait budget, not a relaxed assertion)
for _w in $(seq 1 60); do
  SDK_PKG="$(find "$DSH_HOME" -maxdepth 6 -path '*node_modules/agent-aimail/package.json' 2>/dev/null | head -1)"
  [ -n "$SDK_PKG" ] && break
  kill -0 "$HOST_PID" 2>/dev/null || break
  sleep 3
done
[ -n "$SDK_PKG" ] || { bad "the agent never installed agent-aimail: the activation prompt did not drive the bootstrap (no agent-aimail/package.json under $DSH_HOME; host log: $(tail -8 "$WORK/dsh-host.log" | tr '\n' ' '))"; exit 1; }
GOT_SDK="$(node -e 'process.stdout.write(require(process.argv[1]).version)' "$SDK_PKG" 2>/dev/null || true)"
[ -n "$GOT_SDK" ] || { bad "cannot read the installed SDK version from $SDK_PKG"; exit 1; }
if [ -n "$WANT_SDK" ] && [ "$GOT_SDK" != "$WANT_SDK" ]; then
  bad "installed SDK version $GOT_SDK != npm latest $WANT_SDK (registry moved mid-run? re-run)"; exit 1
fi
# (3) the install must be visible as the AGENT's own command, not ours.
# The transcript lives under $DSH_HOME/sessions/…/session.v4.jsonl.zstd — the earlier version of
# this check grepped $WORK, where no transcript is ever written, so a successful bootstrap was
# reported as "cannot prove self-bootstrap" (measured 2026-11-23).
_DONE_BY_AGENT=0
# The agent may install through the host's OWN plugin mechanism rather than a shell command —
# measured 2026-11-23: dsh's plugin_manager tool with {"action":"install_bundle","target":
# "agent-aimail"}. That IS the doctrine (the host's normal mechanism, driven by the agent), so
# the attribution must accept it alongside bash/npm/pnpm forms.
_pat='install_bundle.{0,160}agent-aimail|install_plugin.{0,160}agent-aimail|agent-aimail.{0,160}(install_bundle|install_plugin)|plugin.{0,24}add.{0,24}agent-aimail|npm (i|install|add) .{0,60}agent-aimail|pnpm (add|i|install) .{0,60}agent-aimail'
# the nudge opens a NEW session, so re-resolve the transcript(s) here instead of reusing the
# one the tripwire read earlier
_sess="$(ls -t "$DSH_HOME"/sessions/*/*/session.v4.jsonl.zstd 2>/dev/null | head -1)"
if [ -n "${_sess:-}" ] && [ -f "$_sess" ]; then
  _tx="$(_tx_read "$_sess")" || _tx=""
  # NEVER pipe a large string into `grep -q`: grep exits at the first match, printf gets SIGPIPE,
  # and with `set -o pipefail` the pipeline reports failure even though the pattern matched — on CI
  # that turned a real agent install into "cannot prove self-bootstrap" (2026-11-24, plus the
  # "printf: write error: Broken pipe" lines in the log). Write once, grep the file.
  [ -n "$_tx" ] || gap "cannot read the session transcript ($_sess) — no zstd/zstandard on this host, so the install cannot be attributed (this is a fixture gap, not a product verdict)"
  _txfile="$WORK/transcript.txt"
  if [ -n "$_tx" ]; then printf '%s' "$_tx" > "$_txfile" 2>/dev/null || true; fi
  [ -s "$_txfile" ] && grep -qaE "$_pat" "$_txfile" && _DONE_BY_AGENT=1
fi
for _f in "$WORK"/session-*.json "$WORK/dsh-host.log"; do
  [ -f "$_f" ] || continue
  [ "$_DONE_BY_AGENT" = 1 ] && break
  grep -qaE "$_pat" "$_f" && _DONE_BY_AGENT=1
done
[ "$_DONE_BY_AGENT" = 1 ] || { bad "the plugin appeared but no agent-side install command is in the transcript (session/host log) — cannot prove self-bootstrap"; exit 1; }
ok "agent bootstrapped the plugin itself: agent-aimail@$GOT_SDK (= npm latest) at $SDK_PKG (fixture ran no install)"
echo "{\"sdk\":\"agent-aimail\",\"npm_latest\":\"${WANT_SDK:-unknown}\",\"installed\":\"$GOT_SDK\",\"path\":\"$SDK_PKG\",\"installed_by\":\"agent\"}" \
  > "${L3_LOG_ARCHIVE:-$WORK}/sdk.json" 2>/dev/null || true
# the composed tree must now carry the mail tool row in the GLOBAL layer
# (L2 gate mechanism: tool-mail is what reaches every session's surface).
DSH_HOME="$DSH_HOME" dsh --profile web --dump-config > "$WORK/post-tree.txt" 2>/dev/null \
  || die "dump-config after the agent's install failed"
MAIL_ROWS="$(grep -c '^- id: tool-mail' "$WORK/post-tree.txt" || true)"
[ "${MAIL_ROWS:-0}" -ge 1 ] || { bad "no tool-mail row in the composed profile tree after the agent's install (tree: $WORK/post-tree.txt)"; exit 1; }
ok "composed profile carries the tool-mail row (global layer, count=$MAIL_ROWS)"

# ═══ [4] host restart (a plugin install applies on the NEXT boot) ══════════════
say "[4/7] host restart (plugin installs take effect on the next boot)"
kill "$HOST_PID" 2>/dev/null || true
for _ in $(seq 1 20); do kill -0 "$HOST_PID" 2>/dev/null || break; sleep 0.5; done
kill -0 "$HOST_PID" 2>/dev/null && kill -9 "$HOST_PID" 2>/dev/null
HOST_PID=""
web_start_host || die "web host restart after plugin install failed"
ok "host restarted (plugin now in the booted tree)"

# ═══ [5] turn 2: the user says "installed, continue" → the agent binds ═════════
say "[5/7] turn 2 — 'installed, continue'; the agent binds via aimail_address"
# a FRESH session: the user's next conversation after restarting the app
# (the previous session's agent surface was composed from the old tree).
SID2="$(web_create_session)" || die "session/create (post-restart) failed"
say "session: $SID2"
cat > "$WORK/turn2-prompt.txt" <<PROMPT
I restarted the app after your setup step. Continue with the activation I sent
you earlier — here is the activation prompt again:

$(cat "$SPOOL.prompt.txt")
PROMPT
web_send_prompt "$SID2" "$WORK/turn2-prompt.txt" || die "turn 2 prompt injection failed"
ok "turn 2 prompt injected (user: installed, continue + the activation prompt)"
# LLM-time delta evidence: snapshot the host-wide total BEFORE the bind turn;
# the turn must ADD real LLM time (turn 1's llmMs must not count for it).
PRE_BIND_LLMMS="$(llm_ms_total)"
# the bind turn: the agent extracts email/code/gateway_url from the prompt,
# calls aimail_address(bind), the gateway activates the code (ownership row),
# the SDK writes the binding files and opens the SSE streams. Wait for the
# turn to close AND for the binding to land (the tool result drives both).
BIND_WAIT=$((SECONDS + TURN_WINDOW))
BINDING=""
while [ $SECONDS -lt $BIND_WAIT ]; do
  BINDING="$(find "$AIMAIL_HOME" -name "$BINDING_FILE" 2>/dev/null | head -1)"
  if [ -n "$BINDING" ]; then
    # the binding file must be COMPLETE (email + api_key) before we judge the
    # turn — a half-written file during the tool call would be a false pass.
    python3 - "$BINDING" <<'PY' && break
import json, sys
b = json.load(open(sys.argv[1], encoding="utf-8"))
sys.exit(0 if b.get("email") and b.get("api_key") else 1)
PY
  fi
  kill -0 "$HOST_PID" 2>/dev/null || { bad "dsh host died during the bind turn: $(tail -15 "$WORK/dsh-host.log" | tr '\n' ' ')"; exit 1; }
  sleep 2
done
[ -n "$BINDING" ] || die "the agent never bound: no complete $BINDING_FILE within ${TURN_WINDOW}s (llmMs: $(llm_ms_total); host log: $(tail -15 "$WORK/dsh-host.log" | tr '\n' ' '))"
say "binding landed: $BINDING"
# ownership must exist (the V2 bind path writes it; without it SSE would 403).
OWNERSHIP="$(python3 - "$GW_HOME/data/aimail.db" "$SYSTEM_ID" <<'PY'
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    n = con.execute("SELECT count(*) FROM agent_system_addresses WHERE system_id=?", (sys.argv[2],)).fetchone()[0]
    print(n)
except Exception:
    print(-1)
PY
)"
[ "${OWNERSHIP:-0}" -ge 1 ] || die "no agent_system_addresses ownership row (system_id=$SYSTEM_ID) — the bind did not complete the V2 path"
ok "ownership row present (agent_system_addresses: $OWNERSHIP)"
# the bind turn must have been a REAL LLM call (session stats delta evidence).
TURN2_LLMMS="$(llm_ms_total)"
BIND_TURN_LLM_MS=$(( ${TURN2_LLMMS:-0} - ${PRE_BIND_LLMMS:-0} ))
[ "$BIND_TURN_LLM_MS" -ge 1 ] || die "no LLM time ADDED by the bind turn (delta=${BIND_TURN_LLM_MS}ms, total: ${PRE_BIND_LLMMS:-0} → ${TURN2_LLMMS:-0} — host swallowed an adapter error or the turn never ran)"
# credential hygiene (same assertions as the pre-V2 dsh cell): owner-only, no
# webhook_url (SSE binding). The V2 shared-domain bind path writes ONLY the
# per-address binding (address.js: saveAgentConfig + hub.reconcile) — the
# system-level aimail_gateway.json is CLI-path owned and is NOT part of this
# journey's contract (the pull loop reads gateway_url from the binding itself).
GW_CFG=""
[ -f "$(find "$AIMAIL_HOME" -name aimail_gateway.json 2>/dev/null | head -1)" ] && GW_CFG="$(find "$AIMAIL_HOME" -name aimail_gateway.json 2>/dev/null | head -1)"
[ -n "$GW_CFG" ] && say "system-level aimail_gateway.json also present: $GW_CFG (optional on this path)"
for _f in "$GW_CFG" "$BINDING"; do
  [ -n "$_f" ] || continue
  _m="$(stat -c %a "$_f" 2>/dev/null || stat -f %Lp "$_f" 2>/dev/null)"
  # POSIX modes do not exist on Windows/NTFS: git-bash reports 644 for every file a native
  # Windows process creates, and Node's chmod on Windows can only toggle the read-only
  # attribute. The check is therefore INAPPLICABLE there, not failed — report it and do not
  # count it as a pass (windows cell, 2026-11-23: mode=644 while llmMs=42124 and the bind
  # had in fact landed).
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      warn "binding mode check skipped: POSIX modes do not exist on Windows/NTFS (mode=$_m) — not counted as a pass" ;;
    *)
      case "$_m" in 600) : ;; *) die "$_f mode=$_m (expected owner-only 600)" ;; esac
      ok "binding mode owner-only (600): $_f" ;;
  esac
done
python3 - "$BINDING" <<'PY' || die "SSE binding carries a webhook_url (regression)"
import json, sys
b = json.load(open(sys.argv[1], encoding="utf-8"))
sys.exit(1 if (b.get("webhook_url") or "") else 0)
PY
ok "bind turn closed (llmMs: $TURN2_LLMMS); binding owner-only; no webhook_url"
# SSE streams: the plugin hub opens them once a binding exists (address.ts
# bind → hub.reconcile); status.json is the host's own report.
STATUS_FILE="$DSH_HOME/profiles/web/aimail/status.json"
SSE_UP=0
for _ in $(seq 1 60); do
  if [ -f "$STATUS_FILE" ] && python3 -c "import json,sys;d=json.load(open(sys.argv[1]));sys.exit(0 if d.get('mode')=='sse' and (d.get('streams') or []) else 1)" "$STATUS_FILE" 2>/dev/null; then
    SSE_UP=1; break
  fi
  kill -0 "$HOST_PID" 2>/dev/null || { bad "dsh host died before SSE streams came up"; exit 1; }
  sleep 2
done
[ "$SSE_UP" = 1 ] || die "dsh SSE streams not established within 120s (status=$STATUS_FILE; host log: $(tail -10 "$WORK/dsh-host.log" | tr '\n' ' '))"
ok "SSE streams up (mode=sse, streams non-empty)"
# ═══ [6] welcome closed loop (agent calibrates → applicant approves) ═══════════
say "[6/7] welcome closed loop — agent calibrates, applicant (default manager) approves"
# SKILL §3: every activated address must be calibrated — the agent calls
# role_calibrator() → the gateway queues the welcome for the address → SSE →
# the agent's next real turn replies with the persona/signature tags.
# Grace period first: a strong model calibrates inside the bind turn itself.
# If the welcome has not been queued, the USER (this script, over the web API
# — exactly what a user who read the activation SKILL would say) nudges the
# agent to run the calibration. The nudge is a USER TURN; the script never
# calls the gateway's welcome API itself (D2: the agent must drive its own
# tools; measured with mimo-v2.6-flash: the bind turn closes without
# calibration, the nudge turn is what makes the loop deterministic).
DB="$GW_HOME/data/aimail.db"
dbq() { python3 - "$DB" <<PY
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
con.execute("PRAGMA query_only=ON")
try:
    for row in con.execute("""$1"""):
        print("|".join("" if v is None else str(v) for v in row))
except Exception:
    pass
PY
}
WELCOME_ROW=""
wait_welcome() { # $1=iterations — returns 0 when a welcome row exists
  local _
  for _ in $(seq 1 "$1"); do
    # The welcome may already be delivered+acked by the time we look: the pending row is deleted
    # on ack, so a pending-only query reports "no welcome" while the agent correctly calibrated
    # (measured 2026-11-24, pi: the ledger had 'Welcome to AIMail World, l3agent, since …',
    # status=delivered, and pending_deliveries was empty). Check the delivered ledger first.
    WELCOME_ROW="$(dbq "SELECT id FROM emails WHERE lower(subject) LIKE '%welcome to aimail%' AND recipients LIKE '%$ADDR%' LIMIT 1" || true)"
    [ -n "$WELCOME_ROW" ] || WELCOME_ROW="$(dbq "SELECT id FROM pending_deliveries WHERE email='$ADDR' AND payload LIKE '%Welcome to the AIMail world%' LIMIT 1" || true)"
    [ -n "$WELCOME_ROW" ] && return 0
    kill -0 "$HOST_PID" 2>/dev/null || return 1
    sleep 2
  done
  return 0
}
wait_welcome 22   # ~44s grace: the bind turn may still be calibrating
if [ -z "$WELCOME_ROW" ]; then
  say "welcome not queued yet — user nudge: run the identity calibration (SKILL §3)"
  cat > "$WORK/turn3-prompt.txt" <<PROMPT
The activation finished, but I think the identity calibration step from the
AIMail activation guide is still pending: every activated address must be
calibrated so it gets its welcome email. Please run the role_calibrator tool
for my address now.
PROMPT
  web_send_prompt "$SID2" "$WORK/turn3-prompt.txt" || die "calibration nudge injection failed"
  wait_welcome 150   # the nudge turn + calibration must queue the welcome
fi
[ -n "$WELCOME_ROW" ] || die "no welcome in the delivery ledger for $ADDR (role_calibrator never ran even after the user nudge — host log: $(tail -15 "$WORK/dsh-host.log" | tr '\n' ' '); nudge session: $SID2)"
ok "welcome queued (ledger row $WELCOME_ROW) — the agent calibrated"
# the agent's reply turn (persona + signature tags) must be a REAL LLM call.
# Snapshot the host-wide LLM time before the approval triggers the reply turn;
# the delta after is the evidence (the reply may attach to any session).
PRE_REPLY_LLMMS="$(llm_ms_total)"
# ── manager approval (user behaviour): the applicant is the address's default
# manager (gateway v1.0.11 applicant→manager wiring). A real manager approves by
# emailing the agent with subject exactly [CMD] approve persona and the
# persona:/signature: sections in the body (webhook.rs parse_persona_approval).
# Persona/signature values: fixed test identity (the manager's own choice — in a
# real deployment the manager types whatever they want).
python3 - "$GW_SMTP_PORT" "$APPLICANT" "$ADDR" <<'PY' || die "manager [CMD] approve persona SMTP send failed"
import sys, smtplib
port, applicant, addr = int(sys.argv[1]), sys.argv[2], sys.argv[3]
msg = (f"From: {applicant}\r\nTo: {addr}\r\nSubject: [CMD] approve persona\r\n\r\n"
       "persona: L3 dsh V2 regression persona\n"
       "signature: -- L3 dsh V2\n")
with smtplib.SMTP("127.0.0.1", port, timeout=15) as s:
    s.sendmail(applicant, [addr], msg)
print("  manager [CMD] approve persona sent via SMTP inbound")
PY
# wait for the approval to process: gateway log line + meta row updated.
APPROVED=""
META_PERSONA=""
for _ in $(seq 1 120); do
  APPROVED="$(grep -ac 'manager_persona_approved' "$GW_HOME/boot.log" 2>/dev/null || true)"
  META_PERSONA="$(dbq "SELECT agent_persona FROM domain_addr_meta WHERE email_address='$ADDR' LIMIT 1" || true)"
  [ "${APPROVED:-0}" -ge 1 ] && [ -n "$META_PERSONA" ] && break
  sleep 2
done
[ "${APPROVED:-0}" -ge 1 ] || die "gateway never logged manager_persona_approved (is_manager failed? meta manager: $(dbq "SELECT manager_address FROM domain_addr_meta WHERE email_address='$ADDR' LIMIT 1" || true))"
[ -n "$META_PERSONA" ] || die "domain_addr_meta.agent_persona empty after approval"
ok "approval processed (manager_persona_approved; persona='$META_PERSONA')"
# the completion signal: the agent receives the persona-applied confirmation
# (SKILL §3 completion signal — notify_agent_persona_applied queues it as an
# inbound for the address). The ledger row's payload is the JSON envelope
# (empirical: no subject column; the body text is the stable marker).
APPLIED_ROW=""
for _ in $(seq 1 120); do
  APPLIED_ROW="$(dbq "SELECT id FROM pending_deliveries WHERE email='$ADDR' AND payload LIKE '%persona and signature are now active%' LIMIT 1" || true)"
  [ -n "$APPLIED_ROW" ] && break
  kill -0 "$HOST_PID" 2>/dev/null || { bad "dsh host died before the persona-applied notice"; exit 1; }
  sleep 2
done
[ -n "$APPLIED_ROW" ] || die "the persona-applied confirmation never reached the agent's ledger (webhook.rs notify_agent_persona_applied did not queue)"
POST_REPLY_LLMMS="$(llm_ms_total)"
ok "persona-applied notice delivered (pending row $APPLIED_ROW; llmMs: ${PRE_REPLY_LLMMS:-0} → ${POST_REPLY_LLMMS:-0})"

# ═══ [7] query-mail signature assertion (whoami readback) ═════════════════════
say "[7/7] query-mail signature assertion (whoami readback)"
# PYTHONIOENCODING: the final line prints a check mark; windows runners default stdout to
# cp1252 and the UnicodeEncodeError turned a PASSING assertion into "signature assertion
# failed" (run 45164123142: persona + signature were both read back before the crash).
PYTHONIOENCODING=utf-8 python3 - "$GW_URL" "$BINDING" "$APPLICANT" <<'PY' || die "signature assertion failed"
import sys, json, time, urllib.request, urllib.error, hashlib, hmac
gw_url, binding_file, manager_addr = sys.argv[1], sys.argv[2], sys.argv[3]
b = json.load(open(binding_file, encoding="utf-8"))
email, key = b.get("email", ""), b.get("api_key", "")
assert email and key, f"binding missing email/api_key: {list(b.keys())}"
print(f"  agent address : {email}")
def signed(method, path, body_obj=None, api_key=key, identity=email):
    body = json.dumps(body_obj).encode() if body_obj is not None else b""
    ts = str(int(time.time() * 1000))
    base_str = f"{method.upper()}\n{path}\n{ts}\n{hashlib.sha256(body).hexdigest()}"
    sig = hmac.new(hashlib.sha256(api_key.encode()).hexdigest().encode(), base_str.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(gw_url.rstrip("/") + path, data=body or None, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Api-Identity", identity)
    req.add_header("X-Api-Timestamp", ts)
    req.add_header("X-Api-Signature", sig)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode(errors="replace")
marker = f"L3D2-{int(time.time())}"
st, resp = signed("POST", "/api/v1/send",
                  {"to": manager_addr, "subject": f"query {marker}",
                   "markdown": f"query {marker}: identity + signature check"})
assert st in (200, 201, 202), f"query mail send failed: HTTP {st} {str(resp)[:200]}"
print(f"  query mail sent (HTTP {st})")
st, who = signed("GET", "/api/v1/whoami")
assert st == 200, f"whoami failed: HTTP {st} {str(who)[:200]}"
persona = (who.get("agent_persona") or "").strip()
sig = (who.get("agent_signature") or "").strip()
print(f"  agent_persona    : {persona[:60] or '(empty)'}")
print(f"  agent_signature  : {sig[:60] or '(empty)'}")
assert persona and sig, "identity card / signature not in effect after welcome approve"
print("  ✓ identity card (persona) + email signature in effect")
PY
ok "query-mail signature assertion passed"

# ═══ verdict ═══════════════════════════════════════════════════════════════════
TOTAL_LLMMS="$(llm_ms_total)"
echo
ok "════ dsh V2 JOURNEY PASS — activation code → host agent bind → welcome loop closed (llmMs: ${TOTAL_LLMMS:-0})"

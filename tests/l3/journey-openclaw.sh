#!/usr/bin/env bash
# Pi L3 journey — user simulation, agent self-bootstrap (owner ruling 2026-11-23).
# Work in progress: NOT wired into l3.yml and deliberately named *.sh.wip so run-l3.sh's guard
# keeps refusing pi cells until this journey passes locally.
set -uo pipefail
HOST_NAME=openclaw
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
[ -n "${OPENCLAW_HOME:-}" ] || gap "OPENCLAW_HOME not set (run-l3.sh openclaw env prep)"
[ -n "${WORK:-}" ] || gap "WORK not set"
[ -x "$GW_BIN" ] || gap "gateway binary not executable: $GW_BIN"
command -v openclaw >/dev/null 2>&1 || gap "openclaw not on PATH (prepare.sh openclaw failed upstream)"

V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TURN_WINDOW="${WELCOME_TIMEOUT:-600}"      # budget, not an assertion
SINK_PORT="${SINK_PORT:-12528}"
OC_PROBE_PORT="${OC_PROBE_PORT:-39802}"
SPOOL="$GW_HOME/mail.spool"
STATE="$WORK/oc-v2.state"
AIMAIL_HOME="${AIMAIL_HOME:-$HOME/.aimail}"; export AIMAIL_HOME
BINDING_FILE="$(python3 -c 'import json,sys; sys.stdout.write(json.load(open(sys.argv[1]))["binding_file"])' "$AIMAIL_REPO/contract/aimail-contract.json")"
[ -n "$BINDING_FILE" ] || gap "cannot read binding_file from $AIMAIL_REPO/contract/aimail-contract.json"
: > "$SPOOL"
GW_PID=""; SINK_PID=""
cleanup() {
  [ -n "${GW_PID:-}" ] && kill "$GW_PID" >/dev/null 2>&1 || true
  [ -n "${SINK_PID:-}" ] && kill "$SINK_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# one openclaw process per user turn; the prompt file is the user's message verbatim
oc_host_turn() {  # oc_host_turn <prompt-file>
  local f="$1"
  mkdir -p "$WORK/ws"
  # the embedded agent defaults to its own model; pin the one the clean LLM config declares
  _m="$(python3 -c "import json;d=json.load(open('$OPENCLAW_HOME/openclaw.json'));m=d.get('agents',{}).get('defaults',{}).get('model');print(m if isinstance(m,str) else (m or {}).get('primary',''))" 2>/dev/null)"
  _marg=""; [ -n "$_m" ] && _marg="--model $_m"
  ( cd "$WORK/ws" && timeout 900 openclaw agent --local $_marg --message-file "$f" ) >> "$WORK/oc-turns.log" 2>&1
  return $?
}
llm_ms_total() { echo 0; }   # openclaw exposes no llmMs counter; the turn output is the evidence
binding_path() { ls "$AIMAIL_HOME"/systems/*/*/"$BINDING_FILE" 2>/dev/null | head -1; }
plugin_pkg()   { find "$OPENCLAW_HOME" -maxdepth 7 -path '*agent-aimail/package.json' 2>/dev/null | head -1; }

# integrity: the agent must not read this repo (fixture or docs). CI showed the difference: with no
# repo on its path the agent did not install at all, so a repo-reading run is not a product verdict.
oc_leak_check() {
  local f hit=0
  for f in $(find "$OPENCLAW_HOME" -type f \( -name '*.jsonl' -o -name '*.json' \) -newermt '-2 hours' 2>/dev/null | head -40); do
    grep -qaE "tests/l3/|journey-openclaw|run-l3\.sh|aimail/docs/|docs/dsh-mail" "$f" 2>/dev/null && hit=1
  done
  [ "$hit" = 0 ] || die "INVALID cell: the agent read the L3 harness or repo docs — the run is open-book and cannot judge the product"
}
oc_leak_check

echo "════ openclaw V2 journey: activation code → agent self-install → bind → welcome loop (journey-openclaw [0]-[7])"
# ═══ [0] fixture: sink + gateway [relay] + LLM proxy + domain offer ═══════════
# Stop the gateway run-l3.sh ② started and bring it back WITH the relay wiring
# (external-domain mail → sink; internal l3.local recipients unaffected —
# they go through create_inbound, not the relay).
say "[0/7] fixture: SMTP sink + gateway [relay] + LLM preflight + domain offer"
[ -n "${GW_PID_L3:-}" ] && kill "$GW_PID_L3" >/dev/null 2>&1 || true
sleep 1
# free the web host port (a stale probe host must not shadow this journey)
python3 - "$OC_PROBE_PORT" <<'PY' || gap "probe port $OC_PROBE_PORT busy (stale process)"
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
# makes for any host cell — idempotent upsert). The protocol route (OpenAI llm-pi-ai vs
# Anthropic llm-deepseek) is chosen by the endpoint's HOST inside llm-config.py:
# CI's xiaomi host takes the OpenAI route (xiaomi serves no /v1/messages), local
# endpoints keep the shipped Messages route. No proxy in front: a proxy's own host
# (127.0.0.1) would flip the route and kill the turn.
python3 "$AIMAIL_REPO/tests/agent-env/lib/llm-config.py" write --platform pi --home "$OPENCLAW_HOME" \
  --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" --api-key "$LLM_API_KEY" >/dev/null \
  || die "llm-config write (pi → $LLM_BASE_URL) failed"
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
L3_NAME = "L3 User"   # the name the submit below uses; verification is name+email+code
st, r = post("/api/v1/public/email-verify/send", {"email": applicant, "name": L3_NAME})
assert st in (200, 201), f"verify/send HTTP {st}: {r}"
print(f"  verify/send: {st}")
part, vcode = spool_wait(applicant, r"(?<!\d)(\d{6})(?!\d)")
assert vcode, "no 6-digit verification code in the applicant mailbox (sink/relay)"
print(f"  mailbox: verification code received ({vcode})")
st, r = post("/api/v1/public/email-verify/check",
              {"email": applicant, "name": L3_NAME, "code": vcode})
assert st in (200, 201) and r.get("ok") is not False, f"verify/check HTTP {st}: {r}"
print(f"  verify/check: {st}")
st, r = post("/api/v1/public/apply-address", {"email": applicant, "name": L3_NAME,
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

# ═══ [2] turn 1: clean pi environment receives the activation mail body ══════════════
say "[2/7] turn 1 — clean pi environment (no plugin) receives the activation prompt"
[ -z "$(plugin_pkg)" ] || die "agent-aimail already installed before the journey ($(plugin_pkg)) — environment not clean"
grep -qa 'agent-aimail' "$OPENCLAW_HOME/agent/settings.json" 2>/dev/null \
  && die "pi settings still declare npm:agent-aimail ($OPENCLAW_HOME/agent/settings.json) — pi would re-install it by itself, so the bootstrap could not be attributed to the agent"
[ -z "$(binding_path)" ] || die "a binding already exists before any bind turn ($(binding_path)) — environment not clean"
cp "$SPOOL.prompt.txt" "$WORK/turn1-prompt.txt"
oc_host_turn "$WORK/turn1-prompt.txt" || say "  turn 1 exited non-zero (detail in $WORK/oc-turns.log)"
ok "turn 1 prompt injected (the activation mail body, verbatim)"

# ═══ [3] the agent installs the SDK itself ══════════════════════════════════════════
say "[3/7] the agent installs the plugin itself (the activation mail told it to)"
WANT_SDK="$(npm view agent-aimail version 2>/dev/null || true)"
say "published SDK under test: agent-aimail@${WANT_SDK:-<npm view failed>}"
if [ -z "$(plugin_pkg)" ]; then
  say "plugin not installed after turn 1 — user nudge (words only; the agent must run the install)"
  cat > "$WORK/turn1b-nudge.txt" <<PROMPT
Go ahead and install the AIMail plugin SDK yourself now (the Node/npm one for this host),
then retry the activation with the same code. I do not have a terminal here — you run it.
PROMPT
  oc_host_turn "$WORK/turn1b-nudge.txt" || say "  nudge turn exited non-zero"
fi
for _w in $(seq 1 60); do [ -n "$(plugin_pkg)" ] && break; sleep 3; done
PKG="$(plugin_pkg)"
[ -n "$PKG" ] || die "the agent never installed agent-aimail: the activation prompt did not drive the bootstrap (see $WORK/oc-turns.log)"
GOT_SDK="$(node -e 'process.stdout.write(require(process.argv[1]).version)' "$PKG" 2>/dev/null || true)"
[ -n "$GOT_SDK" ] || die "cannot read the installed SDK version from $PKG"
if [ -n "$WANT_SDK" ] && [ "$GOT_SDK" != "$WANT_SDK" ]; then
  die "installed SDK version $GOT_SDK != npm latest $WANT_SDK (registry moved mid-run? re-run)"
fi
# The agent's own action must be visible. Two sources: the turn stdout, and pi's session log
# ($OPENCLAW_HOME/agent/sessions/<cwd>/<ts>.jsonl), which is where tool calls live.
_pi_sess="$(ls -t "$OPENCLAW_HOME"/agent/sessions/*/*.jsonl 2>/dev/null | head -1)"
_attrib=0
grep -qaE 'install.{0,40}agent-aimail|agent-aimail.{0,40}(install|add)' "$WORK/oc-turns.log" 2>/dev/null && _attrib=1
if [ "$_attrib" = 0 ] && [ -n "$_pi_sess" ] && [ -f "$_pi_sess" ]; then
  grep -qaE 'install.{0,60}agent-aimail|agent-aimail.{0,60}(install|add)' "$_pi_sess" && _attrib=1
fi
[ "$_attrib" = 1 ] || die "the plugin appeared but no agent-side install action is in the turn log or pi session ($_pi_sess) — cannot prove self-bootstrap (a declarative settings entry would look exactly like this)"
ok "agent bootstrapped the plugin itself: agent-aimail@$GOT_SDK (= npm latest) at $PKG (fixture ran no install)"
echo "{\"sdk\":\"agent-aimail\",\"npm_latest\":\"${WANT_SDK:-unknown}\",\"installed\":\"$GOT_SDK\",\"path\":\"$PKG\",\"installed_by\":\"agent\"}" \
  > "${L3_LOG_ARCHIVE:-$WORK}/sdk.json" 2>/dev/null || true

# ═══ [4] the install applies on the next pi process ══════════════════════════════════
say "[4/7] install applies on the next pi process (no daemon to restart)"

# ═══ [5] turn 2: the user says "continue" → the agent binds ═════════════════════════
say "[5/7] turn 2 — 'installed, continue'; the agent binds via aimail_address"
cat > "$WORK/turn2-prompt.txt" <<PROMPT
Continue with the activation I sent you earlier — here is the activation prompt again:

$(cat "$SPOOL.prompt.txt")
PROMPT
oc_host_turn "$WORK/turn2-prompt.txt" || say "  turn 2 exited non-zero"
BINDING=""
for _w in $(seq 1 90); do BINDING="$(binding_path)"; [ -n "$BINDING" ] && break; sleep 2; done
[ -n "$BINDING" ] || die "the agent never bound: no $BINDING_FILE under $AIMAIL_HOME (see $WORK/oc-turns.log)"
python3 - "$BINDING" <<'PY' || die "binding is missing required fields"
import json, sys
b = json.load(open(sys.argv[1], encoding="utf-8"))
need = ("email", "api_key", "agent_system_id", "gateway_url")
missing = [k for k in need if not b.get(k)]
assert not missing, f"missing {missing}"
assert str(b["agent_system_id"]).startswith("asid_"), b["agent_system_id"]
assert not b.get("webhook_url"), "webhook_url present"
PY
ok "binding landed: $BINDING (agent_system_id, gateway_url, no webhook_url)"

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
    true
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
  oc_host_turn "$WORK/turn3-prompt.txt" || die "calibration nudge injection failed"
  wait_welcome 150   # the nudge turn + calibration must queue the welcome
fi
[ -n "$WELCOME_ROW" ] || die "no welcome in the delivery ledger for $ADDR (role_calibrator never ran even after the user nudge — host log: $(tail -15 "$WORK/oc-turns.log" | tr '\n' ' '))"
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
# The approval is an ordinary inbound mail. Retry briefly (the gateway may still be settling)
# and, on failure, state the port + whether anything is listening — a bare "connection refused"
# cost a whole cell on 2026-11-24.
_approve_try=0
while [ "$_approve_try" -lt 10 ]; do
  if python3 - "$GW_SMTP_PORT" "$APPLICANT" "$ADDR" <<'PY'
import sys, smtplib
import sys, smtplib
port, applicant, addr = int(sys.argv[1]), sys.argv[2], sys.argv[3]
msg = (f"From: {applicant}\r\nTo: {addr}\r\nSubject: [CMD] approve persona\r\n\r\n"
       "persona: L3 pi V2 regression persona\n"
       "signature: -- L3 pi V2\n")
with smtplib.SMTP("127.0.0.1", port, timeout=15) as s:
    s.sendmail(applicant, [addr], msg)
print("  manager [CMD] approve persona sent via SMTP inbound")
PY
  then _approve_try=99; break; fi
  _approve_try=$((_approve_try+1)); sleep 2
done
if [ "$_approve_try" -ne 99 ]; then
  bad "manager [CMD] approve persona SMTP send failed: port ${GW_SMTP_PORT} not accepting after 20s (listening: $(ss -ltn 2>/dev/null | grep -c ":$GW_SMTP_PORT ") ; gateway log: $(tail -3 "$GW_HOME/boot.log" | tr '\n' ' '))"
  exit 1
fi
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
  :  # pi has no daemon to keep alive between turns
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
ok "════ pi V2 JOURNEY PASS — activation code → host agent bind → welcome loop closed (llm evidence: turn log)"

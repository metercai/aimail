#!/usr/bin/env bash
# M6 A3/A4 self-verify: real pi host + 0.5.1 tgz + injected mail => REAL LLM turn + ack.
#
# A3: an inbound mail queued for the bound address (system welcome with the
#      agent-scope key — the L2 harness delivery path, harness.mjs:180) is
#      delivered over the live aggregate SSE stream => deliverPiInbound =>
#      pi.sendUserMessage(steer) => a REAL turn (local LLM) inside the host.
#      Evidence = a logging proxy in front of the local LLM records
#      POST /chat/completions after injection (content-agnostic, timing-robust,
#      strictly stronger than the L2 fake sendUserMessage).
# A4: after delivery the SDK acks (POST /api/v1/admin/pending/ack — http.rs:141,
#      the route system-pull.ts:63 acks through; the stream NEVER acks,
#      http.rs:3566) => `DELETE FROM pending_deliveries` (storage.rs:2505) —
#      the row DISAPPEARS. Observed read-only against the fixture's own SQLite.
#      The claim endpoint /api/v1/agent-system/pending is the SDK's own
#      single-slot endpoint and is never polled from outside.
#
# The welcome injection runs INSIDE the container with the product's own API
# (core.listAgentConfigs + GatewayClient — the L2 harness pattern): the
# agent-scope key never crosses the container boundary (F1 posture). The host
# observes only the LLM proxy log and the fixture DB.
#
# Criteria source: QA findings §6 (HANDOFF-V2-QA-FINDINGS.md §6 items 2-3).
# Turn window = 300s (L3 precedent run-l3.sh:460-464: local 27B turns are slow).
# Exit: 0 PASS · 1 FAIL · 2 ENV GAP.
set -uo pipefail
ROOT=/home/ubuntu/aimail
BIN="$ROOT/../aimail-gateway/target/debug/aimail-gateway"
LLM_URL="${LLM_URL:-http://127.0.0.1:8000/v1}"
LLM_MODEL="${LLM_MODEL:-default}"
PROXY_PORT="${PROXY_PORT:-8001}"
TGZ="${TGZ:-/tmp/agent-aimail-0.5.1.tgz}"
TURN_WINDOW="${TURN_WINDOW:-300}"
[ -x "$BIN" ] || { echo "ENV GAP: gateway binary $BIN"; exit 2; }
[ -f "$TGZ" ] || { echo "ENV GAP: tgz $TGZ"; exit 2; }
command -v docker >/dev/null || { echo "ENV GAP: docker"; exit 2; }
docker image inspect aimail-host-pi:latest >/dev/null 2>&1 || { echo "ENV GAP: aimail-host-pi:latest"; exit 2; }

WORK="$(mktemp -d -t m6-a34-XXXXXX)"
GWDIR="$WORK/gw"; PIHOME="$WORK/pihome"; STAGE="$WORK/stage"; PIDATA="$WORK/pihome-data"
mkdir -p "$GWDIR/storage" "$PIHOME/agent" "$STAGE" "$PIDATA"
: > "$GWDIR/mail.spool"
HPORT=18375; SPORT=12375
cat > "$GWDIR/config.toml" <<TOML
[http]
bind = "127.0.0.1:$HPORT"
hostname = "gateway.test"
[smtp]
bind = "127.0.0.1:$((SPORT+1))"
hostname = "gateway.test"
[storage]
path = "$GWDIR/storage"
[relay]
smtp_server = "127.0.0.1:$SPORT"
[admin]
email = "admin@test.local"
manager_default = "admin@test.local"
[webhook]
allow_private_targets = true
[retry]
poll_interval_secs = 1
initial_backoff_secs = 1
TOML
GW_URL="http://127.0.0.1:$HPORT"
DB="$GWDIR/storage/aimail.db"
PROXY_LOG="$WORK/llm-proxy.log"
PROXY_PID=""; SINK_PID=""; GW_PID=""
cleanup(){
  if [ -n "$GW_PID" ] && kill -0 "$GW_PID" 2>/dev/null; then
    "$BIN" -c "$GWDIR/config.toml" stop >/dev/null 2>&1 || kill "$GW_PID" 2>/dev/null
  fi
  kill "$SINK_PID" "$PROXY_PID" 2>/dev/null || true
  docker rm -f m6-a34-pi >/dev/null 2>&1 || true
}
trap cleanup EXIT
# daemon lock (global pid file) — production-boundary handling as the arming gate
PIDF="/tmp/aimail-gateway.pid"
if [ -f "$PIDF" ]; then
  OLD="$(cat "$PIDF" 2>/dev/null || true)"
  if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null && ! tr '\0' ' ' < "/proc/$OLD/cmdline" 2>/dev/null | grep -qF "$GWDIR/config.toml"; then
    echo "ENV GAP: another aimail-gateway owns the daemon lock (PID $OLD); stop it and re-run"; exit 2
  fi
fi

echo "== [0] gateway fixture + SMTP sink + LLM proxy + LLM check"
python3 "$ROOT/tests/host-acceptance/fixture/smtp_sink.py" --port "$SPORT" --spool "$GWDIR/mail.spool" >"$GWDIR/smtp.log" 2>&1 & SINK_PID=$!
# LLM logging proxy: :PROXY_PORT -> upstream LLM ROOT (no /v1 — models.json
# baseUrl = http://127.0.0.1:PROXY_PORT/v1, so client paths already carry /v1).
LLM_ROOT="${LLM_URL%/v1}"
cat > "$WORK/proxy.py" <<PYEOF
import http.server, socketserver, urllib.request, urllib.error, sys
UPSTREAM = sys.argv[2].rstrip("/")
LOG = open(sys.argv[1], "a", buffering=1)
class H(http.server.BaseHTTPRequestHandler):
    def do(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        LOG.write("REQ %s %s bytes=%d\n" % (self.command, self.path, length)); LOG.flush()
        req = urllib.request.Request(UPSTREAM + self.path, data=body, method=self.command,
                                     headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = r.read()
                self.send_response(r.status)
                for k, v in r.headers.items():
                    if k.lower() not in ("transfer-encoding", "connection"): self.send_header(k, v)
                self.end_headers(); self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code); self.end_headers(); self.wfile.write(data)
        except Exception as e:
            LOG.write("PROXY-ERR %s\n" % e); LOG.flush()
            self.send_response(502); self.end_headers(); self.wfile.write(b'{"error":"proxy"}')
    do_GET = do; do_POST = do; do_PUT = do; do_DELETE = do
    def log_message(self, *a): pass
socketserver.ThreadingTCPServer.allow_reuse_address = True
socketserver.ThreadingTCPServer(("127.0.0.1", int(sys.argv[3])), H).serve_forever()
PYEOF
python3 "$WORK/proxy.py" "$PROXY_LOG" "$LLM_ROOT" "$PROXY_PORT" & PROXY_PID=$!
"$BIN" -c "$GWDIR/config.toml" start >"$GWDIR/gateway.log" 2>&1 & GW_PID=$!
for _ in $(seq 1 80); do curl -fsS "$GW_URL/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS "$GW_URL/health" >/dev/null || { echo "gateway failed"; tail -20 "$GWDIR/gateway.log"; exit 1; }
# LLM must answer through the proxy before we can blame the host for a missing turn.
if ! curl -fsS -m 120 "http://127.0.0.1:$PROXY_PORT/v1/chat/completions" -H "content-type: application/json" \
     -d "{\"model\":\"$LLM_MODEL\",\"max_tokens\":8,\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}" >/dev/null 2>&1; then
  echo "ENV GAP: LLM $LLM_URL not answering via proxy (model $LLM_MODEL)"; exit 2
fi
: > "$PROXY_LOG"   # probe line consumed; the real turn must produce fresh entries
echo "   gateway=$GW_URL LLM proxy=127.0.0.1:$PROXY_PORT (upstream $LLM_ROOT) OK"
ADMIN="$(cat "$GWDIR/storage/aimail.db.admin_key")"; SID="$(cat "$GWDIR/storage/system.id")"
node "$ROOT/tests/dsh-acceptance/harness/admin.mjs" --gateway "$GW_URL" --key "$ADMIN" --identity "$SID" --system "$SID" --domain test.local >/dev/null \
  || { echo "domain offer failed"; exit 1; }

echo "== [1] dev tgz $TGZ -> host staging (0.5.1)"
( cd "$STAGE" && npm install "$TGZ" --no-audit --no-fund >/dev/null 2>&1 )
[ -f "$STAGE/node_modules/agent-aimail/dist/pi/tools.js" ] || { echo "staging missing dist"; exit 2; }
node -e "const v=require('$STAGE/node_modules/agent-aimail/package.json').version; if(v!=='0.5.1'){console.error('tgz version='+v); process.exit(1)}" \
  || { echo "tgz is not 0.5.1"; exit 2; }
echo "   staged (version 0.5.1 verified)"

echo "== [2] claim + LLM config (pi models.json -> proxy)"
TS="$(date +%s | tail -c 4)"
CLAIM_OUT="$(python3 "$ROOT/tests/host-acceptance/fixture/claim_local.py" --gateway "$GW_URL" --spool "$GWDIR/mail.spool" --email "m6pi-$TS@example.com" --kind address --agentname m6pi 2>&1)" || { echo "claim failed: $CLAIM_OUT"; exit 1; }
ADDR="$(echo "$CLAIM_OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("address") or "")')"
CODE="$(echo "$CLAIM_OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("activation_code") or "")')"
[ -n "$ADDR" ] && [ -n "$CODE" ] || { echo "claim parse failed: $CLAIM_OUT"; exit 1; }
# The activation code crosses to the container as a FILE, never as an env var:
# (a) env -e CODE= has been observed to arrive redacted in some write paths
#     (a literal '***' reached the container and caused a 410 — root-caused);
#     a mounted file is immune. (b) F1: the code stays out of the container's
#     env/ps surface.
printf '%s' "$CODE" > "$WORK/code.txt"
chmod 600 "$WORK/code.txt"
# Evidence line (not a secret): the full code is 34 chars (addr- + 6 hex groups).
# A shorter host-side length means the claim/extraction truncated it before bind.
echo "   host CODE len=${#CODE} (expect 34)"
# Hash evidence (the 410 = gateway's sha256(received) found no code_hash in THIS db):
# compare the extracted code's hash to the code_hash this gateway stored.
CODE_HASH_H="$(python3 -c 'import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$CODE")"
DB_HASH="$(sqlite3 "$DB" "PRAGMA query_only=ON; SELECT code_hash FROM activation_codes WHERE email_address='$ADDR' AND claimed=0 LIMIT 1;")"
echo "   host sha256(CODE) = $CODE_HASH_H"
echo "   db   code_hash    = $DB_HASH"
if [ -z "$DB_HASH" ]; then echo "   NO unclaimed code for $ADDR in db"; fi
[ -n "$DB_HASH" ] && [ "$CODE_HASH_H" = "$DB_HASH" ] && echo "   >> host CODE matches db (good)" || echo "   >> host CODE != db code (MISMATCH — 410 explained)"
python3 "$ROOT/tests/agent-env/lib/llm-config.py" write --platform pi --home "$PIHOME" \
  --base-url "http://127.0.0.1:$PROXY_PORT/v1" --model "$LLM_MODEL" --api-key local >/dev/null \
  || { echo "llm-config write failed"; exit 1; }
[ -f "$PIHOME/agent/models.json" ] || { echo "models.json missing"; exit 1; }
echo "   bound target: $ADDR ; models.json -> proxy :$PROXY_PORT"

# bind helper (in-container; the SDK tool is the bind path — QA activation minus the LLM).
# Prints the FULL details envelope (error_code/status/hint — no key is ever in it)
# so a red is diagnosable from the log (F1: the key only lands in the binding file).
cat > "$WORK/bind.mjs" <<'MJS'
import(process.env.EXT + '/dist/pi/tools.js').then(async (m) => {
  const address = m.buildPiTools().find((t) => t.name === 'aimail_address');
  const r = await address.execute('addr', {
    action: 'bind', email: process.env.BIND_EMAIL, code: process.env.CODE,
    gateway_url: process.env.GW_URL,
  }, undefined, { cwd: process.env.WS });
  const d = { ...(r.details ?? r) };
  if (d && d.raw_key) d.raw_key = '[REDACTED]';
  console.log('BIND_DETAILS ' + JSON.stringify(d));
  process.exit(d && d.success ? 0 : 1);
}).catch((e) => { console.error('bind threw:', e.message); process.exit(1); });
MJS

# welcome injection helper (in-container, product API only — L2 harness pattern):
# find the binding via core.listAgentConfigs, POST system/welcome with the
# agent-scope key via GatewayClient. Writes welcome.status.json to AIMAIL_HOME
# (the host-mounted $PIDATA) — the status code crosses, the key never does.
cat > "$WORK/inject.mjs" <<'MJS'
import fs from 'node:fs'
import path from 'node:path'
import(process.env.EXT + '/dist/core/index.js').then(async (core) => {
  const home = process.env.AIMAIL_HOME;
  core.setProfileDirResolver(() => '/root/.pi');
  const systemsDir = path.join(home, 'systems');
  const sids = fs.existsSync(systemsDir) ? fs.readdirSync(systemsDir) : [];
  for (const sid of sids) {
    let cfgs = [];
    try { cfgs = await core.listAgentConfigs(sid); } catch { continue; }
    for (const c of cfgs) {
      if (c.email === process.env.ADDR && c.api_key) {
        const gc = new core.GatewayClient(process.env.GW_URL, c.api_key, 30000, c.email);
        const r = await gc.request('POST', '/api/v1/system/welcome', { to: [] });
        fs.writeFileSync(path.join(home, 'welcome.status.json'),
          JSON.stringify({ status: r.status, ok: r.status >= 200 && r.status < 300 }));
        process.exit(r.status >= 200 && r.status < 300 ? 0 : 1);
      }
    }
  }
  fs.writeFileSync(path.join(home, 'welcome.status.json'),
    JSON.stringify({ status: 0, ok: false, error: 'no binding for ' + process.env.ADDR }));
  process.exit(1);
}).catch((e) => {
  try {
    fs.writeFileSync(process.env.AIMAIL_HOME + '/welcome.status.json',
      JSON.stringify({ status: 0, ok: false, error: String(e && e.message || e) }));
  } catch {}
  process.exit(1);
});
MJS

# LLM model wiring: write_pi (llm-config.py:157) MERGES a provider into
# models.json but does not pick a default — the image ships a cloud provider
# (api.xiaomimimo.com) as factory default. Pin the turn to the local proxy
# model explicitly at boot (test-env choice, like L3's LLM_* vars; the factory
# image already matched the L3 default, mine does not). Provider id computed
# exactly as llm-config.py _provider_id (non-alnum -> '-').
PROVIDER_ID="custom-$(echo "127.0.0.1:$PROXY_PORT" | sed 's/[^a-zA-Z0-9]/-/g')"
echo "   models.json provider=$PROVIDER_ID model=$LLM_MODEL"

echo "== [3] real pi host: install 0.5.1 + bind + boot (SSE, rpc, stdin held) + inject"
cat > "$WORK/pi-a34.sh" <<EOS
set -uo pipefail
export HOME=/root
export AIMAIL_HOME=/root/aimail
export AIMAIL_TRANSPORT=sse
# escaped \$ so the cat runs INSIDE the container (unquoted heredoc)
export CODE="\$(cat /root/code.txt)"
EXT=/root/pkgstage/node_modules/agent-aimail
echo "--- pi install:"
pi install "\$EXT" 2>&1 | tail -1
echo "--- bind (container CODE len=\${#CODE}, expect 34):"
# NOTE: escaped \$ so this runs INSIDE the container (the heredoc is unquoted;
# an unescaped \$(...) would execute on the host at write time — the earlier
# "container sha256" line did and printed an empty host-side result).
echo "container sha256sum=\$(printf '%s' "\$CODE" | sha256sum | cut -d' ' -f1)"
echo "container preflight=\$(node -e 'fetch(process.env.GW_URL + "/api/v1/public/activation-code/preflight", {method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({code: process.env.CODE})}).then(r=>r.text()).then(t=>console.log(t.slice(0,160))).catch(e=>console.log("ERR",e.message))')"
node /root/bind.mjs
rc=\$?
[ \$rc -eq 0 ] || { echo "BIND FAILED (rc=\$rc)"; exit 1; }
echo "--- post-bind mount check (/root/aimail = host pihome-data):"
find /root/aimail -type f 2>/dev/null | head -5
grep " /root/aimail " /proc/mounts 2>/dev/null || echo "(no mount entry for /root/aimail)"
# L3 boot pattern (run-l3.sh:676-682): rpc reads stdin; hold the write end open.
# --model pins the local proxy provider (factory default is the cloud provider).
( sleep 780 ) | timeout 800 pi --mode rpc --model $PROVIDER_ID/$LLM_MODEL > /tmp/a34-pi.log 2>&1 &
PIPID=\$!
for i in \$(seq 1 90); do
  grep -aq "pull entry armed\|aggregate agent\|polling enabled\|default agent bound" /tmp/a34-pi.log 2>/dev/null && break
  kill -0 "\$PIPID" 2>/dev/null || break
  sleep 1
done
echo "--- armed lines so far:"
grep -aE "\[aimail-pull\]|\[agent-aimail\]" /tmp/a34-pi.log 2>/dev/null | head -8
echo "--- inject (system welcome, product API, agent-scope key):"
ADDR="$ADDR" node /root/inject.mjs
echo "inject rc=\$? status-file:"
cat /root/aimail/welcome.status.json 2>/dev/null || echo "(no status file)"
wait "\$PIPID"
EOS
docker rm -f m6-a34-pi >/dev/null 2>&1 || true
docker run -d --name m6-a34-pi --network host --entrypoint /bin/bash \
  -v "$STAGE:/root/pkgstage:ro" \
  -v "$WORK/bind.mjs:/root/bind.mjs:ro" \
  -v "$WORK/inject.mjs:/root/inject.mjs:ro" \
  -v "$WORK/pi-a34.sh:/root/pi-a34.sh:ro" \
  -v "$WORK/code.txt:/root/code.txt:ro" \
  -v "$PIHOME:/root/.pi" \
  -v "$PIDATA:/root/aimail" \
  -e GW_URL="$GW_URL" -e BIND_EMAIL="$ADDR" -e WS=/root/ws -e EXT=/root/pkgstage/node_modules/agent-aimail -e ADDR="$ADDR" \
  aimail-host-pi:latest /root/pi-a34.sh >/dev/null 2>&1

ARMED=""
for i in $(seq 1 120); do
  ARMED="$(docker logs m6-a34-pi 2>&1 | grep -aE "pull entry armed|no pointer — aggregate|polling enabled|default agent bound" | head -1 || true)"
  [ -n "$ARMED" ] && break
  sleep 1
done
[ -n "$ARMED" ] || { echo "A2 prereq FAILED — host never armed:"; docker logs m6-a34-pi 2>&1 | tail -30; exit 1; }
echo "   armed: $ARMED"
# wait for the in-container injection to complete (status file lands in the mount)
WSTAT=""
for i in $(seq 1 90); do
  [ -f "$PIDATA/welcome.status.json" ] && { WSTAT="$(cat "$PIDATA/welcome.status.json")"; break; }
  sleep 1
done
[ -n "$WSTAT" ] || { echo "A3 precondition FAILED — injection never completed:"; docker logs m6-a34-pi 2>&1 | tail -30; exit 1; }
echo "   welcome status: $WSTAT"
grep -q '"ok":true' <<<"$WSTAT" || { echo "A3 precondition FAILED — welcome not 2xx (no mail queued)"; exit 1; }

echo "== [4] A4 precondition: pending row exists; then A3: watch for the real LLM turn (window ${TURN_WINDOW}s)"
# DB reads: query_only on the live WAL DB (gateway holds the write lock; WAL
# allows concurrent readers). Never the /api/v1/agent-system/pending claim
# endpoint — it is the SDK's own single-slot endpoint (slot contention).
dbq(){ sqlite3 -cmd ".timeout 5000" "$DB" "PRAGMA query_only=ON; $1" 2>/dev/null; }
PEND_ROW=""
for i in $(seq 1 30); do
  PEND_ROW="$(dbq "SELECT id || '|' || status || '|' || email FROM pending_deliveries WHERE email='$ADDR' LIMIT 3;")"
  [ -n "$PEND_ROW" ] && break
  sleep 1
done
[ -n "$PEND_ROW" ] || { echo "A4 precondition FAILED — no pending_deliveries row for $ADDR (injection did not queue)"; exit 1; }
echo "   pending row present: $(echo "$PEND_ROW" | tr '\n' '; ')"
T0=$SECONDS
PROXY_TURNS=0
while [ $((SECONDS-T0)) -lt "$TURN_WINDOW" ]; do
  PROXY_TURNS="$(grep -ac "POST /v1/chat/completions" "$PROXY_LOG" 2>/dev/null || true)"
  PROXY_TURNS="${PROXY_TURNS:-0}"
  [ "$PROXY_TURNS" -ge 1 ] 2>/dev/null && break
  sleep 3
done
TURN_ELAPSED=$((SECONDS-T0))
echo "   LLM proxy chat/completions calls: $PROXY_TURNS (elapsed ${TURN_ELAPSED}s)"
echo "   proxy log:"; tail -6 "$PROXY_LOG" 2>/dev/null | sed 's/^/     /'

echo "== [5] A4: pending row disappears (SDK ack after delivery, DELETE storage.rs:2505)"
A4_DRAINED=0
for i in $(seq 1 90); do
  REMAIN="$(dbq "SELECT count(*) FROM pending_deliveries WHERE email='$ADDR' AND status='pending';")"
  if [ "$REMAIN" = "0" ]; then A4_DRAINED=1; break; fi
  sleep 2
done
REMAIN_FINAL="$(dbq "SELECT id||'|'||status FROM pending_deliveries WHERE email='$ADDR';" | tr '\n' '; ')"
echo "   pending for $ADDR: ${REMAIN_FINAL:-(none)} (drained=$A4_DRAINED)"

echo "== evidence: pi host log =="
docker logs m6-a34-pi 2>&1 | grep -aiE "inbound|deliver|steer|aimail-pull|agent-aimail|error" | tail -20 | sed 's/^/   /'

echo "== verdict =="
A3_OK=0; [ "${PROXY_TURNS:-0}" -ge 1 ] 2>/dev/null && A3_OK=1
A4_OK=$A4_DRAINED
echo "   A3 real turn : $([ $A3_OK -eq 1 ] && echo PASS || echo FAIL) (proxy saw $PROXY_TURNS LLM call(s) in ${TURN_ELAPSED}s)"
echo "   A4 ack drain : $([ $A4_OK -eq 1 ] && echo PASS || echo FAIL) (pending row gone)"
if [ $A3_OK -eq 1 ] && [ $A4_OK -eq 1 ]; then
  echo "M6 A3/A4 PASS"
  exit 0
fi
echo "M6 A3/A4 FAIL (WORK=$WORK kept for forensics)"
exit 1

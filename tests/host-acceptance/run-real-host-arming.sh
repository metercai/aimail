#!/usr/bin/env bash
# §6 real-host A2 arming gate (M5/M6 + QA re-verify).
#
# Proves the B1/B2 fix on the REAL host processes (not the L2 fake harness):
#   - pi       : dev tgz installed, one address bound, pointer deleted (QA B1
#                no-pointer condition) => the real pi host process arms exactly
#                ONE aggregate pull stream for the binding.
#   - openclaw : dev tgz installed, TWO addresses bound (same AgentSystemId),
#                pointer deleted => the real openclaw gateway process arms
#                exactly ONE aggregate stream covering BOTH (the P0-7
#                multi-address condition: a pointer must not starve siblings).
#
# Arming lines asserted (the ACTUAL V2 audit lines — see the note on
# "polling enabled" below):
#   [aimail-pull] no pointer — aggregate agent:<asid> gw=<url> members=[<addr>…] (rep <addr>, N/N active)
#   [aimail-pull] pull scope: system= email=agent:<asid> fanout=true
#   (openclaw only) [agent-aimail] pull entry armed for 1 agent-scope binding(s)
#
# NOTE on the QA §6.1 literal ("polling enabled"): that line
# (poll-entry.ts:468) is the V1 per-binding path. V2 bindings (the only kind
# activation creates — they carry agent_system_id) ALWAYS arm through the
# AgentSystemId aggregate (branch guard, poll-entry.ts:488), which emits the
# "no pointer — aggregate … members=[…]" line instead. A literal grep for
# "polling enabled" on a fixed V2 host would therefore be a FALSE NEGATIVE;
# this gate asserts the real V2 lines. (The tier-2 with-pointer path emits no
# [aimail-pull] line at all — only the host-side "pull entry armed" line.)
#
# usage: run-real-host-arming.sh
# env:
#   AIMAIL_GATEWAY_BIN  (default: ../aimail-gateway/target/debug/aimail-gateway)
#   DEV_TGZ             (default: tssdk/packages/agent-aimail dist packed here on the fly)
#   HTTP_PORT (18371) / SMTP_PORT (12371)   gateway fixture ports
#   OC_PORT (18791)                   openclaw internal gateway port
#   HOSTS (default: "pi openclaw")
# Requires: docker (local images aimail-host-pi, aimail-host-openclaw) + python3 + node + npm.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TSSDK="$ROOT/tssdk"
PKG="$TSSDK/packages/agent-aimail"
BIN="${AIMAIL_GATEWAY_BIN:-$ROOT/../aimail-gateway/target/debug/aimail-gateway}"
[ -x "$BIN" ] || { echo "gateway binary missing: $BIN (build it first)"; exit 2; }
command -v docker >/dev/null 2>&1 || { echo "docker required"; exit 2; }
HTTP_PORT="${HTTP_PORT:-18371}"; SMTP_PORT="${SMTP_PORT:-12371}"; OC_PORT="${OC_PORT:-18791}"
GW_URL="http://127.0.0.1:$HTTP_PORT"
WORK="$(mktemp -d -t realhost-arm-XXXXXX)"
GWDIR="$WORK/gw"
mkdir -p "$GWDIR/storage" "$GWDIR/home"
: > "$GWDIR/mail.spool"
cat > "$GWDIR/config.toml" <<TOML
[http]
bind = "127.0.0.1:$HTTP_PORT"
hostname = "gateway.test"
[smtp]
bind = "127.0.0.1:$((SMTP_PORT+1))"
hostname = "gateway.test"
[storage]
path = "$GWDIR/storage"
[relay]
smtp_server = "127.0.0.1:$SMTP_PORT"
[admin]
email = "admin@test.local"
manager_default = "admin@test.local"
[webhook]
allow_private_targets = true
[retry]
poll_interval_secs = 1
initial_backoff_secs = 1
TOML
SINK_PID=""; GW_PID=""
PIDF="/tmp/aimail-gateway.pid"
cleanup(){
  if [ -n "$GW_PID" ] && kill -0 "$GW_PID" 2>/dev/null; then
    "$BIN" -c "$GWDIR/config.toml" stop >/dev/null 2>&1 || kill "$GW_PID" 2>/dev/null
  fi
  kill "$SINK_PID" 2>/dev/null || true
}
trap cleanup EXIT
echo "== [0] local gateway fixture ($GW_URL) + SMTP sink"
# The gateway daemon lock is a GLOBAL pid file (/tmp/aimail-gateway.pid).
# Never touch a lock owned by another gateway (production boundary); only
# reclaim a leftover from a previous run of THIS gate.
if [ -f "$PIDF" ]; then
  OLD="$(cat "$PIDF" 2>/dev/null || true)"
  if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/cmdline" 2>/dev/null | grep -qF "$GWDIR/config.toml"; then
      "$BIN" -c "$GWDIR/config.toml" stop >/dev/null 2>&1 || kill "$OLD" 2>/dev/null || true
      sleep 1
    else
      echo "ENV GAP: another aimail-gateway owns the daemon lock (PID $OLD); stop it and re-run"; exit 2
    fi
  fi
fi
python3 "$HERE/fixture/smtp_sink.py" --port "$SMTP_PORT" --spool "$GWDIR/mail.spool" >"$GWDIR/smtp.log" 2>&1 & SINK_PID=$!
"$BIN" -c "$GWDIR/config.toml" start >"$GWDIR/gateway.log" 2>&1 & GW_PID=$!
for _ in $(seq 1 80); do curl -fsS "$GW_URL/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS "$GW_URL/health" >/dev/null || { echo "gateway fixture failed to start"; tail -20 "$GWDIR/gateway.log"; exit 1; }
ADMIN="$(cat "$GWDIR/storage/aimail.db.admin_key")"; SID="$(cat "$GWDIR/storage/system.id")"
node "$ROOT/tests/dsh-acceptance/harness/admin.mjs" --gateway "$GW_URL" --key "$ADMIN" --identity "$SID" --system "$SID" --domain test.local \
  || { echo "domain offer failed"; exit 1; }
echo "   system=$SID (domain test.local offered)"

# Pack the DEV tgz from the current worktree (L2 semantics: dev version).
# (Pack into $WORK so the package dir is never polluted.)
DEV_TGZ="${DEV_TGZ:-}"
if [ -z "$DEV_TGZ" ]; then
  ( cd "$TSSDK" && pnpm build >/dev/null 2>&1 )
  DEV_TGZ="$WORK/agent-aimail-dev.tgz"
  ( cd "$PKG" && npm pack --pack-destination "$WORK" >/dev/null 2>&1 )
  mv -f "$WORK"/agent-aimail-*.tgz "$DEV_TGZ" 2>/dev/null || true
fi
[ -f "$DEV_TGZ" ] || { echo "dev tgz missing: $DEV_TGZ"; exit 2; }
echo "== [1] dev tgz: $DEV_TGZ"

# Host-staged extension (container installs must NOT need the network):
# npm-install the tgz (fetches the typebox runtime dep) into a stage dir whose
# files are owned by the container's default user (pi: root, openclaw: uid 1000).
STAGE_PI="$WORK/stage-pi"
mkdir -p "$STAGE_PI"
( cd "$STAGE_PI" && npm install "$DEV_TGZ" --no-audit --no-fund >/dev/null 2>&1 )
[ -f "$STAGE_PI/node_modules/agent-aimail/dist/pi/tools.js" ] || { echo "pi stage missing dist/pi/tools.js"; exit 2; }

claim_addr(){ python3 "$HERE/fixture/claim_local.py" --gateway "$GW_URL" --spool "$GWDIR/mail.spool" \
  --email "$1@example.com" --kind address --agentname "$2" 2>&1; }
jget(){ python3 -c "import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])" "$1" "$2"; }

FAIL=0
HOSTS="${HOSTS:-pi openclaw}"
# Gate honesty: missing host images are an ENV GAP (exit 2), not a product red.
need_img(){ docker image inspect "$1" >/dev/null 2>&1 || { echo "ENV GAP: docker image $1 not present (build via .github/workflows/host-images.yml)"; exit 2; }; }
for h in $HOSTS; do case "$h" in pi) need_img aimail-host-pi:latest ;; openclaw) need_img aimail-host-openclaw:latest ;; esac; done

for host in $HOSTS; do
  case "$host" in
  pi)
    echo "== [2] pi real host: bind + no-pointer + boot => assert A2 arming"
    A="$(claim_addr "rhpi-$(date +%s|tail -c 4)" rhpi)"; [ $? -eq 0 ] || { echo "claim failed: $A"; FAIL=1; continue; }
    ADDR="$(jget "$A" address)"; CODE="$(jget "$A" activation_code)"
    # pi bind helper (host-neutral, no LLM; the SDK tool is the bind path —
    # the QA activation itself, minus the LLM turn).
    cat > "$WORK/bind.mjs" <<'MJS'
import(process.env.EXT + '/dist/pi/tools.js').then(async (m) => {
  const address = m.buildPiTools().find((t) => t.name === 'aimail_address');
  if (!address) { console.error('aimail_address missing'); process.exit(1); }
  const r = await address.execute('addr', {
    action: 'bind', email: process.env.BIND_EMAIL, code: process.env.CODE,
    gateway_url: process.env.GW_URL,
  }, undefined, { cwd: process.env.WS });
  const d = r.details ?? r;
  console.log(JSON.stringify({ success: d.success, system_id: d.system_id,
    agent_system_id: d.agent_system_id, webhook_url: d.webhook_url ?? null }));
}).catch((e) => { console.error('bind threw:', e.message); process.exit(1); });
MJS
    cat > "$WORK/pi-side.sh" <<EOS
set -uo pipefail
export HOME=/root
EXT=/root/pkgstage/node_modules/agent-aimail
pi install "\$EXT" 2>&1 | tail -2
node /root/bind.mjs
# QA B1 condition: external activation wrote no profile pointer.
if [ -f /root/.pi/.agentmail ]; then rm -f /root/.pi/.agentmail; echo "pointer DELETED (no-pointer condition reproduced)"; else echo "(no pointer file)"; fi
# Boot the REAL pi (rpc mode; the session loads the extension and arms the
# pull). Output is NOT redirected — the [aimail-pull]/[agent-aimail] arming
# lines must flow to the container stdout (captured by the caller).
( sleep 12 ) | timeout 25 pi --mode rpc 2>&1
EOS
    docker run --rm --network host --entrypoint /bin/bash \
      -v "$STAGE_PI:/root/pkgstage:ro" \
      -v "$WORK/bind.mjs:/root/bind.mjs:ro" \
      -v "$WORK/pi-side.sh:/root/pi-side.sh:ro" \
      -e GW_URL="$GW_URL" -e BIND_EMAIL="$ADDR" -e CODE="$CODE" -e WS=/root/ws -e EXT=/root/pkgstage/node_modules/agent-aimail \
      aimail-host-pi:latest /root/pi-side.sh >"$WORK/pi-side.log" 2>&1
    tail -20 "$WORK/pi-side.log"
    AGG="$(grep -aE 'no pointer — aggregate agent:asid_' "$WORK/pi-side.log" | head -1 || true)"
    FAN="$(grep -aE 'pull scope: .*fanout=true' "$WORK/pi-side.log" | head -1 || true)"
    TOOLS="$(grep -aE 'registered 14 mail tools' "$WORK/pi-side.log" | head -1 || true)"
    echo "   aggregate : ${AGG:-(absent)}"
    echo "   fanout    : ${FAN:-(absent)}"
    echo "   tools     : ${TOOLS:-(absent)}"
    if [ -n "$AGG" ] && [ -n "$FAN" ] && echo "$AGG" | grep -q "$ADDR" && echo "$AGG" | grep -q '1/1 active'; then
      echo "   PI A2 ARMING PASS"
    else
      echo "   PI A2 ARMING FAIL"; FAIL=1
    fi
    ;;
  openclaw)
    echo "== [3] openclaw real host (multi-address, P0-7): 2 binds + no-pointer + boot => assert A2 arming"
    STAGE_OC="$WORK/stage-oc"
    mkdir -p "$STAGE_OC"
    ( cd "$STAGE_OC" && npm install "$DEV_TGZ" --no-audit --no-fund >/dev/null 2>&1 )
    ( cd "$STAGE_OC/node_modules/agent-aimail" && npm install --no-audit --no-fund >/dev/null 2>&1 )
    chown -R 1000:1000 "$STAGE_OC" 2>/dev/null || true
    [ -f "$STAGE_OC/node_modules/agent-aimail/dist/openclaw/tools.js" ] || { echo "oc stage missing dist/openclaw/tools.js"; FAIL=1; continue; }
    TS=$(date +%s | tail -c 4)
    CA="$(claim_addr "rhoc-$TS-a" rhoca)"; CB="$(claim_addr "rhoc-$TS-b" rhocb)"
    ADDR_A="$(jget "$CA" address)"; CODE_A="$(jget "$CA" activation_code)"
    ADDR_B="$(jget "$CB" address)"; CODE_B="$(jget "$CB" activation_code)"
    cat > "$WORK/bind-oc.mjs" <<'MJS'
import path from 'node:path';
const ext = process.env.EXT;
const addrs = JSON.parse(process.env.ADDRS_JSON);
const ocHome = process.env.OC_HOME || path.join(process.env.HOME || '/root', '.openclaw');
const core = await import(path.join(ext, 'dist/core/index.js'));
core.setProfileDirResolver(() => ocHome);
const { createMailTools } = await import(path.join(ext, 'dist/openclaw/tools.js'));
const tools = new Map(createMailTools({ agentId: 'main' }, () => '/root/ws').map((t) => [t.name, t]));
const address = tools.get('aimail_address');
if (!address) { console.error('aimail_address missing'); process.exit(1); }
const out = [];
for (let i = 0; i < addrs.length; i++) {
  const { email, code } = addrs[i];
  const r = await address.execute(`addr${i+1}`, { action: 'bind', email, code, gateway_url: process.env.GW_URL });
  const d = r.details ?? r;
  out.push({ email, success: d.success, system_id: d.system_id, webhook_url: d.webhook_url ?? null });
}
console.log(JSON.stringify({ ok: out.every((o) => o.success), binds: out }));
MJS
    cat > "$WORK/oc-side.sh" <<EOS
set -uo pipefail
export HOME=/data
EXT=/app/pkgstage/node_modules/agent-aimail
openclaw plugins install "\$EXT" --force --accept-capabilities 2>&1 | tail -2
openclaw plugins list 2>/dev/null | grep -i "agent-aimail" | head -1 || true
python3 - "/data" "\$LLM_URL" "\$LLM_MODEL" "\$OC_PORT" <<'PYEOF'
import sys, json, os
home, base, model, port = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
p = home + "/.openclaw/openclaw.json"; os.makedirs(os.path.dirname(p), exist_ok=True)
cfg = {}
try: cfg = json.load(open(p))
except Exception: pass
pid = "llm-local"
m = cfg.setdefault("models", {}); m["mode"] = "merge"
m.setdefault("providers", {})[pid] = {"baseUrl": base, "api": "openai-completions", "apiKey": "local",
  "models": [{"id": model, "name": f"{model} (local)", "contextWindow": 128000, "maxTokens": 4096,
              "input": ["text"], "cost": {"input":0,"output":0,"cacheRead":0,"cacheWrite":0}, "reasoning": False}]}
d = cfg.setdefault("agents", {}).setdefault("defaults", {})
d.setdefault("model", {})["primary"] = f"{pid}/{model}"; d.setdefault("models", {})[f"{pid}/{model}"] = {}
gw = cfg.setdefault("gateway", {}); gw["mode"]="local"; gw["bind"]="loopback"; gw["port"]=port
json.dump(cfg, open(p, "w"), indent=2); print("openclaw.json written (gateway.mode=local port=%d)" % port)
PYEOF
node /data/bind-oc.mjs
find "/data/.aimail" -name agentmail.json 2>/dev/null | head -4
if [ -f "/data/.openclaw/.agentmail" ]; then rm -f /data/.openclaw/.agentmail; echo "pointer DELETED (no-pointer condition reproduced)"; else echo "(no pointer file)"; fi
mkdir -p /data/oclogs
( timeout 70 openclaw gateway > /data/oclogs/gw.log 2>&1 ) &
GWPID=\$!
for i in \$(seq 1 65); do grep -aq "pull entry armed\|pull entry failed" /data/oclogs/gw.log 2>/dev/null && break; kill -0 "\$GWPID" 2>/dev/null || break; sleep 1; done
sleep 2; kill "\$GWPID" 2>/dev/null
echo "--- arming lines:"
cat /data/oclogs/gw.log 2>/dev/null | grep -aE "\[agent-aimail\]|\[aimail-pull\]" | sort -u | head -20
EOS
    mkdir -p "$WORK/ochome"; chown 1000:1000 "$WORK/ochome" 2>/dev/null || true
    ADDRS_JSON="[$(printf '{"email":"%s","code":"%s"}' "$ADDR_A" "$CODE_A"),$(printf '{"email":"%s","code":"%s"}' "$ADDR_B" "$CODE_B")]"
    docker run --rm --network host --entrypoint /bin/bash \
      -v "$STAGE_OC:/app/pkgstage:ro" \
      -v "$WORK/ochome:/data" \
      -v "$WORK/bind-oc.mjs:/data/bind-oc.mjs:ro" \
      -v "$WORK/oc-side.sh:/data/oc-side.sh:ro" \
      -e GW_URL="$GW_URL" -e LLM_URL="http://127.0.0.1:8000/v1" -e LLM_MODEL="default" \
      -e EXT="/app/pkgstage/node_modules/agent-aimail" -e ADDRS_JSON="$ADDRS_JSON" \
      -e OC_HOME="/data/.openclaw" -e OC_PORT="$OC_PORT" \
      aimail-host-openclaw:latest /data/oc-side.sh >"$WORK/oc-side.log" 2>&1
    tail -25 "$WORK/oc-side.log"
    ARMED="$(grep -aE 'pull entry armed for' "$WORK/oc-side.log" | head -1 || true)"
    AGG="$(grep -aE 'no pointer — aggregate agent:asid_' "$WORK/oc-side.log" | head -1 || true)"
    FAN="$(grep -aE 'pull scope: .*fanout=true' "$WORK/oc-side.log" | head -1 || true)"
    echo "   host armed: ${ARMED:-(absent)}"
    echo "   aggregate : ${AGG:-(absent)}"
    echo "   fanout    : ${FAN:-(absent)}"
    if [ -n "$ARMED" ] && [ -n "$AGG" ] && [ -n "$FAN" ] \
       && echo "$AGG" | grep -q "$ADDR_A" && echo "$AGG" | grep -q "$ADDR_B" \
       && echo "$AGG" | grep -q '2/2 active' \
       && [ "$(echo "$ARMED" | grep -o 'for [0-9]*' | grep -o '[0-9]*')" = "1" ]; then
      echo "   OPENCLAW A2 ARMING (multi-address) PASS"
    else
      echo "   OPENCLAW A2 ARMING (multi-address) FAIL"; FAIL=1
    fi
    ;;
  *) echo "unknown host: $host"; FAIL=1 ;;
  esac
done

echo "== verdict =="
echo "WORK=$WORK (logs kept)"
if [ "$FAIL" -eq 0 ]; then
  echo "REAL-HOST A2 ARMING PASS"
  exit 0
else
  echo "REAL-HOST A2 ARMING FAIL"
  exit 1
fi

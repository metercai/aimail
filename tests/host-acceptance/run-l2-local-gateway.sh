#!/usr/bin/env bash
# Closed-loop L2 with the REAL base gateway binary as a local fixture.
# No external gateway, no Docker, no pre-issued codes: starts a local SMTP sink,
# starts the built base gateway, claims a system code via /apply, then runs the
# host activation + SSE welcome path.
#
# usage: run-l2-local-gateway.sh <pi|openclaw|dsh>
# env: AIMAIL_GATEWAY_BIN (default ../aimail-gateway/target/debug/aimail-gateway)
set -euo pipefail
HOST="${1:?usage: run-l2-local-gateway.sh <pi|openclaw|dsh>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BIN="${AIMAIL_GATEWAY_BIN:-$ROOT/../aimail-gateway/target/debug/aimail-gateway}"
[ -x "$BIN" ] || { echo "gateway binary missing: $BIN"; exit 2; }
WORK="$(mktemp -d -t aimail-local-gw-XXXXXX)"
HTTP_PORT="${HTTP_PORT:-18080}"
SMTP_PORT="${SMTP_PORT:-12525}"
SPOOL="$WORK/mail.spool"
CONF="$WORK/config.toml"
mkdir -p "$WORK/storage" "$WORK/workspace" "$WORK/home"
cat >"$CONF" <<TOML
[http]
bind = "127.0.0.1:$HTTP_PORT"
hostname = "gateway.test"

[smtp]
bind = "127.0.0.1:12526"
hostname = "gateway.test"

[storage]
path = "$WORK/storage"

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
cleanup(){ [ -n "$GW_PID" ] && kill "$GW_PID" 2>/dev/null || true; [ -n "$SINK_PID" ] && kill "$SINK_PID" 2>/dev/null || true; }
trap cleanup EXIT
python3 "$HERE/fixture/smtp_sink.py" --port "$SMTP_PORT" --spool "$SPOOL" >"$WORK/smtp.log" 2>&1 & SINK_PID=$!
"$BIN" -c "$CONF" start >"$WORK/gateway.log" 2>&1 & GW_PID=$!
for _ in $(seq 1 80); do curl -fsS "http://127.0.0.1:$HTTP_PORT/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS "http://127.0.0.1:$HTTP_PORT/health" >/dev/null
ADMIN="$(cat "$WORK/storage/aimail.db.admin_key")"
SID="$(cat "$WORK/storage/system.id")"
node "$ROOT/tests/dsh-acceptance/harness/admin.mjs" --gateway "http://127.0.0.1:$HTTP_PORT"   --key "$ADMIN" --identity "$SID" --system "$SID" --domain test.local
# NO_POINTER=1 reproduces the QA real host: pysdk external activation wrote no
# platform pointer, so the host restart lands on the no-pointer tier-3 fallback.
# On a pre-fix build (0.5.0) the tier-3 V1 predicate rejects the V2 binding
# (no pull armed) => the harness MUST exit non-zero (the B1/B2 negative
# evidence). On the fixed build (0.5.1, grouping) it arms one aggregate stream
# per (AgentSystemId, Gateway) and delivery is received => green.
NOPTR_FLAG=""
if [ "${NO_POINTER:-0}" = "1" ]; then NOPTR_FLAG="--no-pointer"; fi
ACLAIM=$(python3 "$HERE/fixture/claim_local.py" --gateway "http://127.0.0.1:$HTTP_PORT" --spool "$SPOOL" --email "qa-$HOST-addr@example.com" --name "QA $HOST" --domain test.local --kind address --agentname alice)
ADDR_CODE=$(printf '%s' "$ACLAIM" | python3 -c 'import json,sys;print(json.load(sys.stdin)["activation_code"])')
echo "local fixture address code: $ADDR_CODE"
ACLAIM2=$(python3 "$HERE/fixture/claim_local.py" --gateway "http://127.0.0.1:$HTTP_PORT" --spool "$SPOOL" --email "qa-$HOST-addr2@example.com" --name "QA $HOST 2" --domain test.local --kind address --agentname bob)
ADDR_CODE2=$(printf '%s' "$ACLAIM2" | python3 -c 'import json,sys;print(json.load(sys.stdin)["activation_code"])')
if [ "$HOST" = "dsh" ]; then
  H="$ROOT/tests/dsh-acceptance/harness/dsh-harness.mjs"
  if ! node "$H" address --gateway "http://127.0.0.1:$HTTP_PORT" --email alice@test.local --code "$ADDR_CODE" --workspace "$WORK/workspace" --home "$WORK/home" > "$WORK/dsh-address.json" 2> "$WORK/dsh-address.err"; then
    echo "dsh address failed"; cat "$WORK/dsh-address.json" "$WORK/dsh-address.err"; exit 1
  fi
  if ! node "$H" address --gateway "http://127.0.0.1:$HTTP_PORT" --email bob@test.local --code "$ADDR_CODE2" --workspace "$WORK/workspace" --home "$WORK/home" > "$WORK/dsh-address2.json" 2> "$WORK/dsh-address2.err"; then
    echo "dsh second address failed"; cat "$WORK/dsh-address2.json" "$WORK/dsh-address2.err"; exit 1
  fi
  node "$H" hub --gateway "http://127.0.0.1:$HTTP_PORT" --email alice@test.local --timeout 30 --home "$WORK/home" > "$WORK/dsh-hub.json" &
  HP=$!; sleep 2
  node "$H" welcome --gateway "http://127.0.0.1:$HTTP_PORT" --email alice@test.local --home "$WORK/home" > "$WORK/dsh-welcome.json"
  node "$H" welcome --gateway "http://127.0.0.1:$HTTP_PORT" --email bob@test.local --home "$WORK/home" > "$WORK/dsh-welcome2.json"
  wait "$HP"
  python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d["status"]["mode"]=="sse";assert len(d["followups"])>=2;print(json.dumps(d))' "$WORK/dsh-hub.json"
else
  EXTRA=""
  if [ "$HOST" = "openclaw" ]; then
    EXTRA="--second-email bob@test.local --second-code $ADDR_CODE2"
  fi
  DELIVERY=1 node "$HERE/harness.mjs" "$HOST" \
    --gateway "http://127.0.0.1:$HTTP_PORT" \
    --email alice@test.local --workspace "$WORK/workspace" --home "$WORK/home" \
    --domain test.local --address-code "$ADDR_CODE" $EXTRA $NOPTR_FLAG --delivery
fi
[ -n "$NOPTR_FLAG" ] && echo "L2-LOCAL-GATEWAY PASS ($HOST, no-pointer)" || echo "L2-LOCAL-GATEWAY PASS ($HOST)"

#!/usr/bin/env bash
# Two real base gateways + one AgentSystemId. Proves one aggregate stream per
# gateway, with each gateway's pending isolated from the other.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BIN="${AIMAIL_GATEWAY_BIN:-$ROOT/../aimail-gateway/target/debug/aimail-gateway}"
[ -x "$BIN" ] || { echo "gateway binary missing: $BIN"; exit 2; }
WORK="$(mktemp -d -t aimail-two-gw-XXXXXX)"
A_HTTP=18090; B_HTTP=18091; A_SMTP=12536; B_SMTP=12537; SINK_PORT=12525
SPOOL="$WORK/mail.spool"
echo "work: $WORK"
mkdir -p "$WORK/a/storage" "$WORK/b/storage" "$WORK/home" "$WORK/workspace"
python3 "$HERE/fixture/smtp_sink.py" --port "$SINK_PORT" --spool "$SPOOL" >"$WORK/smtp.log" 2>&1 & SINK=$!
mkconf() {
cat >"$1/config.toml" <<TOML
[http]
bind = "127.0.0.1:$2"
hostname = "gateway-$2.test"
[smtp]
bind = "127.0.0.1:$3"
hostname = "gateway-$2.test"
[storage]
path = "$1/storage"
[relay]
smtp_server = "127.0.0.1:$SINK_PORT"
[admin]
email = "admin@test.local"
manager_default = "admin@test.local"
[webhook]
allow_private_targets = true
[retry]
poll_interval_secs = 1
initial_backoff_secs = 1
TOML
}
mkconf "$WORK/a" "$A_HTTP" "$A_SMTP"
mkconf "$WORK/b" "$B_HTTP" "$B_SMTP"
A_PID=""; B_PID=""
cleanup(){ [ -n "$A_PID" ] && kill "$A_PID" 2>/dev/null || true; [ -n "$B_PID" ] && kill "$B_PID" 2>/dev/null || true; kill "$SINK" 2>/dev/null || true; }
trap cleanup EXIT
"$BIN" -c "$WORK/a/config.toml" start >"$WORK/a/gateway.log" 2>&1 & A_PID=$!
"$BIN" -c "$WORK/b/config.toml" start >"$WORK/b/gateway.log" 2>&1 & B_PID=$!
for p in $A_HTTP $B_HTTP; do for _ in $(seq 1 80); do curl -fsS "http://127.0.0.1:$p/health" >/dev/null 2>&1 && break; sleep 0.5; done; curl -fsS "http://127.0.0.1:$p/health" >/dev/null; done
for g in a b; do
  if [ "$g" = a ]; then PORT=$A_HTTP; else PORT=$B_HTTP; fi
  ADMIN="$(cat "$WORK/$g/storage/aimail.db.admin_key")"; SID="$(cat "$WORK/$g/storage/system.id")"
  node "$ROOT/tests/dsh-acceptance/harness/admin.mjs" --gateway "http://127.0.0.1:$PORT" --key "$ADMIN" --identity "$SID" --system "$SID" --domain test.local >/dev/null
done
ACLAIM=$(python3 "$HERE/fixture/claim_local.py" --gateway "http://127.0.0.1:$A_HTTP" --spool "$SPOOL" --email "qa-a@example.com" --name "QA A" --domain test.local --kind address --agentname alice)
BCLAIM=$(python3 "$HERE/fixture/claim_local.py" --gateway "http://127.0.0.1:$B_HTTP" --spool "$SPOOL" --email "qa-b@example.com" --name "QA B" --domain test.local --kind address --agentname bob)
A_CODE=$(printf '%s' "$ACLAIM" | python3 -c 'import json,sys;print(json.load(sys.stdin)["activation_code"])')
B_CODE=$(printf '%s' "$BCLAIM" | python3 -c 'import json,sys;print(json.load(sys.stdin)["activation_code"])')
echo "codes: A=$A_CODE B=$B_CODE"
node "$HERE/two-gateway-harness.mjs" --a "http://127.0.0.1:$A_HTTP" --b "http://127.0.0.1:$B_HTTP" --a-code "$A_CODE" --b-code "$B_CODE" --home "$WORK/home" --workspace "$WORK/workspace"
echo "L2-TWO-GATEWAYS PASS"

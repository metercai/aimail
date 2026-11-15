#!/usr/bin/env bash
# Base user loop: webapp claim -> email (Mailpit) -> tool activation ->
# SSE delivery. Run from tests/dsh-acceptance after `docker compose up -d --build`.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1:18080}"
MAILPIT_URL="${MAILPIT_URL:-http://127.0.0.1:8025}"
HOME_DIR="${HOME_DIR:-$HERE/../.data/qa-home}"
rm -rf "$HOME_DIR"; mkdir -p "$HOME_DIR"
COMPOSE=(docker compose -f "$HERE/../docker-compose.yml")

echo "== wait mailpit"
for i in $(seq 1 60); do curl -fsS "$MAILPIT_URL/api/v1/messages" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "$MAILPIT_URL/api/v1/messages" >/dev/null

echo "== wait base"
for i in $(seq 1 90); do curl -fsS "$BASE_URL/health" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "$BASE_URL/apply" >/dev/null

echo "== bootstrap keys/system id (via tools container)"
ADMIN=$("${COMPOSE[@]}" run --rm -T tools sh -c 'cat /data/base/aimail.db.admin_key')
SID=$("${COMPOSE[@]}" run --rm -T tools sh -c 'cat /data/base/system.id')
echo "system=$SID"
node "$HERE/../harness/admin.mjs" --gateway "$BASE_URL" --key "$ADMIN" --identity "$SID" --system "$SID" --domain test.local
node "$HERE/../harness/admin.mjs" --gateway "$BASE_URL" --key "$ADMIN" --identity "$SID" --system "$SID" --domain second.test

echo "== claim system code via webapp API + Mailpit"
SYSCODE=$(bash "$HERE/claim-base.sh" --gateway "$BASE_URL" --mailpit "$MAILPIT_URL" --email qa-admin@example.com --name Admin --kind system --domain test.local)
echo "system code=$SYSCODE"
node "$HERE/../harness/dsh-harness.mjs" system --gateway "$BASE_URL" --code "$SYSCODE" --domain test.local --manager qa-manager@example.com --home "$HOME_DIR" > "$HERE/../.data/system.json"
cat "$HERE/../.data/system.json"

echo "== claim address code + bind"
ACODE=$(bash "$HERE/claim-base.sh" --gateway "$BASE_URL" --mailpit "$MAILPIT_URL" --email qa-user@example.com --name User --kind address --domain test.local --agentname alice)
echo "address code=$ACODE"
node "$HERE/../harness/dsh-harness.mjs" address --gateway "$BASE_URL" --email alice@test.local --code "$ACODE" --workspace "$HOME_DIR/workspace" --home "$HOME_DIR" | tee "$HERE/../.data/address.json"
grep -q '"success": true' "$HERE/../.data/address.json"

echo "== claim second-domain address (multi-domain)"
BCODE=$(bash "$HERE/claim-base.sh" --gateway "$BASE_URL" --mailpit "$MAILPIT_URL" --email qa-user2@example.com --name User2 --kind address --domain second.test --agentname bob)
node "$HERE/../harness/dsh-harness.mjs" address --gateway "$BASE_URL" --email bob@second.test --code "$BCODE" --workspace "$HOME_DIR/workspace" --home "$HOME_DIR" | tee "$HERE/../.data/address2.json"
grep -q '"success": true' "$HERE/../.data/address2.json"

echo "== SSE delivery to the default worker (manager is auto-whitelisted)"
node "$HERE/../harness/dsh-harness.mjs" hub --gateway "$BASE_URL" --email agent@test.local --timeout 30 --home "$HOME_DIR" > "$HERE/../.data/hub.json" &
HUB=$!
sleep 2
python3 - <<'PY'
import smtplib
from email.message import EmailMessage
m=EmailMessage(); m['From']='qa-manager@example.com'; m['To']='agent@test.local'; m['Subject']='Acceptance loop'; m.set_content('hello acceptance')
s=smtplib.SMTP('127.0.0.1',12525,timeout=10); s.send_message(m); s.quit()
print('smtp sent')
PY
wait "$HUB"
cat "$HERE/../.data/hub.json"
grep -q '"followups": \[' "$HERE/../.data/hub.json"

echo "== base loop PASS"

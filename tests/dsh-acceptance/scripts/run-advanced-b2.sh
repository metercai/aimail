#!/usr/bin/env bash
# Advanced B2 loop: shared-domain address code -> tssdk address bind -> SSE.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADV_URL="${ADV_URL:-http://127.0.0.1:18081}"
MAILPIT_URL="${MAILPIT_URL:-http://127.0.0.1:8025}"
HOME_DIR="${HOME_DIR:-$HERE/../.data/qa-adv-home}"
EMAIL="${EMAIL:-applicant@example.com}"
AGENT="${AGENT:-alice}"
SHARED="${SHARED:-shared.test}"
rm -rf "$HOME_DIR"; mkdir -p "$HOME_DIR"

echo "== wait mailpit"
for i in $(seq 1 60); do curl -fsS "$MAILPIT_URL/api/v1/messages" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "$MAILPIT_URL/api/v1/messages" >/dev/null

echo "== wait advanced"
for i in $(seq 1 120); do curl -fsS "$ADV_URL/health" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "$ADV_URL/health" >/dev/null

echo "== claim shared address code"
CODE=$(bash "$HERE/claim-advanced.sh" --gateway "$ADV_URL" --mailpit "$MAILPIT_URL" --email "$EMAIL" --name Applicant --agentname "$AGENT" --shared-domain "$SHARED")
echo "address code=$CODE"

echo "== bind via tssdk"
node "$HERE/../harness/dsh-harness.mjs" address --gateway "$ADV_URL" --email "$AGENT@$SHARED" --code "$CODE" --workspace "$HOME_DIR/workspace" --home "$HOME_DIR" | tee "$HERE/../.data/adv-address.json"
grep -q '"success": true' "$HERE/../.data/adv-address.json"

echo "== SSE delivery from the applicant (auto-whitelisted manager)"
node "$HERE/../harness/dsh-harness.mjs" hub --gateway "$ADV_URL" --email "$AGENT@$SHARED" --timeout 30 --home "$HOME_DIR" > "$HERE/../.data/adv-hub.json" &
HUB=$!
sleep 2
python3 - <<PY
import smtplib
from email.message import EmailMessage
m=EmailMessage(); m['From']='$EMAIL'; m['To']='$AGENT@$SHARED'; m['Subject']='Advanced B2 acceptance'; m.set_content('hello shared domain')
s=smtplib.SMTP('127.0.0.1',12526,timeout=10); s.send_message(m); s.quit()
print('smtp sent')
PY
wait "$HUB"
cat "$HERE/../.data/adv-hub.json"
grep -q '"followups": \[' "$HERE/../.data/adv-hub.json"

echo "== advanced B2 loop PASS"

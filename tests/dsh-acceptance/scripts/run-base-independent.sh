#!/usr/bin/env bash
# Scenario 5-independent: self-hosted gateway onboarding via a system
# activation code claimed on the gateway's /apply page (NOT an admin/system
# key — keys never appear in the agent conversation). Then scenario 6:
# address binding without an address code, using the system domain key.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1:18080}"
MAILPIT_URL="${MAILPIT_URL:-http://127.0.0.1:8025}"
HOME_DIR="${HOME_DIR:-$HERE/../.data/qa-independent}"
rm -rf "$HOME_DIR"; mkdir -p "$HOME_DIR"

for i in $(seq 1 90); do curl -fsS "$BASE_URL/health" >/dev/null 2>&1 && break; sleep 1; done

echo "== claim a system code on the self-hosted /apply page"
SYSCODE=$(bash "$HERE/claim-base.sh" --gateway "$BASE_URL" --mailpit "$MAILPIT_URL" --email qa-admin@example.com --name Admin --kind system --domain test.local)
echo "system code=$SYSCODE"
node "$HERE/../harness/dsh-harness.mjs" system --gateway "$BASE_URL" --code "$SYSCODE" --domain test.local --manager qa-manager@example.com --home "$HOME_DIR" | tee "$HERE/../.data/independent-system.json"
grep -q '"success": true' "$HERE/../.data/independent-system.json"
grep -q '"kind": "new"' "$HERE/../.data/independent-system.json"

echo "== scenario 6: address after onboarding, no address code"
node "$HERE/../harness/dsh-harness.mjs" address --gateway "$BASE_URL" --email worker@test.local --workspace "$HOME_DIR/workspace" --home "$HOME_DIR" | tee "$HERE/../.data/independent-address.json"
grep -q '"success": true' "$HERE/../.data/independent-address.json"
grep -q '"mode": "register"' "$HERE/../.data/independent-address.json"
echo "== independent/scene-6 PASS"

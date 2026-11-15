#!/usr/bin/env bash
# Scenario 5-independent: system onboarding with an existing system-level key
# (no activation code), then scenario 6 address binding without a code.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1:18080}"
HOME_DIR="${HOME_DIR:-$HERE/../.data/qa-independent}"
COMPOSE=(docker compose -f "$HERE/../docker-compose.yml")
rm -rf "$HOME_DIR"; mkdir -p "$HOME_DIR"

for i in $(seq 1 90); do curl -fsS "$BASE_URL/health" >/dev/null 2>&1 && break; sleep 1; done
SID=$("${COMPOSE[@]}" run --rm -T tools sh -c 'cat /data/base/system.id')
KEY=$("${COMPOSE[@]}" run --rm -T tools sh -c "cat /data/base/${SID}.system.key")
echo "independent system=$SID"
node "$HERE/../harness/dsh-harness.mjs" system --gateway "$BASE_URL" --system_key "$KEY" --system_id "$SID" --domain test.local --home "$HOME_DIR" | tee "$HERE/../.data/independent-system.json"
grep -q '"success": true' "$HERE/../.data/independent-system.json"
grep -q '"kind": "key"' "$HERE/../.data/independent-system.json"

# Scenario 6: address after system onboarding, no address code -> domain key registers it.
node "$HERE/../harness/dsh-harness.mjs" address --gateway "$BASE_URL" --email agent@test.local --workspace "$HOME_DIR/workspace" --home "$HOME_DIR" | tee "$HERE/../.data/independent-address.json"
grep -q '"success": true' "$HERE/../.data/independent-address.json"
grep -q '"mode": "register"' "$HERE/../.data/independent-address.json"
echo "== independent/scene-6 PASS"

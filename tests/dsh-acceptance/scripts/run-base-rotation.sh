#!/usr/bin/env bash
# Re-bind alice with a new address code; base must report rotation and the old
# key must stop working (the upsert replaces key_hash immediately).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1:18080}"
MAILPIT_URL="${MAILPIT_URL:-http://127.0.0.1:8025}"
HOME_DIR="${HOME_DIR:-$HERE/../.data/qa-home}"
CODE=$(bash "$HERE/claim-base.sh" --gateway "$BASE_URL" --mailpit "$MAILPIT_URL" --email user@test.local --name User --kind address --domain test.local --agentname alice)
node "$HERE/../harness/dsh-harness.mjs" address --gateway "$BASE_URL" --email alice@test.local --code "$CODE" --workspace "$HOME_DIR/workspace" --home "$HOME_DIR" | tee "$HERE/../.data/rotation.json"
grep -q '"kind": "rotation"' "$HERE/../.data/rotation.json"
echo "== rotation PASS"

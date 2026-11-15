#!/usr/bin/env bash
# Pause/resume/no-unbind lifecycle on the base loop's alice binding.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${HOME_DIR:-$HERE/../.data/qa-home}"
H="$HERE/../harness/dsh-harness.mjs"
node "$H" lifecycle --email alice@test.local --action pause --home "$HOME_DIR" | tee "$HERE/../.data/pause.json"
grep -q '"paused": true' "$HERE/../.data/pause.json"
node "$H" lifecycle --email alice@test.local --action resume --home "$HOME_DIR" | tee "$HERE/../.data/resume.json"
grep -q '"paused": false' "$HERE/../.data/resume.json"
node "$H" lifecycle --email alice@test.local --action unbind --home "$HOME_DIR" | tee "$HERE/../.data/unbind.json"
grep -q '"error_code": "bad_args"' "$HERE/../.data/unbind.json"
echo "== lifecycle PASS"

#!/usr/bin/env bash
# V6: clear activation_codes in both test databases, then assert an old code is
# rejected. No migration is performed by design (old codes are invalid).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/.."

BASE_URL="${BASE_URL:-http://127.0.0.1:18080}"
OLD_CODE="${OLD_CODE:-sys-55c79006d5b44e34}"

# Compose project name drives the volume names; read it from the running container.
CID="$(docker ps -qf name=base-1 | head -1)"
PROJECT=""
if [ -n "$CID" ]; then
  PROJECT="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$CID" 2>/dev/null || true)"
fi
PROJECT="${PROJECT:-aimail-dsh-acceptance}"

docker run --rm \
  -v "${PROJECT}_base-data:/base" \
  -v "${PROJECT}_advanced-data:/adv" \
  python:3.11-alpine python - <<'PY'
import os
import sqlite3

for path in ("/base/aimail.db", "/adv/aimail.db"):
    if not os.path.exists(path):
        print("missing", path)
        continue
    conn = sqlite3.connect(path)
    rows = conn.execute("select count(*) from activation_codes").fetchone()[0]
    conn.execute("delete from activation_codes")
    conn.commit()
    conn.close()
    print("cleared", path, rows, "rows")
PY

status="$(curl -s -o /tmp/v6-preflight.json -w '%{http_code}' -X POST \
  -H 'content-type: application/json' \
  -d "{\"code\":\"${OLD_CODE}\"}" \
  "${BASE_URL}/api/v1/public/activation-code/preflight" || true)"
echo "preflight(${OLD_CODE}) -> ${status} $(head -c 200 /tmp/v6-preflight.json 2>/dev/null || true)"

# The preflight API signals an invalid code with HTTP 200 and {"valid": false};
# assert on the body, not on the status code.
python3 -c '
import json, sys
raw = open("/tmp/v6-preflight.json").read()
try:
    body = json.loads(raw)
except Exception:
    print("V6 FAIL: preflight body is not JSON:", raw[:120]); sys.exit(1)
if body.get("valid") is False:
    print("V6 PASS: old code rejected (valid=false)")
    sys.exit(0)
print("V6 FAIL: expected valid=false, got:", body)
sys.exit(1)
'

#!/usr/bin/env bash
# Closed-loop host L2: start the repository's self-hosted gateway fixture,
# claim a system code from /apply via Mailpit, activate it through the host
# tool, then run the optional live welcome check. No external gateway/codes.
#
# usage: run-l2-fixture.sh <pi|openclaw>
# Requires Docker (the same fixture as tests/dsh-acceptance/docker-compose.yml).
set -euo pipefail
HOST="${1:?usage: run-l2-fixture.sh <pi|openclaw>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
COMPOSE="$ROOT/tests/dsh-acceptance/docker-compose.yml"
GW="${GATEWAY_URL:-http://127.0.0.1:18080}"
MAILPIT="${MAILPIT_URL:-http://127.0.0.1:8025}"
WORK="$(mktemp -d -t aimail-host-l2-XXXXXX)"
cleanup() { docker compose -f "$COMPOSE" down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker compose -f "$COMPOSE" up -d --build mailpit base
for _ in $(seq 1 120); do
  curl -fsS "$GW/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "$MAILPIT/api/v1/messages" >/dev/null
SYS_CODE=$(bash "$ROOT/tests/dsh-acceptance/scripts/claim-base.sh" \
  --gateway "$GW" --mailpit "$MAILPIT" \
  --email "qa-${HOST}-sys@example.com" --name "QA ${HOST}" \
  --kind system --domain test.local)
echo "system code: $SYS_CODE"
DELIVERY=1 node "$HERE/harness.mjs" "$HOST" \
  --gateway "$GW" --system-code "$SYS_CODE" \
  --email alice@test.local --workspace "$WORK/workspace" --home "$WORK" \
  --domain test.local
echo "L2-FIXTURE PASS ($HOST)"

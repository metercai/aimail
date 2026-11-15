#!/usr/bin/env bash
# V6: clear activation_codes in both test databases. Old codes must then be
# rejected (400/404); this is the explicit "no migration" reset from the plan.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=(docker compose -f "$HERE/../docker-compose.yml")
"${COMPOSE[@]}" run --rm -T tools sh -c '
  set -e
  apk add --no-cache sqlite >/dev/null 2>&1
  for db in /data/base/aimail.db /data/advanced/aimail.db; do
    if [ -f "$db" ]; then
      sqlite3 "$db" "DELETE FROM activation_codes;"
      echo "cleared $db"
    fi
  done
'

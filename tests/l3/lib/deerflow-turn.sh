#!/usr/bin/env bash
# deerflow-turn.sh — drive ONE deerflow agent turn from inside the host container.
#
# Why a driver: deerflow is a service (langgraph backend + gateway), not a CLI. Its only
# product surface is HTTP, so a prompt-driven test must (a) boot the backend and (b) post a
# message through the same API a user would. Both steps are lifted from the L2 fixture
# (tests/host-acceptance/v2/hosts/deerflow.sh), which is why the ports/env names here look
# familiar: PYTHONPATH=., DEER_FLOW_AUTH_DISABLED=1, AIMAIL_HOME, ./.venv/bin/python.
#
# Usage (inside the container): bash deerflow-turn.sh <prompt-file> <drive-script> [port] [wait]
# Exit 0 = the backend came up and the run was submitted.
set -uo pipefail
PROMPT_FILE="${1:?prompt file}"
DRIVE="${2:?deerflow_drive.py path}"
PORT="${3:-39201}"
WAIT="${4:-300}"

cd /app/backend || { echo "no /app/backend"; exit 1; }
export PYTHONPATH=. DEER_FLOW_AUTH_DISABLED=1 AIMAIL_HOME=/app/.aimail
export AIMAIL_DEERFLOW_API="http://127.0.0.1:$PORT"

# uvicorn binds the port we tell it to; the app ALSO logs its own internal gateway port,
# so readiness parses "Uvicorn running on http://…:<port>" and then probes /openapi.json.
nohup ./.venv/bin/python -m uvicorn app.gateway.app:app --host 127.0.0.1 --port "$PORT" \
  >/tmp/df.log 2>&1 &
DF_PID=$!
ap=""
for _ in $(seq 1 150); do
  ap="$(sed -n "s/.*Uvicorn running on http:\/\/[^:]*:\([0-9][0-9]*\).*/\1/p" /tmp/df.log | head -1)"
  if [ -n "$ap" ] && ./.venv/bin/python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:$ap/openapi.json', timeout=2)" 2>/dev/null; then
    break
  fi
  kill -0 "$DF_PID" 2>/dev/null || { echo "deerflow backend exited"; tail -25 /tmp/df.log; exit 1; }
  sleep 2
done
[ -n "$ap" ] || { echo "deerflow backend not ready in 300s"; tail -25 /tmp/df.log; exit 1; }
echo "api_port=$ap"

THREAD="aimail-bootstrap-$$"
./.venv/bin/python "$DRIVE" --url "http://127.0.0.1:$ap" \
  turn --thread "$THREAD" --text "$(cat "$PROMPT_FILE")" --wait "$WAIT" --tail 3000
rc=$?

# The run is a langgraph job: submitting it returns while it is still 'pending'. Keep polling
# until an assistant message lands (or the deadline), otherwise "pending" is all the evidence
# we ever get and the harness cannot tell a slow turn from a stuck one.
deadline=$(( $(date +%s) + WAIT ))
seen=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  msgs="$(./.venv/bin/python "$DRIVE" --url "http://127.0.0.1:$ap" messages --thread "$THREAD" 2>/dev/null)"
  if printf '%s' "$msgs" | grep -qiE "^\s*(ai|assistant)|'type': 'ai'"; then
    seen=1
    echo "--- assistant messages ---"
    printf '%s\n' "$msgs" | tail -40
    break
  fi
  sleep 10
done
echo "assistant_seen=$seen"
exit $rc

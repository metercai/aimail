#!/usr/bin/env bash
# bootstrap-matrix.sh — run the prompt-driven SDK bootstrap loop on every host that CAN do it,
# and print one matrix. This is the thing QA re-runs; boot-loop.sh is the per-host engine.
#
# Owner rulings baked in (2026-11-27):
#   * deerflow is N/A — its lead agent has no shell and no delegating executor, so it is skipped
#     by design, not reported as a failure.
#   * the route does not matter: PASS = the SDK landed in the host's own environment.
#
# Requirements: docker, a built gateway binary, LLM_API_KEY (never printed), and the host images
# (tests/agent-env/docker/rebuild-hosts.sh). A gateway is started on a private port and stopped
# again, so this can run next to a developer's own gateway.
#
# Usage: LLM_API_KEY=… bash tests/l3/bootstrap-matrix.sh [host ...]
#        env knobs: GW_BIN, GW_CONFIG, GW_PORT (default 38992), TMO (default 420)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOSTS=("$@"); [ ${#HOSTS[@]} -gt 0 ] || HOSTS=(pi openclaw dsh hermes)
PORT="${GW_PORT:-38992}"
TMO="${TMO:-420}"
GW_BIN="${GW_BIN:-/home/ubuntu/aimail-gateway/target/release/aimail-gateway}"
GW_CONFIG_SRC="${GW_CONFIG:-/home/ubuntu/aimail-gateway/config.toml}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/evidence/bootstrap-matrix/$STAMP"
mkdir -p "$OUT"

say() { printf '%s\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

[ -n "${LLM_API_KEY:-}" ] || { bad "LLM_API_KEY is empty — no agent turn can run"; exit 2; }
[ -x "$GW_BIN" ] || { bad "gateway binary not found: $GW_BIN (set GW_BIN)"; exit 2; }
[ -f "$GW_CONFIG_SRC" ] || { bad "gateway config not found: $GW_CONFIG_SRC (set GW_CONFIG)"; exit 2; }

# private gateway: same config, other ports, throwaway db
WORK="$(mktemp -d /tmp/bmat.XXXXXX)"
sed -e "s#127.0.0.1:8000#127.0.0.1:$PORT#" -e "s#127.0.0.1:10026#127.0.0.1:$((PORT + 1000))#" \
  "$GW_CONFIG_SRC" > "$WORK/config.toml"
"$GW_BIN" -c "$WORK/config.toml" --db "$WORK/gw.db" --pid-file "$WORK/gw.pid" \
  > "$WORK/gw.log" 2>&1 &
GW_PID=$!
cleanup() { kill "$GW_PID" 2>/dev/null || true; wait "$GW_PID" 2>/dev/null || true; }
trap cleanup EXIT
for _ in $(seq 1 30); do
  curl -s -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -s -m 3 "http://127.0.0.1:$PORT/api/v1/agent-setup/bootstrap?shell=sh" | head -1 | grep -q '^#!' \
  || { bad "gateway did not serve the bootstrap script on :$PORT"; exit 2; }
ok "gateway up on :$PORT (serving the bootstrap)"

RESULTS=()
for h in "${HOSTS[@]}"; do
  say ""
  say "════ $h"
  mkdir -p "$OUT/$h"
  if bash "$ROOT/tests/l3/boot-loop.sh" --host "$h" --docker "aimail-host-$h:latest" \
        --gw-port "$PORT" --timeout "$TMO" > "$OUT/$h/boot-loop.log" 2>&1; then
    RESULTS+=("PASS|$h")
    ok "$h PASS"
  else
    rc=$?
    if grep -q "N/A" "$OUT/$h/boot-loop.log" 2>/dev/null; then
      RESULTS+=("N/A|$h")
      say "  · $h N/A (host cannot self-bootstrap)"
    else
      RESULTS+=("FAIL|$h")
      bad "$h FAIL (rc=$rc)"
    fi
  fi
  grep -aE "^  (✓|✗)|boot-loop (PASS|FAIL)|AIMAIL_BOOTSTRAP ok" "$OUT/$h/boot-loop.log" | tail -6 | sed 's/^/    /'
done

say ""
say "──────── matrix ($STAMP)"
for r in "${RESULTS[@]}"; do printf '  %-5s %s\n' "${r%%|*}" "${r##*|}"; done
say "evidence: $OUT"

for r in "${RESULTS[@]}"; do [ "${r%%|*}" = FAIL ] && exit 1; done
exit 0

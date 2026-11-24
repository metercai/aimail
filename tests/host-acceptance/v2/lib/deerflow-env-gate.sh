#!/usr/bin/env bash
# deerflow-env-gate.sh — E-track gate: is the deer-flow L2 host environment ready?
#
# "Ready" is not "an image exists". It means, in this order:
#   1. pin coherence  — pins.env DEERFLOW_REV ⇄ source checkout ⇄ local base image
#   2. clean image    — no AIMail SDK anywhere in it (not even a cache entry)
#   3. gate endpoint  — config.yaml points at the gate LLM endpoint, key NOT baked in
#   4. host boots     — Gateway answers /openapi.json with DEER_FLOW_AUTH_DISABLED=1
#   5. extension path — a `plugins:` record loads, install(registry, config) runs, and a
#                       registered ModelTool reaches the host (marker written by install)
#   6. run API        — POST /api/threads + POST /api/threads/{tid}/runs accepted (run_id)
#   7. model calls the tool — needs LLM_API_KEY; without it this gate reports BLOCKED,
#      never PASS (an env that never ran a turn is unproven, not green).
#
# Usage:
#   bash tests/host-acceptance/v2/lib/deerflow-env-gate.sh                 # full gate
#   bash tests/host-acceptance/v2/lib/deerflow-env-gate.sh --no-llm        # steps 1-6
#   LLM_API_KEY=… bash tests/host-acceptance/v2/lib/deerflow-env-gate.sh   # steps 1-7
#
# Exit: 0 = every step judged PASS · 3 = 1-6 PASS but the model turn was not run (BLOCKED,
#       no credential) · 1 = a step FAILED (fail-closed; never downgraded to a skip).
set -uo pipefail

V2="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"          # …/tests/host-acceptance/v2
ROOT="$(cd "$V2/../../.." && pwd)"                             # repo root
IMAGE="${DEERFLOW_IMAGE:-aimail-host-deerflow:latest}"
NO_LLM=0
PORT="${DF_PORT:-18077}"
for a in "$@"; do
  case "$a" in
    --no-llm) NO_LLM=1 ;;
    -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

N_PASS=0; N_FAIL=0
pass(){ echo "  PASS  $*"; N_PASS=$((N_PASS+1)); }
fail(){ echo "  FAIL  $*"; N_FAIL=$((N_FAIL+1)); }
step(){ echo; echo "── $*"; }

step "1/7 pin coherence (pins.env ⇄ source ⇄ local base image)"
if bash "$ROOT/tests/agent-env/docker/rebuild-deerflow-base.sh" --check >/tmp/df-pin.log 2>&1; then
  sed 's/^/        /' /tmp/df-pin.log | tail -5; pass "pin coherent"
else
  sed 's/^/        /' /tmp/df-pin.log | tail -6; fail "pin incoherent (see /tmp/df-pin.log)"
fi

step "2/7 clean image (no SDK, not even a cache entry)"
if bash "$ROOT/tests/agent-env/audit-hosts.sh" --clean-only "$IMAGE:deerflow" >/tmp/df-clean.log 2>&1; then
  grep -E "✓|✗" /tmp/df-clean.log | sed 's/^/        /'; pass "image clean"
else
  grep -E "✓|✗" /tmp/df-clean.log | sed 's/^/        /'; fail "image carries our SDK (see /tmp/df-clean.log)"
fi

step "3/7 gate endpoint in config.yaml, key not baked in"
. "$ROOT/tests/agent-env/pins.env"
cfg="$(docker run --rm --entrypoint bash "$IMAGE" -lc 'cat /app/config.yaml' 2>/dev/null)"
base_url="$(printf '%s\n' "$cfg" | sed -n 's/^ *base_url: *//p' | head -1)"
api_key="$(printf '%s\n' "$cfg" | sed -n 's/^ *api_key: *//p' | head -1)"
model="$(printf '%s\n' "$cfg" | sed -n 's/^ *model: *//p' | head -1)"
[ "$base_url" = "https://api.xiaomimimo.com/v1" ] \
  && pass "endpoint is the gate endpoint ($base_url)" \
  || fail "endpoint is not the gate endpoint: ${base_url:-<missing>}"
case "$api_key" in
  ""|__LLM_API_KEY__) pass "no credential baked in (placeholder '${api_key:-<empty>}')" ;;
  *) fail "a credential-looking value is baked in: '${api_key}'" ;;
esac
[ -n "$model" ] && pass "model declared ($model)" || fail "config.yaml declares no model"

step "4/7 host boots with DEER_FLOW_AUTH_DISABLED=1"
CID="dfenv-$$"
cleanup(){ docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT
# The probe extension only needs to be importable; dirs are mounted, never written into.
docker run -d --network host --name "$CID" \
  -v "$V2/lib:/opt/v2lib:ro" \
  -e DEER_FLOW_AUTH_DISABLED=1 \
  -e AIMAIL_DF_PROBE_LOG=/tmp/df-probe.log \
  --entrypoint bash "$IMAGE" -lc '
    set -u
    # 1) declare the probe extension (config.yaml `plugins:` is the host mechanism)
    python3 - <<PY
import yaml, sys
p = "/app/config.yaml"
cfg = yaml.safe_load(open(p)) or {}
rows = [r for r in (cfg.get("plugins") or []) if not (isinstance(r, dict) and r.get("name") == "aimail-probe")]
rows.append({"name": "aimail-probe", "use": "deerflow_probe_ext:install",
              "enabled": True, "required": False, "config": {"token": "env-gate"}})
cfg["plugins"] = rows
open(p, "w").write(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
print("plugins declared:", [r.get("name") for r in rows])
PY
    # 2) start the Gateway exactly as its own CMD does, plus the fixture path
    cd /app/backend
    export PYTHONPATH=.:/opt/v2lib
    exec ./.venv/bin/python -m uvicorn app.gateway.app:app --host 127.0.0.1 --port '"$PORT"' >/tmp/df-gw.log 2>&1
  ' >/dev/null 2>&1 || { fail "container did not start"; docker rm -f "$CID" >/dev/null 2>&1; }

up=0
for _ in $(seq 1 60); do
  if docker exec "$CID" bash -lc "test -f /tmp/df-gw.log" 2>/dev/null \
     && docker exec "$CID" bash -lc "grep -q 'Application startup complete' /tmp/df-gw.log" 2>/dev/null; then up=1; break; fi
  sleep 2
done
if [ "$up" = 1 ]; then
  pass "gateway reported 'Application startup complete'"
else
  fail "gateway never became ready"; docker exec "$CID" bash -lc 'tail -25 /tmp/df-gw.log' 2>&1 | sed 's/^/        /'
fi

step "5/7 extension path (plugins: record → install() → ModelTool)"
# "install() ran" and "install() succeeded" are different facts: the first marker line is
# written before the host API is touched, so accepting it would green-light a failing
# installer (measured 2026-11-23: install had run, the tool never reached the model).
if docker exec "$CID" bash -lc 'test -f /tmp/df-probe.log && cat /tmp/df-probe.log' 2>/dev/null | grep -q '"event": "install_ok"'; then
  docker exec "$CID" bash -lc 'cat /tmp/df-probe.log' | sed 's/^/        /'
  pass "install(registry, config) accepted the contribution (install_ok)"
else
  docker exec "$CID" bash -lc 'cat /tmp/df-probe.log 2>/dev/null' | sed 's/^/        /'
  docker exec "$CID" bash -lc 'grep -iE "extension|plugin|install\(\)" /tmp/df-gw.log | tail -12' 2>&1 | sed 's/^/        /'
  fail "the declared extension did not install successfully (no install_ok marker)"
fi
step "6/7 run API accepted a mail-shaped run (thread create + run accept)"
if docker exec "$CID" python3 /opt/v2lib/deerflow_drive.py --url "http://127.0.0.1:$PORT" probe 2>&1 | sed 's/^/        /' | tee /tmp/df-probe-api.log \
   && grep -q "^        ok   run " /tmp/df-probe-api.log; then
  pass "POST /api/threads + /api/threads/{tid}/runs returned a run record"
else
  fail "run API did not accept a run (see /tmp/df-probe-api.log)"
fi

step "7/7 model turn actually calls the registered tool"
if [ "$NO_LLM" = 1 ] || [ -z "${LLM_API_KEY:-}" ]; then
  echo "        BLOCKED: no LLM_API_KEY in this shell — set it and re-run to prove the turn"
  echo
  echo "──────────────────────────────────────────────"
  printf 'deerflow env gate: PASS=%d FAIL=%d (steps 1-6 judged; step 7 BLOCKED)\n' "$N_PASS" "$N_FAIL"
  [ "$N_FAIL" = 0 ] && { echo "VERDICT: BLOCKED (environment ready, model turn unproven)"; exit 3; }
  echo "VERDICT: FAIL"; exit 1
fi
# The key is injected at run time only (nothing is baked into the image).
docker exec "$CID" bash -lc "LLM_API_KEY='$LLM_API_KEY' bash /opt/agent-env/set-llm-key.sh deerflow" 2>&1 | sed 's/^/        /'
TID="aimail-env-gate-$(date +%s)"
if docker exec "$CID" python3 /opt/v2lib/deerflow_drive.py --url "http://127.0.0.1:$PORT" \
      turn --thread "$TID" --idem "aimail-env-gate-$TID" \
      --text 'Use the environment self-test tool once (the tool whose description says it echoes a value back) with value "env-gate-ok", then reply with its result.' \
      --wait 240 --need "" --tail 1200 2>&1 | sed 's/^/        /' | tee /tmp/df-turn.log; then
  if docker exec "$CID" bash -lc 'grep -q "\"event\": \"call\"" /tmp/df-probe.log' 2>/dev/null; then
    docker exec "$CID" bash -lc 'grep "event\": \"call" /tmp/df-probe.log' | sed 's/^/        /'
    pass "model invoked aimail_env_probe (marker recorded, including thread_id)"
  else
    fail "no tool-call marker: the model turn produced no tool call"
  fi
else
  fail "the model turn did not complete (see /tmp/df-turn.log)"
fi

echo
echo "──────────────────────────────────────────────"
printf 'deerflow env gate: PASS=%d FAIL=%d\n' "$N_PASS" "$N_FAIL"
if [ "$N_FAIL" = 0 ]; then echo "VERDICT: PASS (environment ready + model really called the tool)"; exit 0; fi
echo "VERDICT: FAIL"; exit 1

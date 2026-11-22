#!/usr/bin/env bash
# L2 matrix runner — dev-build SDK pre-installed into pinned host images,
# against the PUBLISHED gateway binary (pins.env).
#
#   run-matrix.sh [--host dsh|pi|openclaw|all] [--stages "s1 s2"]
#
# Round 1 covers S1 (activation) + S2 (arming). S3/S4/S5/negatives are listed
# as not-yet-covered (INFO) rather than silently skipped.
set -uo pipefail
V2="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AIMAIL_ROOT="$(cd "$V2/../.." && pwd)"
# shellcheck source=lib/assert.sh
. "$V2/lib/assert.sh"
. "$V2/lib/evidence.sh"
. "$V2/lib/gw.sh"

HOSTS="all"; STAGES="${STAGES:-s1 s2}"
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOSTS="$2"; shift 2 ;;
    --stages) STAGES="$2"; shift 2 ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
done
[ "$HOSTS" = all ] && HOSTS="dsh pi openclaw"

: "${LLM_API_KEY:?LLM_API_KEY (XIAOMI_API_KEY) must be exported for real-host L2}"
WORK="$(mktemp -d -t aimail-l2-XXXXXX)"
export GW_HTTP_PORT="${GW_HTTP_PORT:-18080}" GW_SMTP_PORT="${GW_SMTP_PORT:-12525}"

# ── dev build (L2 = current dev SDK) ──────────────────────────────────────────────
if [ -z "${SDK_TARBALL_DIR:-}" ]; then
  SDK_TARBALL_DIR="$WORK/sdkpack"; mkdir -p "$SDK_TARBALL_DIR"
  echo "── building dev SDK (pnpm build + pack)"
  ( cd "$AIMAIL_ROOT/tssdk" && pnpm install --frozen-lockfile >/dev/null 2>&1 && pnpm build >/dev/null 2>&1 ) \
    || { echo "GAP: dev SDK build failed"; exit 2; }
  ( cd "$AIMAIL_ROOT/tssdk/packages/agent-aimail" && npm pack --pack-destination "$SDK_TARBALL_DIR" >/dev/null 2>&1 ) \
    || { echo "GAP: npm pack failed"; exit 2; }
fi
export SDK_TARBALL_DIR
SDK_VER="$(cd "$AIMAIL_ROOT/tssdk/packages/agent-aimail" && node -p "require('./package.json').version")"
echo "── L2 dev SDK $SDK_VER  ($(ls "$SDK_TARBALL_DIR" | head -1))"

# ── published gateway (both layers, owner rule 5) ─────────────────────────────────
GATEWAY_VERSION="$(_gw_pin GATEWAY_VERSION)"
echo "── published gateway $GATEWAY_VERSION ($(gw_platform))"
gw_up "$WORK/gw" "$GW_HTTP_PORT" "$GW_SMTP_PORT" || exit 2
trap 'gw_down' EXIT
echo "   gateway=$GW_URL system=$GW_SID"

EV_ROOT="${EV_ROOT:-$AIMAIL_ROOT/evidence/l2}"; export EV_ROOT
for h in $HOSTS; do
  . "$V2/hosts/$h.sh"
  out="$WORK/$h"; mkdir -p "$out"
  apply_out="$(python3 "$V2/lib/gw.py" apply --gateway "$GW_URL" \
      --email "l2-$h-$RANDOM@example.com" --name "L2 $h" --agentname "l2$h" --domain test.local --spool "$WORK/gw/mail.spool")"
  echo "$apply_out" | grep -q '"ok": true' || { echo "GAP($h): /apply failed: $apply_out"; N_GAP=$((N_GAP+1)); continue; }
  addr="$(printf '%s' "$apply_out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["address"])')"
  code="$(printf '%s' "$apply_out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["activation_code"])')"
  echo "── $h → $addr"
  ev_init; ev_meta host "$h"; ev_meta sdk_version "$SDK_VER"; ev_meta gateway_version "$GATEWAY_VERSION"; ev_meta address "$addr"
  for st in $STAGES; do
    case "$st" in
      s1) . "$V2/cases/s1_activation.sh" "$out" "$addr" "$code" ;;
      s2) . "$V2/cases/s2_arming.sh" "$out" ;;
    esac
  done
  ev_log "$out/host.log" 300
  ev_binding "$out/binding.json"
done
echo
echo "not yet covered in round 1: s3 delivery · s4 multi-address · s5 lifecycle · negatives"
summary

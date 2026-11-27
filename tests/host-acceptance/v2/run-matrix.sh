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

HOSTS="all"; STAGES="${STAGES:-s1 s2 s3}"; SDK_KIND="${SDK_KIND:-ts}"
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOSTS="$2"; shift 2 ;;
    --stages) STAGES="$2"; shift 2 ;;
    --sdk) SDK_KIND="$2"; shift 2 ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
done
case "$SDK_KIND" in
  ts) [ "$HOSTS" = all ] && HOSTS="dsh pi openclaw" ;;
  py) [ "$HOSTS" = all ] && HOSTS="hermes deerflow" ;;
  none) [ "$HOSTS" = all ] && HOSTS="dsh pi openclaw hermes deerflow" ;;
  *) echo "unknown --sdk $SDK_KIND (ts|py|none)"; exit 2 ;;
esac
: "${LLM_API_KEY:?LLM_API_KEY (XIAOMI_API_KEY) must be exported for real-host L2}"
WORK="$(mktemp -d -t aimail-l2-XXXXXX)"
export GW_HTTP_PORT="${GW_HTTP_PORT:-18080}" GW_SMTP_PORT="${GW_SMTP_PORT:-12525}"

# ── dev build (L2 = current dev SDK) ──────────────────────────────────────────────
if [ "$SDK_KIND" = py ]; then
  # pysdk hosts: the wheel IS the dev build (L2 rule: current working tree, pre-installed).
  . "$V2/lib/pysdk.sh"
  pysdk_build "$WORK" || exit 2
elif [ -z "${SDK_TARBALL_DIR:-}" ]; then
  SDK_TARBALL_DIR="$WORK/sdkpack"; mkdir -p "$SDK_TARBALL_DIR"
  echo "── building dev SDK (pnpm build + pack)"
  # Never swallow the reason: a frozen-lockfile mismatch or a tsc error must be readable
  # in the log, otherwise the only output is "GAP: dev SDK build failed" (hit 2026-11-23
  # when package.json dependencies changed but pnpm-lock.yaml did not).
  _sdk_build_log="$WORK/sdk-build.log"
  ( cd "$AIMAIL_ROOT/tssdk" && pnpm install --frozen-lockfile && pnpm build ) >"$_sdk_build_log" 2>&1 \
    || { echo "GAP: dev SDK build failed — tail of pnpm install/build ($_sdk_build_log):"; tail -12 "$_sdk_build_log" | sed 's/^/    /'; exit 2; }
  ( cd "$AIMAIL_ROOT/tssdk/packages/agent-aimail" && npm pack --pack-destination "$SDK_TARBALL_DIR" >/dev/null 2>&1 ) \
    || { echo "GAP: npm pack failed for agent-aimail"; exit 2; }
fi
export SDK_TARBALL_DIR
if [ "$SDK_KIND" = py ]; then
  echo "── L2 dev SDK $SDK_VER  ($(basename "$SDK_FILE"))  [pysdk]"
else
  SDK_VER="$(cd "$AIMAIL_ROOT/tssdk/packages/agent-aimail" && node -p "require('./package.json').version")"
  echo "── L2 dev SDK $SDK_VER  ($(ls "$SDK_TARBALL_DIR" | head -1))"
fi

# ── published gateway (both layers, owner rule 5) ─────────────────────────────────
GATEWAY_VERSION="$(_gw_pin GATEWAY_VERSION)"
echo "── published gateway $GATEWAY_VERSION ($(gw_platform))"
gw_up "$WORK/gw" "$GW_HTTP_PORT" "$GW_SMTP_PORT" || exit 2
trap 'gw_down' EXIT
echo "   gateway=$GW_URL system=$GW_SID"

EV_ROOT="${EV_ROOT:-$AIMAIL_ROOT/evidence/l2}"; export EV_ROOT
for h in $HOSTS; do
  # Host capability vars must not leak from the previous host: hosts are SOURCED into this shell,
  # so a value set by pi (single-address) was silently used to judge openclaw (multi-address) and
  # produced a false C1 RED (measured 2026-11-27: b1=1, b2=2 coexisted yet the case took the
  # single-address branch). Reset the trio before each host adapter is loaded.
  unset HOST_ADDRESS_MODEL HOST_CAN_ALLOCATE_SESSION HOST_DELIVERY_CONFIRMATION
  . "$V2/hosts/$h.sh"
  out="$WORK/$h"; mkdir -p "$out"
  apply_out="$(python3 "$V2/lib/gw.py" apply --gateway "$GW_URL" \
      --email "l2-$h-$RANDOM@example.com" --name "L2 $h" --agentname "l2$h" --domain test.local --spool "$WORK/gw/mail.spool")"
  if ! printf '%s' "$apply_out" | grep -q '"ok": true'; then
    CASE="$h apply"; gap "/apply failed: $apply_out"; continue
  fi
  addr="$(printf '%s' "$apply_out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["address"])')"
  code="$(printf '%s' "$apply_out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["activation_code"])')"
  token="L2TOK-$h-$(date +%s)-$RANDOM"
  S4_ADDR=""; S4_CODE=""; LIFECYCLE=0
  case " $STAGES " in *" s4 "*)
    # EVERY host gets a second code, including the single-address ones: C1 is exactly
    # "refuse the second address WITHOUT consuming the code", and a host that is never
    # asked cannot demonstrate it. pi used to be skipped here, so its s4 assertion was a
    # trivial pass that never reached the refusal path.
    if true; then
      a2="$(python3 "$V2/lib/gw.py" apply --gateway "$GW_URL" --email "l2-$h-2-$RANDOM@example.com" \
            --name "L2 $h 2" --agentname "l2${h}2" --domain test.local --spool "$WORK/gw/mail.spool")"
      printf '%s' "$a2" | grep -q '"ok": true' || echo "   (S4 second claim failed: $a2)" >&2
      printf '%s' "$a2" | grep -q '"ok": true' && {
        S4_ADDR="$(printf '%s' "$a2" | python3 -c 'import json,sys;print(json.load(sys.stdin)["address"])')"
        S4_CODE="$(printf '%s' "$a2" | python3 -c 'import json,sys;print(json.load(sys.stdin)["activation_code"])')"
      }
    fi ;;
  esac
  case " $STAGES " in *" s5 "*) LIFECYCLE=1 ;; esac
  # LIFECYCLE=1 is a JOURNEY VARIANT: the adapters then skip the whitelist, the S3 injection and
  # the gateway boot (they only run the pause/unbind turns). Asking for s5 together with any
  # stage that needs delivery/threading/anchor in the same pass therefore cannot work -- and it
  # fails silently as RED/GAP (measured 2026-11-27: S3 'journey never reached the mail stage',
  # S6 'acked=0', S7/S8 'no evidence file', all in a run whose stage list also contained s5).
  # Refuse instead of mis-reporting.
  if [ "$LIFECYCLE" = 1 ]; then
    case " $STAGES " in
      *" s3 "*|*" s6 "*|*" s7 "*|*" s8 "*)
        echo "GAP: s5 (LIFECYCLE=1) cannot share a pass with s3/s6/s7/s8 — run them as two passes:" >&2
        echo "       run-matrix.sh --stages \"s1 s2 s3 s4 s6 s7 s8 neg\"" >&2
        echo "       run-matrix.sh --stages \"s5\"" >&2
        exit 2 ;;
    esac
  fi
  export S4_ADDR S4_CODE LIFECYCLE
  echo "── $h → $addr"
  ev_init; ev_meta host "$h"; ev_meta sdk_version "$SDK_VER"; ev_meta sdk_kind "$SDK_KIND"; ev_meta gateway_version "$GATEWAY_VERSION"; ev_meta address "$addr"
  export STAGES   # adapters need to know whether s3 (live delivery) is in this journey
  # A container left over from an earlier run keeps pulling and lands mail in *its own* session, which
  # shows up as a phantom new session in s6 (seen 2026-11-25: two 37h-old containers, sessions 8->9->12).
  docker ps -q --filter "ancestor=aimail-host-$h" 2>/dev/null | xargs -r docker rm -f >/dev/null 2>&1 || true
  host_journey "$out" "$addr" "$code" "$token"
  _wait_ev() {  # _wait_ev <file> <seconds>: wait for host-side evidence, bounded
    local f="$1" n="${2:-300}" i=0
    while [ "$i" -lt "$n" ]; do [ -s "$f" ] && return 0; i=$((i+1)); sleep 1; done
    return 1
  }
  for st in $STAGES; do
    case "$st" in
      s1) . "$V2/cases/s1_activation.sh" "$out" ;;
      s2) . "$V2/cases/s2_arming.sh" "$out" ;;
      s3) . "$V2/cases/s3_delivery.sh" "$out" ;;
      s4) . "$V2/cases/s4_multiaddr.sh" "$out" "$h" ;;
      s5) . "$V2/cases/s5_lifecycle.sh" "$out" ;;
      # s6/s7/s8 evidence is written by the host_journey INSIDE the container, which keeps running
      # after /out/ready: the drivers come after it (they need the binding, the whitelist and a live
      # session store). The cases used to run the moment s5 finished, so the files were not there yet
      # and every such obligation came back as GAP even on hosts that produce them. Wait for the file
      # (bounded), never for a fixed sleep.
      s6) _wait_ev "$out/s6.json" 420; . "$V2/cases/s6_threading.sh" "$out" ;;   # contract C8 host side (evidence from host_journey)
      s7) _wait_ev "$out/s7_gone.json" 420; . "$V2/cases/s7_session_gone.sh" "$out" ;;  # contract C17 session-gone downgrade (host hook)
      s8) _wait_ev "$out/s8_anchor.json" 420; . "$V2/cases/s8_anchor_isolation.sh" "$out" ;;  # contract C16 anchor isolation (host hook)
      neg) host_negative "$out" "$addr"; . "$V2/cases/negatives.sh" "$out" "$addr" ;;
    esac
  done
  ev_results          # persist this host's PASS/RED/GAP rows (evidence/l2/<ts>-<host>/results.txt)
  RESULT_LINES=""
  ev_log "$out/host.log" 300
  ev_binding "$out/binding.json"
  host_stop || true
done
echo
echo "stages run: $STAGES  (s3 needs a gateway release that contains the pull-mode ledger fix —"
echo "see docs/qa/REQ1-ingress-evidence.md §4/§5; with pinned v1.0.11 it is expected RED)"
summary

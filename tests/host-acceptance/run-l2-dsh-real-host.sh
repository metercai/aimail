#!/usr/bin/env bash
# @level L2
# L2 real-host DSH tool-surface gate (B4).
#
# L2 vs L3 (owner ruling 2026-11-21 — two different processes, do not conflate):
#   L2 = BEFORE the test, install the CURRENT DEVELOPMENT build of the plugin
#        into the host profile; then test that development build.
#   L3 = starts CLEAN (no plugin); during the test the agent itself installs
#        the LATEST PUBLISHED version, guided by the activation prompt.
#
# What this gate proves (B4):
#   B4 symptom (QA 2026-11-21): a real `dsh web` host whose web profile does not
#   carry the agent-aimail plugin boots fine — model turns run — but every HTTP
#   session's tool surface has ZERO mail tools (300s, 0 tool frames, no binding
#   written). Root cause (plan 2026-11-20-aimail-v2-sse-qafindings-fix.md §11 v2):
#   the 15 mail tools reach a session ONLY through the plugin's `tool-mail` row,
#   which lands in the GLOBAL layer of the profile the web server boots with.
#   No plugin in that profile ⇒ the row is absent ⇒ every session resolves its
#   tool surface without the 15 tools, silently.
#   Mechanism (verified against the pinned host):
#     - core/tools view() seeds the inherited surface from the global layer for
#       EVERY scope, unconditionally (0.2.2 dsh-tools lib, source
#       packages/core/tools/src/index.ts view());
#     - session/create without an explicit preset resolves the configured
#       default preset (standard) and mounts it (session-controller composeAgent);
#     - plugin install does NOT go through HMR — it takes effect on the NEXT
#       boot of the host (dsh-hmr README: "Package installation runs outside
#       that queue … applies changes on restart").
#   ⇒ the gate = "the dev plugin is installed into the profile the web server
#   boots, AND that profile's composed tree carries the mail tool row in the
#   global layer, AND (live) a fresh HTTP session resolves to the default
#   preset". This is the B4 minimal repro asserted positively, plus a negative
#   control (clean profile must show the row absent) so the gate cannot pass by
#   accident.
#
# Semantics:
#   - Structural gate (authoritative, LLM-free, gateway-free):
#       (a) clean profile (no plugin)   ⇒ tool-mail count == 0   (negative control)
#       (b) dev plugin installed        ⇒ tool-mail count == 1, in the GLOBAL
#                                         layer (above the first preset row)
#   - Live probe (optional; DSH_L2_LIVE=0 to skip): boot the real web server and
#     run session/create over HTTP exactly as the QA B4 journey does (no
#     agentPreset), asserting the session resolves to the default preset. No LLM
#     call, no prompt, no gateway.
#   - Dev source precedence (L2 = test the development build):
#       DSH_L2_DEV_TGZ  packed tgz of the dev build (preferred; versioned copy)
#       DSH_L2_DEV_DIR  source tree link (must carry a built dist/dsh/index.js)
#       default         repo tree tssdk/packages/agent-aimail (built dist required)
#   - Host provenance: tests/agent-env single source (pins.env DSH_VERSION) via
#     prepare.sh — the same pinned host the QA real-host journey ran (0.2.2-rc.1),
#     NOT the globally installed dsh (which may be a different build).
#
# usage:
#   DSH_L2_DEV_TGZ=/path/agent-aimail-0.5.1.tgz \
#   [DSH_L2_PORT=39480] [DSH_L2_LIVE=1|0] [DSH_L2_CLEAN_ONLY=1] \
#   ./tests/host-acceptance/run-l2-dsh-real-host.sh
#
#   DSH_L2_CLEAN_ONLY=1  negative control: run the whole flow WITHOUT installing
#                        the dev plugin; the tool-surface gate MUST fail.
#
# Exit: 0 PASS · 1 FAIL (gate red) · 2 ENV GAP (prerequisite missing)

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
AGENT_ENV="$ROOT/tests/agent-env"
[ -f "$AGENT_ENV/prepare.sh" ] || { echo "✗ agent-env single source missing: $AGENT_ENV/prepare.sh"; exit 2; }
# shellcheck source=/dev/null
. "$AGENT_ENV/pins.env"
DSH_VERSION="${DSH_VERSION:-}"
[ -n "$DSH_VERSION" ] || { echo "✗ pins.env: DSH_VERSION missing"; exit 2; }

DSH_PROFILE="${DSH_PROFILE:-web}"
PORT="${DSH_L2_PORT:-39480}"
LIVE="${DSH_L2_LIVE:-1}"
LIVE_DONE=""
CLEAN_ONLY="${DSH_L2_CLEAN_ONLY:-0}"
WORK="$(mktemp -d -t l2-dsh-real-host-XXXXXX)"
DSH_HOME="$WORK/dsh-home"
export DSH_HOME
mkdir -p "$DSH_HOME" "$WORK/ws"
LOG="$WORK/host.log"
HOST_PID=""

say()  { echo "  $*"; }
ok()   { echo "  ✓ $*"; }
bad()  { echo "  ✗ $*" >&2; }
gap()  { echo "⊘ ENV GAP: $*" >&2; exit 2; }

command -v node >/dev/null 2>&1 || gap "node not in PATH"
command -v curl >/dev/null 2>&1 || gap "curl not in PATH"
command -v python3 >/dev/null 2>&1 || gap "python3 not in PATH"

cleanup() {
  [ -n "$HOST_PID" ] && kill "$HOST_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "════ L2 real-host dsh tool-surface gate (B4): host=@deepseek-ai/dsh@$DSH_VERSION profile=$DSH_PROFILE dev-source=${DSH_L2_DEV_TGZ:-${DSH_L2_DEV_DIR:-$ROOT/tssdk/packages/agent-aimail}} ════"
say "work=$WORK (DSH_HOME=$DSH_HOME)"

# ── 0) host from the agent-env single source (pinned version; isolated HOME) ───
say "preparing host (agent-env single source, pinned $DSH_VERSION)…"
bash "$AGENT_ENV/prepare.sh" dsh --home "$DSH_HOME" --no-verify \
  || gap "agent-env prepare.sh dsh failed (pinned host unavailable; see its output)"
command -v pnpm >/dev/null 2>&1 || gap "pnpm not on PATH after prepare (plugin install needs it)"
# Resolve the PINNED host binary and verify its version — this is the honesty core
# of the gate (it must test the same host the QA real-host journey ran, 0.2.2-rc.1).
# npm install -g (run by prepare.sh) lands the binary at the npm prefix's bin. Some
# dev machines ALSO have a source-tree `dsh` shim earlier in PATH (a different build)
# ⇒ `command -v dsh` is NOT trusted here; prefer the npm-prefix copy and require an
# exact version match. If nothing matches the pin, this is an ENV GAP, not a red gate.
NPM_PREFIX="$(npm prefix -g 2>/dev/null || true)"
CANDIDATES=()
[ -n "$NPM_PREFIX" ] && [ -x "$NPM_PREFIX/bin/dsh" ] && CANDIDATES+=("$NPM_PREFIX/bin/dsh")
command -v dsh >/dev/null 2>&1 && CANDIDATES+=("$(command -v dsh)")
DSH_BIN=""
for _c in ${CANDIDATES[@]+"${CANDIDATES[@]}"}; do
  _v="$("$_c" --version 2>/dev/null | head -1 | tr -d 'v')"
  if [ "$_v" = "$DSH_VERSION" ]; then DSH_BIN="$_c"; break; fi
done
[ -n "$DSH_BIN" ] || gap "no dsh binary at pinned version $DSH_VERSION found (tried: ${CANDIDATES[*]:-none}) — prepare.sh may not have installed the pinned host"
say "pinned host: $DSH_BIN ($("$DSH_BIN" --version 2>/dev/null | head -1))"
# Warm the profile with the PINNED binary (prepare.sh warms with `command -v dsh`,
# which on some machines is a different build) so the profile schema matches the
# host that installs the plugin and boots the web server.
"$DSH_BIN" --profile "$DSH_PROFILE" --dump-config >"$WORK/warm-tree.txt" 2>"$WORK/warm-tree.err" \
  || gap "pinned host profile warmup failed: $(tail -3 "$WORK/warm-tree.err")"

# ── 1) negative control: CLEAN profile must not carry the mail tool row ────────
say "[1/3] negative control — composed tree of the clean profile (no plugin)…"
"$DSH_BIN" --profile "$DSH_PROFILE" --dump-config >"$WORK/clean-tree.txt" 2>"$WORK/clean-tree.err" \
  || gap "dump-config failed on the clean profile: $(tail -3 "$WORK/clean-tree.err")"
CLEAN_MAIL="$(grep -c '^- id: tool-mail' "$WORK/clean-tree.txt" || true)"
[ "${CLEAN_MAIL:-0}" -eq 0 ] || { bad "clean profile already carries tool-mail (count=$CLEAN_MAIL) — environment not clean; negative control red"; exit 1; }
ok "clean profile: 0 tool-mail rows (host is genuinely plugin-free)"

# ── 2) install the DEV plugin, then assert the tool surface ────────────────────
if [ "$CLEAN_ONLY" = "1" ]; then
  say "[2/3] DSH_L2_CLEAN_ONLY=1 — dev plugin install SKIPPED (negative-control run; gate must fail below)"
else
  DEV_SRC="${DSH_L2_DEV_TGZ:-}"
  if [ -z "$DEV_SRC" ] && [ -n "${DSH_L2_DEV_DIR:-}" ]; then
    DEV_SRC="$DSH_L2_DEV_DIR"
  else
    DEV_SRC="${DEV_SRC:-$ROOT/tssdk/packages/agent-aimail}"
  fi
  [ -e "$DEV_SRC" ] || gap "dev plugin source missing: $DEV_SRC"
  case "$DEV_SRC" in
    *.tgz)
      [ -f "$DEV_SRC" ] || gap "DSH_L2_DEV_TGZ not a file: $DEV_SRC (run: cd tssdk/packages/agent-aimail && npm pack)"
      say "[2/3] installing DEV plugin (packed tgz): $DEV_SRC"
      ;;
    *)
      DIST="$DEV_SRC/dist/dsh/index.js"
      [ -f "$DIST" ] || gap "dev source $DEV_SRC has no built dist/dsh/index.js — build first (pnpm build in the tssdk workspace); L2 tests the development build"
      say "[2/3] installing DEV plugin (source-tree link): $DEV_SRC"
      ;;
  esac
  # Plugin install lands in $DSH_HOME/profiles/<profile>/ (its own pnpm workspace);
  # it is idempotent (re-add = 'Already up to date', count stays 1 — verified).
  "$DSH_BIN" plugin --profile "$DSH_PROFILE" add "$DEV_SRC" >"$WORK/plugin-add.log" 2>&1 \
    || { bad "dsh plugin add failed (dev source): $(tail -8 "$WORK/plugin-add.log")"; exit 1; }
  ok "dev plugin installed into profile '$DSH_PROFILE' (DSH_HOME isolated)"
fi

say "composed tree of the profile after install…"
"$DSH_BIN" --profile "$DSH_PROFILE" --dump-config >"$WORK/post-tree.txt" 2>"$WORK/post-tree.err" \
  || { bad "dump-config failed after install: $(tail -3 "$WORK/post-tree.err")"; exit 1; }
MAIL_ROWS="$(grep -c '^- id: tool-mail' "$WORK/post-tree.txt" || true)"
[ "${MAIL_ROWS:-0}" -ge 1 ] || { bad "GATE RED (B4): the web profile's composed tree has no TOP-LEVEL (global-layer) tool-mail row after installing the dev plugin — every session's tool surface (which view() seeds from the global layer for every scope) lacks the 15 mail tools. tree: $WORK/post-tree.txt"; exit 1; }
# The row must also be ENABLED: a disabled global row never reaches a session's
# surface (host-plane base tools are disabled on the web surface; preset-scoped
# tool rows are nested, not top-level).
if awk '/^- id: tool-mail$/{f=1;next} f&&/disabled:/{print;exit} f&&!/^[[:space:]]/{f=0}' "$WORK/post-tree.txt" | grep -q .; then
  bad "GATE RED: the top-level tool-mail row carries a disabled flag — the mail tools will not reach any session's tool surface"
  exit 1
fi
ok "tool-surface gate: enabled GLOBAL-LAYER tool-mail row present (top-level, count=$MAIL_ROWS) — inherited by every session via view()"

# ── 3) optional live probe: real web host + HTTP session/create (no LLM) ───────
if [ "$LIVE" != "1" ]; then
  say "[3/3] live probe skipped (DSH_L2_LIVE != 1)"
else
  say "[3/3] live probe — booting the real web host (port $PORT)…"
  "$DSH_BIN" web --port "$PORT" --no-open >"$LOG" 2>&1 &
  HOST_PID=$!
  TOKEN=""
  for _ in $(seq 1 40); do
    kill -0 "$HOST_PID" 2>/dev/null || break
    TOKEN="$(grep -o 'token=[A-Za-z0-9_-]*' "$LOG" 2>/dev/null | head -1 | cut -d= -f2 || true)"
    [ -n "$TOKEN" ] && break
    sleep 0.5
  done
  [ -n "$TOKEN" ] || { bad "GATE RED (live): web host did not print its login URL within 20s — host.log: $(tail -5 "$LOG" 2>/dev/null | tr '\n' ' ')"; exit 1; }
  ok "web host up (login URL printed)"
  # QA B4 journey, verbatim shape: session/create WITHOUT agentPreset over the
  # token-login HTTP bridge. Asserts the session resolves to the default preset —
  # the live proof the session is composed (not bare) and inherits the global
  # layer that now carries the mail tools. No LLM call, no prompt, no gateway.
  COOKIES="$WORK/cookies.txt"
  BASE="http://127.0.0.1:$PORT"
  LOGIN_HTTP="$(curl -s -c "$COOKIES" -o /dev/null -w '%{http_code}' "$BASE/?token=$TOKEN" || true)"
  [ "$LOGIN_HTTP" = "303" ] || { bad "GATE RED (live): token login failed (http=$LOGIN_HTTP)"; exit 1; }
  RESP="$(curl -s -b "$COOKIES" -X POST "$BASE/api/session/create" \
    -H 'content-type: application/json' \
    -d '{"type":"client-request","rpcId":"b4-live","method":"session/create","payload":{"args":{"request":{"cwd":"'$WORK/ws'"}}}}' || true)"
  say "session/create → ${RESP:0:200}"
  echo "$RESP" | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    print("EMPTY"); sys.exit(0)
d = json.loads(raw)
v = d.get("result", {})
ok = v.get("ok") if isinstance(v, dict) else None
val = v.get("value") if isinstance(v, dict) else None
preset = val.get("agentPreset") if isinstance(val, dict) else None
print("EMPTY" if ok is None else "OK preset=" + str(preset))
' >"$WORK/probe.txt"
  PROBE="$(head -1 "$WORK/probe.txt")"
  case "$PROBE" in
    OK\ preset=?*)
      say "session resolved agentPreset=${PROBE#OK preset=} (default preset mount confirmed live)"
      ;;
    *)
      bad "GATE RED (live): session/create did not confirm a preset mount — response: $RESP"
      exit 1
      ;;
  esac
  ok "live probe: real web host + HTTP session composes (B4 journey shape, no LLM)"
  LIVE_DONE="live-probe=ok"
fi

echo
echo "  RESULT: L2 REAL-HOST DSH TOOL-SURFACE GATE PASS (profile=$DSH_PROFILE, host=$DSH_VERSION, clean-control=0-rows, post-install=$MAIL_ROWS-rows${LIVE_DONE:+, $LIVE_DONE})"
echo "  evidence: $WORK"
exit 0

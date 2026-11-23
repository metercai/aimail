#!/usr/bin/env bash
# @level L3
# @scenario 1,2,3
# ═══════════════════════════════════════════════════════════════════════
# run-l3.sh — L3 integration closed-loop regression (driven by the matrix in l3.yml).
#
# Regression content **completely identical**, does not vary with OS/agent (D3 ruling):
#   ① Install target agent system + configure LLM (deepseek-flash) + verify agent itself can run
#   ② Get published gateway (basic version, GitHub Release binary) and start it, capture system/admin key
#   ③ Get published CLI (bootstrap.sh online = real user entry point)
#   ④ SDK installation: host plugin mechanism only (dsh plugin add); the SDK CLI install path is retired
#   ⑤ welcome closed loop: welcome → agent replies with three tags → manager (security officer) approves persona
#   ⑥ Identity assertion: SDK isomorphic send_mail sends one query → whoami reads back the identity card (agent_persona)
#      + email signature (agent_signature) is effective (whoami.rs:44-52, welcome approve UPSERT read-back point)
#
# Activation flow: the dsh cell runs the full V2 activation-code journey (external
# applicant verifies email → applies an address code → the host agent binds over
# its web API) — dsh-v2.sh. There is no second flow: the L3 matrix is dsh-only.
# Test targets: all from published channels (GitHub Release / npm), never from the working tree.
#
# Environment (injected by workflow):
#   L3_PLATFORM  linux-amd64|linux-arm64|macos-arm64|windows-amd64
#   L3_AGENT     dsh — the ONLY cell on the tssdk path.
#                Owner ruling 2026-11-23: L3 = published SDK, installed by the agent
#                itself, dsh only. Retired cells and why:
#                  dsh-aimail  CLI entry (`aimail install`) is no longer shipped
#                  hermes      pysdk not migrated to SSE-V2 ⇒ SKIP_LEGACY, never green
#                  pi/openclaw L2 scope (local platform), not L3
#                Any other L3_AGENT is refused at the top of this script rather than
#                silently reused.
#   AIMAIL_REPO  aimail repo checkout (environment preparation single source + llm-config.py both in tests/agent-env/)
#   LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
#
# Process contract (README Quick Start scenario 4 for the host plugin mechanism):
#   ③ env four-piece set (AIMAIL_GW_URL/ADMIN_KEY/DOMAIN/MANAGER_ADDRESS) — dsh needs no published CLI
#   ④ dsh plugin --profile web add agent-aimail (host plugin mechanism; inside dsh-v2.sh)
#
# Exit codes: 0=green (closed loop complete) · 1=red (assertion failed in some stage, reason printed) · 2=environment missing prerequisites
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

# Portable timeout (macOS runner has no GNU timeout): use if available, otherwise background+watchdog, timeout rc=124 semantics same as GNU.
run_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
  else
    "$@" &
    local pid=$!
    for _ in $(seq 1 "$secs"); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; return 124; fi
    wait "$pid"
  fi
}

say() { printf '  %s\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m⚠ %s\n' "$*"; }
die() { printf '\033[31m✗ %s\n' "$*" ; exit 1; }
gap() { printf '\033[35m⊘ ENV GAP: %s\n' "$*"; exit 2; }

# Failure forensics (owner discipline: red must be able to "determine cause by read-only logs", no guessing, and no repeated reruns).
# welcome when not closed-loop, print **three-hop evidence** to stdout —— CI uses tee to capture stdout, so it also goes into the job log
# and l3-logs artifacts. Three hops = ① gateway delivery ② inbound ledger (agentmail.log) ③ host original words (agent host log);
# Also attach "which interpreter the SDK lands in" and hermes's platform_toolsets (historically known gap: missing it ⇒ agent
 # it can receive but not send out). Previously it only printed "no reply" ⇒ we could only guess; in practice the cause was undeterminable.
archive_agent_state() {
  # Bounded snapshot of what the agent actually persisted. Runs on failure AND
  # on success: a green cell needs the same evidence to prove *why* it was green
  # (e.g. did it look the mail up at all?), otherwise red/green differences get
  # attributed to the platform or the install path instead of to behaviour.
  [ -n "${L3_LOG_ARCHIVE:-}" ] || return 0
  [ -n "${_L3_ARCHIVED:-}" ] && return 0
  _L3_ARCHIVED=1
  local arch="$L3_LOG_ARCHIVE/state-archive" d
  mkdir -p "$arch" 2>/dev/null
  for d in "${DSH_HOME:-$HOME/.dsh}/storages" "${DSH_HOME:-$HOME/.dsh}/logs" \
           "${DSH_HOME:-$HOME/.dsh}/sessions" "$HOME/.aimail/systems"; do
    [ -d "$d" ] || continue
    ( cd "$(dirname "$d")" && tar czf "$arch/$(basename "$d").tgz" --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' "$(basename "$d")" 2>/dev/null )
  done
  find "$HOME/.aimail/systems" -maxdepth 4 -name '*.log' -size -2M 2>/dev/null | while read -r f; do
    mkdir -p "$arch/aimail-logs/$(dirname "${f#$HOME/.aimail/systems/}")" 2>/dev/null
    cp "$f" "$arch/aimail-logs/${f#$HOME/.aimail/systems/}" 2>/dev/null
  done
  echo "  [archive] state archived to $arch"
}

dump_failure_evidence() {
  local h g f found_am=0 skill pat
  # Single source of truth for contract constants: SKILL names in search terms **are read from the manifest** — hardcoded literals will collide with contract literal ratchets
  # (run-cli-gates step 1 shared boundary scan whole repo): source of truth = contract/aimail-contract.json.
  skill="$(python3 - "${AIMAIL_REPO:-}/contract/aimail-contract.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["agent_skill_name"])
PY
)"
  pat="Response for webhook|toolset|not registered|unknown name|send_mail|ImportError|ModuleNotFound"
  [ -n "$skill" ] && pat="$pat|$skill"
  echo "  ────────── Failure forensics dump ──────────"
  archive_agent_state
  for h in "$WORK"/*-host.log; do
    [ -f "$h" ] || continue
    echo "  [③Host original words] $h (tail -40)"
    tail -80 "$h" | sed 's/^/    /'
    echo "  [③Host original words: webhook/toolset/email tool key lines]"
    grep -aiE "$pat" "$h" | tail -25 | sed 's/^/    /'
  done
  for g in "$GW_HOME/boot.log" "$GW_HOME"/*.log; do
    [ -f "$g" ] || continue
    echo "  [①Gateway delivery] $g (webhook/deliver/pending related lines, tail -25)"
    grep -aiE "webhook|deliver|inbound|non_2xx|overlimit|pending" "$g" | tail -25 | sed 's/^/    /'
  done
  # Full text tail: if agent side actually sent emails, outbound records can only be seen here (filter words will miss other phrasings)
  [ -f "$GW_HOME/boot.log" ] && { echo "  [①Gateway delivery: boot.log full text tail -40]"; tail -40 "$GW_HOME/boot.log" | sed 's/^/    /'; }
  for f in "$HOME"/.aimail/systems/*/*/agentmail.log; do
    [ -f "$f" ] || continue
    found_am=1
    echo "  [②Inbound ledger] $f (tail -30)"
    tail -30 "$f" | sed 's/^/    /'
  done
  # Explicitly report "no agentmail.log" (previously silently skipped ⇒ cannot distinguish "file does not exist" from "sampling written incorrectly")
  [ "$found_am" = 1 ] || echo "  [②Inbound ledger] agentmail.log does not exist (glob: $HOME/.aimail/systems/*/*/agentmail.log; system dirs: $(ls -d "$HOME"/.aimail/systems/*/ 2>/dev/null | tr '\n' ' '))"
  # The key is not "whether system python3 has the SDK", but **whether the interpreter used by the agent runtime** has it ——
  # official hermes host runs on its bundled tools/python-*/bin/python3 (not in PATH).
  echo "  [SDK landing point] Per-interpreter import probe:"
  for p in "$(command -v python3)" "$HOME"/.hermes/tools/*/bin/python3 \
           "$HOME"/.hermes/hermes-agent/.venv/bin/python3 "$HOME"/.hermes/hermes-agent/venv/bin/python3 \
           "$HOME"/.hermes/*/venv/bin/python3 "$HOME"/.hermes/*/.venv/bin/python3; do
    [ -x "$p" ] || continue
    printf '    %-62s %s\n' "$p" \
      "$("$p" -c "import importlib.util as u; s=u.find_spec('aimail'); print(s.origin if s else 'NONE')" 2>&1 | tail -1)"
  done
  echo "  [agentmail.log location] $(find "$HOME" -name 'agentmail.log' 2>/dev/null | head -5 | tr '\n' ' ') (empty = SDK never wrote this log)"
  if [ -n "${HERMES_HOME:-}" ]; then
    # Print the entire block (previously -A3 truncated ⇒ cannot see if the webhook: key is present, and its absence is exactly
    # ensure_config.py:4-11 Remember the root cause of "can receive but cannot send out": webhook session fallback lacks the default toolset with send_mail)
    echo "  [hermes config] $HERMES_HOME/config.yaml: platform_toolsets / platforms.webhook whole block"
    sed -n '/^platform_toolsets:/,/^[a-z_]*:/p' "$HERMES_HOME/config.yaml" 2>/dev/null | head -30 | sed 's/^/    /'
    sed -n '/^platforms:/,/^[a-z_]*:/p' "$HERMES_HOME/config.yaml" 2>/dev/null | head -30 | sed 's/^/    /'
    grep -aqE "^\s*webhook:" <(sed -n '/^platform_toolsets:/,/^[a-z_]*:/p' "$HERMES_HOME/config.yaml") \
      && echo "    ✓ platform_toolsets.webhook exists" \
      || echo "    ✗ platform_toolsets.webhook missing —— known root cause in ensure_config.py:4-11 (agent 'received but cannot reply')"
    # Reply protocol injection channel: skills field of webhook route (webhook.py:853 injects by route skills
    # SKILL content into prompt; missing contract skill name ⇒ agent has send_mail tool but no reply protocol).
    # Route table lands in {HERMES_HOME}/webhook_subscriptions.json (written by _ensure_webhook_route).
    # Route name/skill name read from contract single source of truth (literal ratchet: no new hardcoded locations allowed).
    CONTRACT_JSON="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/contract/aimail-contract.json"
    SUBS="$HERMES_HOME/webhook_subscriptions.json"
    # Both places possible: gateway runtime reads get_hermes_home() (= $HERMES_HOME), while
    # _ensure_webhook_route writes to the profile directory when profile_dir is non-empty — check both places.
    for SUBS in "$HERMES_HOME/webhook_subscriptions.json" \
                "$HERMES_HOME/profiles/agent/webhook_subscriptions.json"; do
      if [ -f "$SUBS" ]; then
        echo "  [hermes webhook route] $SUBS (skills field = reply protocol injection)"
        python3 - "$SUBS" "$CONTRACT_JSON" <<'PY' | sed 's/^/    /'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
try:
    c = json.load(open(sys.argv[2], encoding="utf-8"))
    route, skill = c["hermes_route_name"], c["agent_skill_name"]
except Exception:
    route = skill = None
for name, r in d.items():
    print(f"  route {name}: skills={r.get('skills')} preprocess={r.get('preprocess')} deliver={r.get('deliver')}")
    if route and name == route:
        sk = r.get("skills") or []
        if skill in sk:
            print(f"  ✓ route {route} skills contains {skill} =", sk)
        else:
            print(f"  ✗ route {route} skills={sk} missing {skill} —— webhook session cannot get reply protocol")
PY
      fi
    done
    [ -f "$HERMES_HOME/webhook_subscriptions.json" ] || [ -f "$HERMES_HOME/profiles/agent/webhook_subscriptions.json" ] \
      || echo "  ✗ webhook_subscriptions.json does not exist in either location —— _ensure_webhook_route was not executed (install path broken)"
  fi
  # dsh grid forensics blind spot: host agent conversations do not go through stdout (dsh-host.log only has startup frames, historically proven 4 lines),
  # session persistence is in $DSH_HOME/storages/ (workspace.json: global.sessionIds + tables;
  # single session is in storages/session_projcache/sessions/<id>.json —— three layers, L3 proven) ——
  # the real data source for dsh grid "③ host original text" (live experiment confirms dsh disk layout).
  if [ -n "${DSH_HOME:-}" ] && [ -d "$DSH_HOME/storages" ]; then
    echo "  [③ dsh session persistence] $DSH_HOME/storages/"
    find "$DSH_HOME/storages" -type f 2>/dev/null | head -10 | sed 's/^/    /'
    # Recursively get all session json (workspace + single session), not just the top two layers —— single session files are in
    # session_projcache/sessions/ third layer, which are the conversation records of the agent's actual turns.
    while IFS= read -r sf; do
      [ -f "$sf" ] || continue
      sz=$(wc -c < "$sf")
      echo "  [③ dsh session] ${sf#"$DSH_HOME"/storages/} ($sz bytes, full text ≤12KB / truncated if exceeded)"
      # Front part of session file = agent turn conversations (messages), tail part = metadata —— must read from the front,
      # cannot use tail (historically proven 8KB session, tail -40 only captured metadata, all turn content lost).
      if [ "$sz" -le 12000 ]; then
        cat "$sf" | sed 's/^/    /'
      else
        head -c 12000 "$sf" | sed 's/^/    /'
        echo "    ... (truncated at 12000/$sz bytes)"
      fi
    done < <(find "$DSH_HOME/storages" -type f -name '*.json' 2>/dev/null)
  elif [ -n "${DSH_HOME:-}" ]; then
    echo "  [③ dsh session persistence] does not exist $DSH_HOME/storages (dsh host not persisted —— host may never have booted to session layer)"
  fi
  # dsh grid LLM death cause localization: session proven llmMs=0 + response="" = LLM call failed before initiation
  # (dsh-llm-deepseek-api-key: throw an error at the adapter layer when the key is missing, the host swallows the error).
  # Two criteria: ① whether DEEPSEEK_API_KEY is really in the host process environment (env propagation chain proven, value only checks existence);
  # ② Actually hit the /anthropic endpoint once with the same key (401=key expired, 404=endpoint drift, 200=key valid).
  if [ -n "${HOST_PID:-}" ] && [ -r "/proc/$HOST_PID/environ" ]; then
    python3 - "/proc/$HOST_PID/environ" <<'PY' | sed 's/^/    /'
import sys
pref = b"LLM_" + b"API_KEY" + b"="   # Concatenation: avoid literal being masked as secret
data = open(sys.argv[1], "rb").read().split(b"\0")
v = next((d for d in data if d.startswith(pref) and len(d) > len(pref)), None)
if v:
    print("  [③ dsh LLM] ✓ host process environment contains LLM_API_KEY (non-empty, value not printed)")
else:
    print("  [③ dsh LLM] ✗ host process environment has no LLM_API_KEY or it is empty —— MISSING_CREDENTIAL, root cause of turn dying in 0ms")
PY
  else
    echo "  [③ dsh LLM] ✗ host process ${HOST_PID:-?} is dead or environ is not readable —— cannot judge env propagation"
  fi
  python3 - "$LLM_BASE_URL" "$LLM_MODEL" <<'PY' | sed 's/^/    /'
import os, sys, json, time, urllib.request
key = os.environ.get("LLM_API_KEY", "")
if not key:
    print("  ✗ forensics process itself has no LLM_API_KEY, skipping probe"); raise SystemExit
# Probe hits the endpoint actually called by the agent (OpenAI chat/completions, = llm-pi-ai / hermes custom same shape),
# no longer hardcoding deepseek/anthropic —— hardcoded probe disconnected from agent real path = false green light blind spot.
base = sys.argv[1].rstrip("/")
url = base + "/chat/completions" if base.endswith("/v1") else base + "/v1/chat/completions"
body = json.dumps({"model": sys.argv[2], "max_tokens": 8,
                   "messages": [{"role": "user", "content": "ping"}]}).encode()
req = urllib.request.Request(url, data=body, method="POST",
    headers={"Authorization": "Bearer " + key, "content-type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"  ✓ LLM probe: HTTP {r.status} in {time.time()-t0:.1f}s (key valid, endpoint reachable)")
except urllib.error.HTTPError as e:
    frag = (e.read() or b"")[:200].decode(errors="replace")
    print(f"  ✗ LLM probe: HTTP {e.code} in {time.time()-t0:.1f}s — {frag}")
except Exception as e:
    print(f"  ✗ LLM probe: {type(e).__name__} in {time.time()-t0:.1f}s — {str(e)[:120]}")
PY
  # Product's own criteria (historically in L2 it reported '✗ toolset: … platform_toolsets MISSING').
  # No CLI self-check here: the dsh cell ships no aimail CLI (install path retired); its
  # product-side criteria are asserted in dsh-v2.sh against the gateway + host state.
  echo "  ──────────────────────────────────"
}

PLATFORM="${L3_PLATFORM:?L3_PLATFORM not set}"
AGENT="${L3_AGENT:?L3_AGENT not set}"
AIMAIL_REPO="${AIMAIL_REPO:?AIMAIL_REPO not set}"
# ── matrix scope (owner ruling 2026-11-23): the tssdk L3 path is dsh-only ──────────
# Refuse anything else up front. Rationale per retired cell:
#   dsh-aimail  the CLI entry no longer exists (`aimail install` retired)
#   hermes      pysdk has not migrated to SSE-V2 ⇒ SKIP_LEGACY, never counted green
#   pi/openclaw L2 scope (local platform), not an L3 cell
case "$AGENT" in
  dsh) : ;;
  *)   die "L3 matrix is dsh-only on the tssdk path; agent '$AGENT' is retired or out of scope (see tests/l3/GATE.md)" ;;
esac
# Host-version pin check lives AFTER ① (see below): it is only meaningful once ① has
# installed the pinned host. Placing it here read $AGENT before assignment and killed
# every cell with 'AGENT: unbound variable' under `set -u`.

# Environment preparation single source = aimail repo tests/agent-env/ (official install + basic environment + disable package manager age gate + LLM config;
# contract see tests/agent-env/README.md). LLM writer is also only that one copy.
AGENT_ENV="$AIMAIL_REPO/tests/agent-env"
LLMCFG="$AGENT_ENV/lib/llm-config.py"
[ -f "$AGENT_ENV/prepare.sh" ] || die "agent-env/prepare.sh missing at $AGENT_ENV (self-contained in the aimail repo)"
[ -f "$LLMCFG" ] || die "llm-config.py missing at $LLMCFG (self-contained in the aimail repo)"
LLM_BASE_URL="${LLM_BASE_URL:-https://api.xiaomimimo.com/v1}"
LLM_MODEL="${LLM_MODEL:-mimo-v2.6-flash}"
[ -n "${LLM_API_KEY:-}" ] || { echo "✗ LLM_API_KEY secret not set"; exit 2; }

WORK="$(mktemp -d /tmp/l3-XXXXXX)"
GW_HOME="$WORK/gw"
mkdir -p "$GW_HOME/data"
LOG="$WORK/l3.log"
# Log archive: on failure workflow uploads $HOME/l3-logs (full OS deterministic path); EXIT trap fallback lands final log
LOG_ARCHIVE="${L3_LOG_ARCHIVE:-$HOME/l3-logs}"
mkdir -p "$LOG_ARCHIVE" 2>/dev/null || true
trap 'archive_agent_state; cp -f "$WORK"/* "$LOG_ARCHIVE/" 2>/dev/null; cp -f "$LOG" "$LOG_ARCHIVE/" 2>/dev/null; true' EXIT

# Asset name platform → release asset suffix
asset_suffix() {
  case "$PLATFORM" in
    linux-amd64)    echo "linux-amd64" ;;
    linux-arm64)    echo "linux-arm64" ;;
    macos-arm64)    echo "macos-arm64" ;;
    windows-amd64)  echo "windows-amd64" ;;
    *) die "unknown platform $PLATFORM" ;;
  esac
}
ASSET="$(asset_suffix)"
EXT=""; [ "$PLATFORM" = "windows-amd64" ] && EXT=".exe"
GW_BIN="$GW_HOME/aimail-gateway$EXT"

echo "════ L3: $PLATFORM / $AGENT  (LLM=$LLM_MODEL @ $LLM_BASE_URL)"

# ── ① Install agent + configure LLM + verify runnable ─────────────────────────────
echo "── [1/6] agent env: $AGENT"
case "$AGENT" in
  dsh)
    # Official npm install dsh + pnpm (part of dsh's own base environment, must be pre-installed: cold container first call will fetch pnpm from network,
    # hangs on probe if network is bad; CI runner has no pnpm ⇒ plugin install will fail) + profile warm-up (profiles/web/
    # cordis.patch.yml is the landing point for LLM writes and plugin installs) + disable age gate + LLM config = single-source prepare.sh.
    DSH_HOME="${HOME}/.dsh"
    AGENT_HOME="$DSH_HOME"
    bash "$AGENT_ENV/prepare.sh" dsh --home "$DSH_HOME" \
      --llm-url "$LLM_BASE_URL" --llm-model "$LLM_MODEL" --llm-key "$LLM_API_KEY" \
      || die "dsh env prepare failed (single source: tests/agent-env)"
    VERIFY_HOME="$DSH_HOME"
    VERIFY_EXTRA=()
    ok "dsh env ready (official npm + pnpm + profile warmed + LLM configured; single source)"
    ;;
  *)
    # hermes / pi / anything else: unreachable — the matrix-scope guard above already
    # refused them. Keep the branch so a future edit cannot silently fall through.
    die "no environment-prepare branch for agent '$AGENT' (L3 matrix is dsh-only)"
    ;;
esac

# ── host version must match the pin (a stale host would fake green) ───────────────
# Runs AFTER ① on purpose: prepare.sh installs the pinned host, so this is the first
# moment the version can be read. (Historically placed before ① ⇒ it read $AGENT
# before assignment under `set -u` ⇒ every cell died at ① with rc=127.)
PINS_HOST="$AIMAIL_REPO/tests/agent-env/pins.env"
if [ -f "$PINS_HOST" ]; then
  WANT="$(grep -E '^DSH_VERSION=' "$PINS_HOST" | head -1 | cut -d= -f2-)"
  GOT="$(dsh --version 2>/dev/null | head -1 | tr -d '[:space:]')"
  if [ -n "$WANT" ] && [ "$GOT" != "$WANT" ]; then
    gap "host version mismatch for dsh: got '$GOT', pin '$WANT' — refusing to judge"
  fi
  ok "host version matches the pin ($GOT)"
fi

# The agent itself is runnable (D3 prerequisite): probe that the LLM endpoint is reachable + the agent version can be printed
say "verifying agent runs + LLM endpoint reachable…"
python3 "$LLMCFG" verify \
  --platform "$AGENT" --home "$VERIFY_HOME" ${VERIFY_EXTRA[@]+"${VERIFY_EXTRA[@]}"} \
  --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" \
  || warn "agent LLM verify returned non-zero (continuing; welcome will surface the real failure)"
ok "agent env ready: $AGENT"

# ── ② Fetch published base gateway and start it (capture key)───────────────────────────
echo "── [2/6] published gateway (basic edition, $PLATFORM)"
# Primary path for asset discovery = github.com releases/latest redirect (web endpoint, CDN distribution,
# not subject to api.github.com unauthenticated IP rate limit 60/h constraint). api.github.com is only a fallback:
# Under matrix concurrency, unauthenticated calls will hit rate limit (403, observed 2026-10-09 macos two cells),
# and rate limit responses must be distinguished from "asset missing" —— false positive ENV GAP would mask the real cause of death.
GW_TAG=""
GW_TAG_URL="$(curl -sIL -o /dev/null -w '%{url_effective}' --connect-timeout 15 --max-time 30 \
  "https://github.com/metercai/aimail-gateway/releases/latest" 2>/dev/null)"
case "$GW_TAG_URL" in *"/releases/tag/"*) GW_TAG="${GW_TAG_URL##*/releases/tag/}";; esac
GW_DOWNLOADED=""
if [ -n "$GW_TAG" ]; then
  GW_URL_ASSET="https://github.com/metercai/aimail-gateway/releases/download/${GW_TAG}/aimail-gateway-$ASSET$EXT"
  say "fetching gateway: $GW_URL_ASSET (latest release $GW_TAG)"
  curl -fsSL --retry 2 --retry-delay 2 --connect-timeout 15 --max-time 300 "$GW_URL_ASSET" -o "$GW_BIN" \
    && GW_DOWNLOADED=1 \
    || say "  latest release has no asset for this platform/transient failure → fallback to releases API scan…"
fi
if [ -z "$GW_DOWNLOADED" ]; then
  GW_URL_ASSET=""
  GW_API_RESP=""
  for attempt in 1 2 3 4 5; do
    GW_API_RESP="$(curl -s --connect-timeout 15 --max-time 60 \
      "https://api.github.com/repos/metercai/aimail-gateway/releases?per_page=30")" || GW_API_RESP=""
    if [ -n "$GW_API_RESP" ] && grep -q '"message"' <<<"$GW_API_RESP"; then
      say "  releases API rate limit/error: $(head -c 120 <<<"$GW_API_RESP") — retry ${attempt}/5…"
      sleep $((attempt * 20))
      continue
    fi
    GW_URL_ASSET="$(grep -o '"browser_download_url": *"[^"]*"' <<<"$GW_API_RESP" \
      | sed 's/"browser_download_url": *"//; s/"$//' | grep -m1 "aimail-gateway-$ASSET$EXT\$")"
    break   # API normal (regardless of whether asset exists): retry is meaningless
  done
  [ -n "$GW_URL_ASSET" ] || gap "no aimail-gateway-$ASSET release asset (latest=$GW_TAG, API: $(head -c 100 <<<"$GW_API_RESP") — run the gateway release workflow first)"
  say "fetching gateway: $GW_URL_ASSET"
  curl -fsSL --retry 2 --retry-delay 2 --connect-timeout 15 --max-time 300 "$GW_URL_ASSET" -o "$GW_BIN" \
    || die "gateway download failed"
fi
chmod +x "$GW_BIN"
# ── pinned published gateway (owner rule 5: L2/L3 both test the PUBLISHED gateway) ──
# pins.env is the single source; when it names a version we use exactly that asset and
# verify its sha256 — "releases/latest" is only a fallback for an unpinned checkout.
PINS_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/agent-env/pins.env"
PIN_TAG=""; PIN_SHA=""
if [ -f "$PINS_FILE" ]; then
  PIN_TAG="$(grep -E '^GATEWAY_VERSION=' "$PINS_FILE" | head -1 | cut -d= -f2-)"
  PIN_SHA="$(grep -E "^GATEWAY_SHA256_${PLATFORM}=" "$PINS_FILE" | head -1 | cut -d= -f2-)"
fi
if [ -n "$PIN_TAG" ]; then
  if [ "${GW_TAG:-}" != "$PIN_TAG" ]; then
    say "pinned gateway differs from latest (${GW_TAG:-none} → $PIN_TAG): re-fetching pinned asset"
    curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 15 --max-time 300 \
      "https://github.com/metercai/aimail-gateway/releases/download/${PIN_TAG}/aimail-gateway-$ASSET$EXT" \
      -o "$GW_BIN" || die "pinned gateway download failed ($PIN_TAG/$PLATFORM)"
    chmod +x "$GW_BIN"
  fi
  if [ -n "$PIN_SHA" ]; then
    GOT_SHA="$(sha256sum "$GW_BIN" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$GW_BIN" | cut -d' ' -f1)"
    [ "$GOT_SHA" = "$PIN_SHA" ] || die "gateway sha256 mismatch for $PIN_TAG/$PLATFORM ($GOT_SHA != $PIN_SHA)"
    say "gateway pinned ok: $PIN_TAG sha256=${PIN_SHA:0:12}…"
  fi
  echo "{\"gateway_tag\":\"$PIN_TAG\",\"gateway_sha256\":\"${PIN_SHA:-unknown}\",\"platform\":\"$PLATFORM\"}" >"$LOG_ARCHIVE/gateway.json" 2>/dev/null || true
else
  warn "pins.env has no GATEWAY_VERSION — L3 is tracking releases/latest (not reproducible)"
fi


# Minimal config (local loopback, port auto-selected to avoid host processes —— see advanced agent-turn criteria)
GW_HTTP_PORT=18123; GW_SMTP_PORT=12526
# Windows(git-bash): config is the **file content** read by the gateway(native PE), MSYS only translates argv
# does not translate strings inside files —— /d/a/... in Windows semantics = D:\d\a\... (relative to current drive root),
# Misalignment with the D:\a\... on the bash side ⇒ the gateway wrote to the path it understood, but bash cannot find the key.
 # cygpath -w converts to a Windows native path (C:\\a\\...) for writing into config: the backslashes from -w in TOML
# basic string are escape sequences (\U/\R = unicode 8-hex, round 3 empirical Invalid hex value),
# so [storage] path uses TOML literal string (single quotes, backslashes not escaped); Windows API accepts
# forward/backward double slashes. unix branch: no cygpath, original POSIX path goes into basic string, behavior unchanged.
case "$(uname -s)" in
  MINGW*) GW_DATA_PATH="$(cygpath -w "${GW_HOME}/data")"; GW_PATH_TOML="'${GW_DATA_PATH}'" ;;
  *)      GW_PATH_TOML="'${GW_HOME}/data'" ;;
esac
cat > "$GW_HOME/config.toml" <<EOF
[http]
bind = "127.0.0.1:${GW_HTTP_PORT}"
hostname = "l3.local"
[smtp]
bind = "127.0.0.1:${GW_SMTP_PORT}"
hostname = "l3.local"
[storage]
path = ${GW_PATH_TOML}
[logging]
level = "info"
[admin]
email = "admin@l3.local"
# L3 在单机 CI 上跑：agent inbound 是 127.0.0.1，属私有部署。
# 网关默认拒绝私网/回环 webhook 目标（SSRF 防护），这里必须显式放开，
# 否则 agent 注册会以 webhook_target_not_allowed 失败 → welcome 400。
[webhook]
allow_private_targets = true
EOF
say "starting gateway…"
"$GW_BIN" --config "$GW_HOME/config.toml" > "$GW_HOME/boot.log" 2>&1 &
GW_PID=$!
# Wait for system key to be persisted (setup_admin_key prints once + persists)
for i in $(seq 1 30); do
  if ls "$GW_HOME/data"/*.system.key >/dev/null 2>&1; then break; fi
  kill -0 "$GW_PID" 2>/dev/null || die "gateway exited early — boot log: $(tail -5 "$GW_HOME/boot.log")"
  sleep 1
done
SYS_KEY_FILE="$(ls "$GW_HOME/data"/*.system.key 2>/dev/null | head -1)"
[ -n "$SYS_KEY_FILE" ] || die "gateway did not provision system key — boot.log: $(tail -10 "$GW_HOME/boot.log" 2>/dev/null | tr '\n' ' ') | data dir: $(ls -la "$GW_HOME/data" 2>&1 | tr '\n' ' ') | config: $(cat "$GW_HOME/config.toml" | tr '\n' ' ')"
SYSTEM_ID="$(cat "$GW_HOME/data/system.id" 2>/dev/null | tr -d '[:space:]')"
SYS_KEY="$(tr -d '[:space:]' < "$SYS_KEY_FILE")"
ADMIN_KEY_FILE="$GW_HOME/data/aimail.db.admin_key"
[ -f "$ADMIN_KEY_FILE" ] || ADMIN_KEY_FILE="$(ls "$GW_HOME/data"/*.admin_key 2>/dev/null | head -1)"
ADMIN_KEY="$(tr -d '[:space:]' < "$ADMIN_KEY_FILE" 2>/dev/null)"
GW_URL="http://127.0.0.1:${GW_HTTP_PORT}"
ok "gateway up: $GW_URL (system_id=$SYSTEM_ID)"
say "  system key : ${SYS_KEY:0:8}… (agent-side, install -k)"
say "  admin  key : ${ADMIN_KEY:0:8}… (platform, welcome/approve)"
[ -n "$SYS_KEY" ] && [ -n "$ADMIN_KEY" ] || die "missing gateway key(s)"

# ── ③ Fetch published CLI (README scenario 4 as-is: env four-piece set + online bootstrap)────────
# Strictly reproduce the user flow published in README (lines 130-135): first export four envs, then online curl|bash.
# bootstrap.sh persists env to ~/.aimail/.env (machine-level, config.rs:284 subsequent CLI commands read it),
# ④ AGENT_HOME install and ⑤ AGENT_HOME welcome rely on .env + pointer files, not on residual exports from this shell.
echo "── [3/6] published CLI (README scenario 4: env + online bootstrap)"
export AIMAIL_GW_URL="$GW_URL"
export AIMAIL_ADMIN_KEY="$SYS_KEY"
export AIMAIL_DOMAIN="l3.local"
# Security officer (manager) = external domain address: gateway anti-loop (receiver.rs:232-249) rejects **sender domain registered**
# inbound emails (MAIL FROM domain = l3.local ⇒ 550). Approval emails go via SMTP inbound (base version
# auth_from=manager), security officer must use unregistered domain to pass anti-loop. l3-mgr.example = RFC 2606
# reserved domain, no real MX, no real outbound email. Approval validation (webhook.rs:716) matches by sender == install
# registered manager_address, changing domain does not affect approval flow.
export AIMAIL_MANAGER_ADDRESS="manager@l3-mgr.example"
# arm64 slow runner: observed cold start turn exceeds default 120s reply window (llmMs=65.9s still in flight when timeout,
# 2026-10-15 run 38683702165 linux-arm64/dsh-aimail empirical: path alive, not dead) ⇒ relax to 300s.
# macos-arm64 same arm64 slow runner: 2026-10-16 run 38872198518 macos-arm64/dsh-aimail
# empirical same type flake——webhook 200 delivery success + dsh session forensics prove turn finished (output 1808
# tokens/step 6/llmRetry empty), turn falls outside (120s,140s] window ⇒ not product regression.
# Window itself is in published CLI (welcome poll_reply, --timeout flag configurable,
# default 120 unchanged) ⇒ this line requires corresponding CLI release to take effect in L3.
# 2026-11-02 (L3 runs 41553978770 / 41557017040 / 41465247989): the 120s window is
# not an arm64 property. On loaded runners the same cell passes at 300s on
# linux-amd64 (41557017040 success) and windows-amd64 (41465247989 success) while
# failing at 120s. Window is a wait budget, not an assertion: unify at 300s for
# every platform so a red means "the loop did not close", not "the runner was busy".
# An explicit WELCOME_TIMEOUT (e.g. workflow dispatch welcome_timeout) still wins.
case "${L3_PLATFORM:-}" in
  *) WELCOME_TIMEOUT="${WELCOME_TIMEOUT:-300}" ;;
esac
# The dsh cell is V2-only and needs no published CLI: the activation prompt is applied
# inside the host, the plugin is installed by the host's plugin mechanism, and the
# welcome/approval steps run against the gateway's HTTP API (dsh-v2.sh). The former
# `curl bootstrap.sh` CLI stage belonged to the retired CLI cells and is gone with them.

# ── dsh V2 journey (self-contained: dsh-v2.sh [0]-[7]) ────────────────────────
# V2 (owner ruling 2026-11-22): the dsh cell runs the full user journey —
# external applicant verifies email + applies the address code (D1: the code and
# the prompt come from the mailbox, never the DB), the real web host's agent
# binds through the LLM turn (D2), the user installs the plugin via the host's
# normal mechanism + restart, then the welcome/approval loop closes. dsh-v2.sh
# takes over the gateway from ② (GW_PID_L3): [0] restarts it WITH the [relay]
# wiring (external-domain mail → sink) and kills it on exit.
if [ "$AGENT" = "dsh" ]; then
  echo "── [4/6] dsh V2 journey (activation code → host agent bind → welcome loop)"
  # dsh-v2.sh runs as a CHILD process (bash …) ⇒ it only inherits EXPORTED
  # variables. run-l3.sh assigns the journey's inputs as shell locals; export
  # the whole dependency set here (single point — keeps the child's contract
  # explicit instead of relying on scattered exports).
  export GW_BIN GW_HOME GW_URL GW_HTTP_PORT GW_SMTP_PORT
  export SYSTEM_ID ADMIN_KEY
  export LLM_API_KEY LLM_BASE_URL LLM_MODEL
  export WORK AIMAIL_REPO WELCOME_TIMEOUT
  export GW_PID_L3="$GW_PID"
  export DSH_HOME="${HOME}/.dsh"
  bash "$(dirname "${BASH_SOURCE[0]}")/dsh-v2.sh"
  V2_RC=$?
  if [ "$V2_RC" -ne 0 ]; then
    # rc=2 is an ENV GAP (dsh-v2.sh gap() = exit 2, same contract as run-l3.sh
    # gap()) — report it distinctly but still RED: a gap is an incomplete
    # environment, not a pass. rc=1 is an assertion failure.
    dump_failure_evidence
    [ "$V2_RC" -eq 2 ] && die "dsh V2 journey aborted on ENV GAP (rc=2) — environment incomplete" \
      || die "dsh V2 journey failed (rc=$V2_RC)"
  fi
  echo
  ok "════ L3 PASS: $PLATFORM / dsh — V2 activation code → host bind → welcome loop closed"
  # the journey killed ②'s gateway itself (GW_PID_L3); ②'s pid is already gone
  GW_PID=""
  exit 0
fi

# ── end of the dsh cell ──────────────────────────────────────────────────────────────
# Everything below this point used to be the CLI-cell tail (aimail install → host
# webhook up → CLI `aimail welcome` → CLI signature probe). It was unreachable once the
# matrix became dsh-only and it depended on the retired CLI install path, so it was
# deleted (commit history keeps it). Reaching this line means the dsh journey fell
# through without exiting — that is a gate bug, not a pass.
die "internal: agent '$AGENT' reached the end of run-l3.sh without completing the dsh V2 journey"

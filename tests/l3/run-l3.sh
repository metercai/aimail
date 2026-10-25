#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# run-l3.sh — L3 integration closed-loop regression (driven by the matrix in l3.yml).
#
# Regression content **completely identical**, does not vary with OS/agent (D3 ruling):
#   ① Install target agent system + configure LLM (deepseek-flash) + verify agent itself can run
#   ② Get published gateway (basic version, GitHub Release binary) and start it, capture system/admin key
#   ③ Get published CLI (bootstrap.sh online = real user entry point)
#   ④ SDK installation (README original commands: aimail install (home provided by AGENT_HOME env) / dsh plugin add)
#   ⑤ welcome closed loop: welcome → agent replies with three tags → manager (security officer) approves persona
#   ⑥ Identity assertion: SDK isomorphic send_mail sends one query → whoami reads back the identity card (agent_persona)
#      + email signature (agent_signature) is effective (whoami.rs:44-52, welcome approve UPSERT read-back point)
#
# Activation flow: only goes through **admin-key** (basic version gateway); does not test activation code (advanced version).
# Test targets: all from published channels (GitHub Release / PyPI / npm), never from the working tree.
#
# Environment (injected by workflow):
#   L3_PLATFORM  linux-amd64|linux-arm64|macos-arm64|windows-amd64
#   L3_AGENT     hermes | dsh-aimail | dsh-plugin
#                (dsh two README published paths each one slot: AGENT_HOME env + aimail install / dsh plugin add)
#   AIMAIL_REPO  aimail repo checkout (environment preparation single source + llm-config.py both in tests/agent-env/)
#   LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
#
# Process contract (README Quick Start scenario 4, reproduced as-is, strictly no self-adjustment):
#   ③ env four-piece set (AIMAIL_GW_URL/ADMIN_KEY/DOMAIN/MANAGER_ADDRESS) + online bootstrap curl|bash
#   ④ aimail install (home provided by AGENT_HOME env, no --home flag) or dsh plugin --profile web add agent-aimail
#   ⑤ aimail welcome (AGENT_HOME env → .agentmail pointer resolves system parameters)  # contract-allowed: L3 comment references contract pointer filename
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
  # Product's own criteria (historically in L2 it reported '✗ toolset: … platform_toolsets MISSING')
  [ -x "${AIMAIL_BIN:-/nonexistent}" ] && { echo "  [Product Self-Check] aimail check:"; "$AIMAIL_BIN" check 2>&1 | tail -30 | sed 's/^/    /'; }
  echo "  ──────────────────────────────────"
}

PLATFORM="${L3_PLATFORM:?L3_PLATFORM not set}"
AGENT="${L3_AGENT:?L3_AGENT not set}"
AIMAIL_REPO="${AIMAIL_REPO:?AIMAIL_REPO not set}"
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
trap 'cp -f "$WORK"/* "$LOG_ARCHIVE/" 2>/dev/null; cp -f "$LOG" "$LOG_ARCHIVE/" 2>/dev/null; true' EXIT

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
  hermes)
    # Official install (source tree+venv, **not pip**) + disable age gate + LLM config all in single source prepare.sh:
    # CLI adapter's health_checks checks source file {home}/hermes-agent/gateway/platforms/webhook.py
    # and hermes_cli/profiles.py ⇒ pip wheel layout must fail; hermes tree's own pyproject.toml has
    # `[tool.uv] exclude-newer="14 days"`, uv discovers it upwards from CWD ⇒ installing SDK will silently fall back to old version.
    HERMES_HOME="${HOME}/.hermes"
     export HERMES_HOME   # child processes (prepare/verify) locate the install tree/launcher via the host's real home
    AGENT_HOME="$HERMES_HOME"
    # LLM dual write (journey J4b authoritative form): --home lands in scratch (verify reads it, not read by host process),
    # --extra-home lands in real $HERMES_HOME (host process reads it).
    bash "$AGENT_ENV/prepare.sh" hermes \
      --home "$WORK/llm-scratch" --extra-home "$HERMES_HOME" \
      --llm-url "$LLM_BASE_URL" --llm-model "$LLM_MODEL" --llm-key "$LLM_API_KEY" \
      || die "hermes env prepare failed (single source: tests/agent-env)"
    VERIFY_HOME="$WORK/llm-scratch"
    VERIFY_EXTRA=(--extra-home "$HERMES_HOME")
    ok "hermes env ready (official install + LLM configured; single source)"
    ;;
  dsh|dsh-aimail|dsh-plugin)
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
  *) die "unknown agent $AGENT" ;;
esac

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
# Under 9-cell matrix concurrency, unauthenticated calls will hit rate limit (403, observed 2026-10-09 macos two cells),
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
case "${L3_PLATFORM:-}" in
  linux-arm64|macos-arm64) WELCOME_TIMEOUT=300 ;;
esac
curl -fsSL "https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh" | bash \
  || die "online bootstrap failed (published CLI unavailable)"
# bootstrap landing point ~/.local/bin relies on PATH reachability (product contract: bootstrap log self-proves
# "⚠ add to PATH: export PATH=~/.local/bin:$PATH"). unix runner default PATH already includes it;
# Windows git-bash default PATH does not include it ⇒ node side execFile('aimail') ENOENT
# (L3 2026-10-17 empirical: dsh-host.log "aimail CLI not found ('aimail')",
# same cell linux green = default PATH includes it). Explicit export = reproducing the contract published by bootstrap, not a product change.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    export PATH="$HOME/.local/bin:$PATH"
    ;;
esac
AIMAIL_BIN="$(command -v aimail || echo "$HOME/.local/bin/aimail")"
[ -x "$AIMAIL_BIN" ] || die "aimail binary not on PATH after bootstrap: $AIMAIL_BIN"
ok "published CLI: $( "$AIMAIL_BIN" version 2>/dev/null | head -1 )"

# ── ④ aimail install / dsh plugin add (the two entry points published in README) ──────
# README: "install either through the aimail command line or through the Agent's
# plugin — pick one of the two". Both paths are publicly published ⇒ both are tested (matrix agent dimension
# extension: hermes / dsh-aimail / dsh-plugin). Command form = exactly as in README:
#   CLI entry point = `aimail install` (home is provided by export AGENT_HOME starting with ④, 
#   Shares the same env as ⑤ welcome; no --home flag).
#   Plugin entry = `dsh plugin --profile web add agent-aimail` (no AGENT_HOME dependency).
# env has been persisted by ③ to ~/.aimail/.env + exported in this shell.
echo "── [4/6] SDK install (README command shape)"
export AGENT_HOME="$AGENT_HOME"
case "$AGENT" in
  dsh-plugin)
    # README: dsh plugin --profile web add agent-aimail (Agent plugin entry)
    DSH_HOME="$DSH_HOME" dsh plugin --profile web add agent-aimail \
      || die "dsh plugin add agent-aimail failed (README plugin path)"
    ;;
  dsh-aimail|hermes)
    # README: aimail install (AGENT_HOME env provides home; shared with welcome)
    "$AIMAIL_BIN" install \
      || die "aimail install failed (README CLI path)"
    ;;
esac
ok "install done (system activated + platform adapted)"

# ── ④b Start agent host (receive and reply to mail) ────────────────────────────────────
# welcome closed loop requires the host to be **alive**: the gateway POSTs the welcome message to the bound webhook_url,
# the host's LLM turn calls send_mail to reply, and the CLI polls until three labels are present to approve.
# Installing without starting ⇒ welcome times out at 120s (first run will definitely be red). The inbound endpoint is wired by the registration step of install,
# here we only start the host + wait for the port to be ready (waiting for the **same port written into the binding during registration**).
echo "── [4b/6] agent host up (inbound endpoint ready)"
HOST_PID=""
case "$AGENT" in
  hermes)
    # The host process reads $HERMES_HOME/config.yaml (install registration step has already written platforms.webhook).
    # webhook port = extra.port written by _next_available_webhook_port(base 8644) during registration;
    # gateway listens on the same port when starting the platform ⇒ read it back from config, do not guess or hardcode.
    # Pure standard library text reading (macOS system python3 has no PyYAML): find the top-level platforms: block
    # webhook: extra: port (active lines only, comment lines ignored). The install registration step writes
    # platforms.webhook.{enabled, extra:{host,port,secret}}(aimail_hermes.py)。
    HERMES_PORT="$(python3 - "$HERMES_HOME/config.yaml" <<'PY'
import re, sys
port = 0
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
in_plat = in_wh = in_extra = False
for ln in lines:
    s = ln.strip()
    if s.startswith("#"):
        continue
    if not ln.startswith(" "):
        in_plat = (s == "platforms:")
        in_wh = in_extra = False
        continue
    if in_plat and ln.startswith("  ") and not ln.startswith("   "):
        in_wh = (s == "webhook:")
        in_extra = False
        continue
    if in_wh and ln.startswith("    ") and not ln.startswith("     "):
        if s == "extra:":
            in_extra = True
        elif s.startswith("port:"):
            port = int(re.match(r"port:\s*(\d+)", s).group(1))
            in_extra = False
        else:
            in_extra = False
        continue
    if in_extra and ln.startswith("      ") and s.startswith("port:"):
        port = int(re.match(r"port:\s*(\d+)", s).group(1))
print(port)
PY
)" || die "cannot read hermes webhook port"
    [ "${HERMES_PORT:-0}" -gt 0 ] 2>/dev/null || die "hermes platforms.webhook.port not set (install did not wire the webhook)"
    # launcher uses the single source of truth (tests/agent-env/agent-paths.sh). Previously this block had its own assignment + `[ -x ]` guard,
    # 2026-10-11 was incidentally deleted when wired to single-source agent-env, leaving only the consumer below ⇒ CI reports
    # `HERMES_BIN: unbound variable` (set -u interrupts directly; hermes host never started, port 8644 never ready,
    # L3 9/9 red). The guard is restored together: if the launcher is not found, it must fail loudly, not silently start an empty command.
    # shellcheck source=../agent-env/agent-paths.sh
    . "$(dirname "${BASH_SOURCE[0]}")/../agent-env/agent-paths.sh"
    HERMES_BIN="$(agent_launcher hermes "${HERMES_HOME:-}" || true)"
    [ -x "$HERMES_BIN" ] || die "hermes launcher not found after install (searched <home>/hermes-agent/.hermes/bin, /opt/hermes/bin, <home>/.local/bin, PATH)"
    HERMES_HOME="$HERMES_HOME" nohup "$HERMES_BIN" gateway run > "$WORK/hermes-host.log" 2>&1 &
    HOST_PID=$!
    say "hermes host starting (webhook port $HERMES_PORT)…"
    UP=0
    for _ in $(seq 1 120); do
      python3 -c "import socket,sys;s=socket.socket();sys.exit(s.connect_ex(('127.0.0.1',$HERMES_PORT)))" 2>/dev/null && { UP=1; break; }
      kill -0 "$HOST_PID" 2>/dev/null || { tail -15 "$WORK/hermes-host.log" | sed 's/^/    /'; die "hermes host exited before inbound port $HERMES_PORT was ready"; }
      sleep 1
    done
    [ "$UP" = 1 ] || { tail -15 "$WORK/hermes-host.log" | sed 's/^/    /'; die "hermes inbound port $HERMES_PORT not up in 120s"; }
    ok "hermes host up (inbound :$HERMES_PORT)"
    # ── send_mail tool exposure assertion (after install, before welcome) ──────────────────────
    # send_mail exposure in webhook session = platform_toolsets.webhook in config.yaml
    # contains the contract toolset (read during hermes boot, written by install's ensure_config).
    # Inbound port being alive only proves the host started, not that the tool is exposed —— known historical root cause
    # (ensure_config.py:4-11 "receives but cannot send out"): missing it ⇒ webhook session has no send_mail ⇒
    # after welcome trigger, agent cannot reply ⇒ 120s timeout. Fail loudly here, don't let welcome take the blame.
    # dsh cell does not need an equivalent assertion: send_mail (tool-mail) and mail-inbound are the same atomic bundle
    # (cordis.patch.yml), boot fail-loud ⇒ the V7 inbound routing gate above (routing already mounted) has transitively
    # guaranteed tool-mail registration; a duplicate probe is redundant.
    # toolset name is read from the contract (literal ratchet: hardcoding contract values is forbidden, same as dump_failure_evidence taking skill).
    TS="$(python3 - "${AIMAIL_REPO:-}/contract/aimail-contract.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["agent_toolset_name"])
PY
)" || die "cannot read agent_toolset_name from contract"
    [ -n "$TS" ] || die "empty agent_toolset_name from contract"
    PT="$(python3 - "$HERMES_HOME/config.yaml" <<'PY'
import sys
# No PyYAML (L3 container macOS system python3 has none) ⇒ pure standard library text parsing, same discipline as the port parsing above.
# Compatible with three yaml.safe_dump forms: sibling block list (default, 2 spaces, same level as webhook:),
# nested block list (4 spaces), flow list ([a, b]).
tools = []
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
n = len(lines)
i = 0
while i < n and not (lines[i].strip() == "platform_toolsets:" and not lines[i].startswith(" ")):
    i += 1
i += 1  # enter platform_toolsets block
web_indent = None
while i < n:
    ln = lines[i]
    s = ln.strip()
    if not s or s.startswith("#"):
        i += 1
        continue
    ind = len(ln) - len(ln.lstrip(" "))
    if ind == 0:
        break  # leave platform_toolsets block
    if web_indent is None and ind == 2 and s.startswith("webhook:"):
        web_indent = ind
        rest = s[len("webhook:"):].strip()
        if rest.startswith("["):
            tools = [x.strip().strip('"').strip("'") for x in rest.strip("[]").split(",") if x.strip()]
        i += 1
        continue
    if web_indent is not None and s.startswith("- ") and ind >= web_indent:
        tools.append(s[2:].strip().strip('"').strip("'"))
        i += 1
        continue
    if web_indent is not None and ind <= web_indent:
        break  # next sibling key (webhook list ends)
    i += 1
print(" ".join(tools))
PY
)" || die "cannot read hermes platform_toolsets.webhook from $HERMES_HOME/config.yaml"
    case " $PT " in
      *" $TS "*) : ;;
      *) die "send_mail not exposed to webhook sessions: platform_toolsets.webhook=[$PT] (missing $TS) — install's ensure_config did not wire the toolset; welcome cannot reply" ;;
    esac
    ok "send_mail exposed to webhook sessions (platform_toolsets.webhook ⊇ $TS)"
    ;;
  dsh|dsh-aimail|dsh-plugin)
    # inbound = agent-aimail plugin's mail-inbound server (host lifecycle self-managed;
    # the shared listenWithRetry bind starts with the host). On registration
    # localWebhook = inboundUrl (common default inbound_port=9099) ⇒ bound
    # webhook_url port = 9099 (default, unless overridden by AIMAIL_INBOUND_URL/PORT env).
    DSH_INBOUND_PORT="${AIMAIL_INBOUND_PORT:-9099}"
    DSH_HOME="$DSH_HOME" AIMAIL_HOME="${HOME}/.aimail" AIMAIL_INBOUND_PORT="$DSH_INBOUND_PORT" \
      LLM_API_KEY="$LLM_API_KEY" \
      nohup dsh --profile web --port 0 --no-open > "$WORK/dsh-host.log" 2>&1 &
    HOST_PID=$!
    say "dsh host starting (inbound port $DSH_INBOUND_PORT)…"
    UP=0
    for _ in $(seq 1 120); do
      python3 -c "import socket,sys;s=socket.socket();sys.exit(s.connect_ex(('127.0.0.1',$DSH_INBOUND_PORT)))" 2>/dev/null && { UP=1; break; }
      kill -0 "$HOST_PID" 2>/dev/null || { tail -15 "$WORK/dsh-host.log" | sed 's/^/    /'; die "dsh host exited before inbound port $DSH_INBOUND_PORT was ready"; }
      sleep 1
    done
    [ "$UP" = 1 ] || { tail -15 "$WORK/dsh-host.log" | sed 's/^/    /'; die "dsh inbound port $DSH_INBOUND_PORT not up in 120s"; }
    ok "dsh host up (inbound :$DSH_INBOUND_PORT)"
    # ── Plugin inbound route readiness gate (V7, L2 same source: J4e flaky mechanism 2026-10-13)──────────────
    # Port listening ≠ plugin route mounting: agent-aimail plugin's mail-inbound route mounts later than host port,
    # if gateway welcome first push falls in unmounted window ⇒ 404 ⇒ 3 retries (5s/15s/40s) exhausted ⇒ permanent loss
    # (L2 j4e6 evidence: 360s zero reply). L3 runs each cell once ⇒ must ensure mounting before welcome.
    # Criterion (L2 journey same source): unsigned POST to bound webhook_url —— handler resolve precedes signature verification,
    # empty body ⇒ 200 no_agent = mounted; 404 = host present but route not mounted; 000 = connection refused.
    # Target to probe = bound webhook_url (the same URL the gateway pushes welcome to, single source of truth, no guessing).
    # Binding filename = contract binding_file constant; shell has no import constant module, only literal glob possible.
    DSH_BIND="$(ls "${HOME}/.aimail/systems/${SYSTEM_ID}"/*/agentmail.json 2>/dev/null | head -1)"  # contract-allowed: shell probe glob real disk binding file (contract binding_file); shell has no import constant module, literal is only way
    [ -n "$DSH_BIND" ] || die "dsh binding not found under ${HOME}/.aimail/systems/${SYSTEM_ID} (install did not write it)"
    # Windows git-bash: ls glob returns MSYS form /c/Users/.../agentmail.json, native  # contract-allowed: L3 comment references contract binding filename
    # Windows python does not recognize /c/ prefix (treats as relative path) ⇒ open fails (L3 2026-10-17 evidence:
    # FileNotFoundError '/c/Users/runneradmin/.aimail/...' ⇒ misjudged as no webhook_url).
    # cygpath -m converts to C:/Users/... (forward slashes): Windows API accepts, and python string has no
    # backslash escape surface (-w's C:\Users in python source \U is unicode escape ⇒ SyntaxError).
    # unix branch unchanged.
    case "$(uname -s)" in
      MINGW*|MSYS*|CYGWIN*) DSH_BIND_PY="$(cygpath -m "$DSH_BIND")" ;;
      *) DSH_BIND_PY="$DSH_BIND" ;;
    esac
    DSH_BIND_URL="$(python3 -c "import json;print(json.load(open('$DSH_BIND_PY', encoding='utf-8')).get('webhook_url') or '')")"
    [ -n "$DSH_BIND_URL" ] || die "dsh binding has no webhook_url (pull mode — welcome push cannot be judged)"
    say "dsh inbound route readiness: probing $DSH_BIND_URL (mounted = 200 no_agent; 404 = not mounted)"
    RDY=0; _i=0
    for _i in $(seq 1 120); do
      _code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 -X POST "$DSH_BIND_URL" -H 'content-type: application/json' -d '{}')"
      case "$_code" in 200|401|403) RDY=1; break ;; esac
      sleep 2
    done
    [ "$RDY" = 1 ] || { tail -15 "$WORK/dsh-host.log" | sed 's/^/    /'; die "dsh plugin inbound route not mounted in 240s (last probe code=$_code, URL=$DSH_BIND_URL) — welcome would race the gateway's 3 retries"; }
    ok "dsh inbound route mounted (probe code=$_code, ready time ≈$((_i * 2))s)"
    ;;
esac

# ── ⑤ welcome closed loop (welcome → agent replies three tags → manager approve)──
echo "── [5/6] welcome closed loop (manager approves identity card + signature)"
# README as-is: `aimail welcome`. Context resolution chain (welcome.rs:25-91):
#   sid:      AGENT_HOME env (④ already exported, shared by install and welcome)
#             → {AGENT_HOME}/.agentmail pointer (written during install/plugin registration) → end;  # contract-allowed: L3 comment references contract pointer filename
#             user does not know system_id, does not pass --system-id.
#   manager:  -m flag → AIMAIL_MANAGER_ADDRESS env (③ already exported + bootstrap persisted to .env)
#             → config.manager_address。
#   admin_key: systems/<sid>/aimail_gateway.json (written by install, welcome.rs:102).
# Requires agent inbound endpoint started (install already wired), and agent+LLM can generate three-tag reply.
"$AIMAIL_BIN" welcome --timeout "${WELCOME_TIMEOUT:-120}" 2>&1 | tee "$WORK/welcome.log"
WELC_RC=${PIPESTATUS[0]}
[ "$WELC_RC" -eq 0 ] || { dump_failure_evidence; die "welcome closed loop failed (rc=$WELC_RC) — $(tail -12 "$WORK/welcome.log")"; }
ok "welcome loop complete (identity card + signature approved)"

# ── ⑥ Query mail signature assertion ─────────────────────────────────────────
echo "── [6/6] query-mail signature assertion"
# Use the address-level key from the agent binding to send a **query email** (POST /api/v1/send, SDK send_mail same shape,
# v1 signature protocol), then whoami reads back gateway-side effective identity card (agent_persona) + mail signature
# (agent_signature) —— authoritative assertion point for UPSERT to domain_addr_meta after welcome approve
# (whoami.rs:44-52; CLI check/l1 same source).
# Windows: [6/6] last line print contains ✓ (U+2713), pipe stdout default cp1252 cannot encode
# (L3 2026-10-17 evidence: UnicodeEncodeError '\u2713' position 2 —— persona/signature
# already read back, only last line missing). PYTHONIOENCODING=utf-8 overrides stdout/stderr encoding;
# unix default is UTF-8 ⇒ behavior unchanged.
PYTHONPATH="$AIMAIL_REPO" PYTHONIOENCODING=utf-8 python3 - "$GW_URL" "$SYSTEM_ID" "$AIMAIL_MANAGER_ADDRESS" <<'PYEOF' || die "signature assertion failed"
import sys, json, os, time, urllib.request, urllib.error
import hashlib, hmac

gw_url, system_id, manager_addr = sys.argv[1], sys.argv[2], sys.argv[3]

# ── 1) Locate agent binding file (single source of truth dropped by install, check/l0 same source) ──
# Layout: ~/.aimail/systems/<sid>/*/agentmail.json —— fields email/api_key required  # contract-allowed: L3 validates install written contract binding file
# (BINDING_REQUIRED, cli/src/core/checks/l0.rs:36)。
home = os.path.expanduser("~/.aimail")
binding = None
base = os.path.join(home, "systems", system_id)
for entry in sorted(os.listdir(base)) if os.path.isdir(base) else []:
    aj = os.path.join(base, entry, "agentmail.json")  # contract-allowed: L3 reads contract binding file
    if os.path.isfile(aj):
        binding = json.load(open(aj, encoding="utf-8"))
        break
if not binding:
    print(f"  ✗ no agentmail.json under {base} (install did not adapt the platform)"); sys.exit(1)  # contract-allowed: L3 error references contract-bound filename
email, key = binding.get("email", ""), binding.get("api_key", "")
if not email or not key:
    print(f"  ✗ binding missing email/api_key: {list(binding.keys())}"); sys.exit(1)
print(f"  agent address : {email}")

# ── 2) Send a query email (SDK send_mail isomorphic: POST /api/v1/send, agent key) ──
# v1 signature protocol (aimail_base.py:29-59 exact replica): HMAC key = sha256(raw_key),
# base = METHOD\npath\nms_timestamp\nsha256(body); Identity header X-Api-Identity = email of the address-level key.
def signed(method, path, body_obj=None, api_key=key, identity=email):
    body = json.dumps(body_obj).encode() if body_obj is not None else b""
    ts = str(int(time.time() * 1000))
    body_hash = hashlib.sha256(body).hexdigest()
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    base_str = f"{method.upper()}\n{path}\n{ts}\n{body_hash}"
    sig = hmac.new(key_hash.encode("utf-8"), base_str.encode("utf-8"), hashlib.sha256).hexdigest()
    req = urllib.request.Request(gw_url.rstrip("/") + path, data=body or None, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Api-Identity", identity)
    req.add_header("X-Api-Timestamp", ts)
    req.add_header("X-Api-Signature", sig)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode(errors="replace")

marker = f"L3SIG-{int(time.time())}"
st, resp = signed("POST", "/api/v1/send",
                  {"to": manager_addr, "subject": f"query {marker}",
                   "markdown": f"query {marker}: identity + signature check"})
if st not in (200, 201, 202):
    print(f"  ✗ query mail send failed: HTTP {st} {str(resp)[:200]}"); sys.exit(1)
print(f"  query mail sent (HTTP {st}) — persona/signature are applied by the gateway")

# ── 3) Assertion: whoami reads back identity card (agent_persona) + email signature (agent_signature) ──
# whoami.rs:44-52 —— after welcome approve, gateway UPSERTs to domain_addr_meta, whoami reads back.
# This is the authoritative assertion point for "identity card + email signature is effective" (same source as CLI check/l1, checks/l1.rs:130).
st, who = signed("GET", "/api/v1/whoami")
if st != 200:
    print(f"  ✗ whoami failed: HTTP {st} {str(who)[:200]}"); sys.exit(1)
persona = (who.get("agent_persona") or "").strip()
sig     = (who.get("agent_signature") or "").strip()
print(f"  agent_persona    : {persona[:60] or '(empty)'}")
print(f"  agent_signature  : {sig[:60] or '(empty)'}")
if not persona or not sig:
    print("  ✗ identity card / signature not in effect after welcome approve"); sys.exit(1)
print("  ✓ identity card (persona) + email signature in effect")
PYEOF

echo
ok "════ L3 PASS: $PLATFORM / $AGENT — CLI×SDK×gateway integration loop completed"
# teardown
kill "$GW_PID" 2>/dev/null || true
[ -n "${HOST_PID:-}" ] && kill "$HOST_PID" 2>/dev/null || true
exit 0

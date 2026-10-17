#!/usr/bin/env bash
#
# agent-env/prepare.sh — the SINGLE entry point that prepares a test environment for one
# of the five agent hosts (hermes | openclaw | dsh | pi | deerflow).
#
# Contract (authoritative text: README.md):
#   1. the agent is installed by its OFFICIAL method (no repo/snapshot builds);
#   2. the base environment is installed AND configured (interpreter, package manager,
#      LLM config) — never patched up during the test run;
#   3. the agent runs and its LLM answers (probe; SKIP-not-pass when unreachable);
#   4. the aimail CLI / pysdk / tssdk are NEVER pre-installed here (they are the thing
#      under test — verify.sh asserts they are absent);
#   5. NO package-manager release-age gate is left in the environment:
#        python/uv : <tree>/uv.toml with `exclude-newer = false`
#        node/pnpm : minimumReleaseAge = 0 (one npmrc, npm+pnpm both read it)
#      Why: uv walks up from the CWD and the hermes tree pins
#      `[tool.uv] exclude-newer = "14 days"` in its own pyproject.toml; pnpm applies a
#      release-age gate of its own. Both silently resolve an *older* aimailsdk than the
#      registry latest — the install "succeeds" and ships a stale package. That is a
#      property of the ENVIRONMENT, so it is removed once, here, not re-patched per run.
#   6. the result is verified by verify.sh (fail-closed): a prepare that cannot pass the
#      contract must fail loudly instead of producing a half-ready environment.
#
# Idempotent: re-running is a no-op for steps that are already in place.
#
# Usage:
#   prepare.sh <agent> [--home PATH] [--llm-url URL] [--llm-model M] [--llm-key K]
#                      [--extra-home PATH]... [--no-verify]
#
set -uo pipefail
CTX="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Version pins (single source: pins.env next to this script, maintained by update-pins.sh).
# Tests must start from the same environment every time; "is it the latest?" is a
# maintenance decision, not a test-time one. Installs below are exact versions and
# verify.sh asserts what actually landed.
if [ -f "$CTX/pins.env" ]; then . "$CTX/pins.env"
else echo "✗ prepare: $CTX/pins.env missing (the pinned versions are the testing start point)" >&2; exit 1
fi

die() { echo "✗ prepare: $*" >&2; exit 1; }
say() { echo "  · $*"; }
ok()  { echo "  ✓ $*"; }

AGENT="${1:-}"; shift || true
[ -n "$AGENT" ] || die "usage: prepare.sh <hermes|openclaw|dsh|pi|deerflow> [--home PATH] [--llm-url URL] [--llm-model M] [--llm-key K] [--extra-home PATH] [--no-verify]"

HOME_DIR=""
LLM_URL="${LLM_BASE_URL:-}"
LLM_MODEL="${LLM_MODEL:-}"
LLM_KEY="${LLM_API_KEY:-${DEEPSEEK_API_KEY:-}}"
EXTRA_HOMES=()
NO_VERIFY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --home)       HOME_DIR="${2:-}"; shift 2 ;;
    --llm-url)    LLM_URL="${2:-}"; shift 2 ;;
    --llm-model)  LLM_MODEL="${2:-}"; shift 2 ;;
    --llm-key)    LLM_KEY="${2:-}"; shift 2 ;;
    --extra-home) EXTRA_HOMES+=("${2:-}"); shift 2 ;;
    --no-verify)  NO_VERIFY=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

# Default platform home: HERMES_HOME/DSH_HOME already encode the docker-vs-host layout
# for the two python/node hosts that exist in both worlds.
default_home() {
  case "$1" in
    hermes)   echo "${HERMES_HOME:-$HOME/.hermes}" ;;
    openclaw) echo "${HOME}/.openclaw" ;;
    dsh)      echo "${DSH_HOME:-$HOME/.dsh}" ;;
    pi)       echo "${HOME}/.pi" ;;
    deerflow) echo "/app" ;;
    *) die "unknown agent: $1" ;;
  esac
}
[ -n "$HOME_DIR" ] || HOME_DIR="$(default_home "$AGENT")"

# ── (1) official install (idempotent) ────────────────────────────────────────────────

# npm_install_pinned <pkg> <version> <command>: install one exact version (idempotent) and
# never silently keep a different one.
npm_install_pinned() {
  local pkg="$1" ver="$2" cmd="$3" have=""
  [ -n "$ver" ] || die "$pkg: no pinned version in pins.env"
  command -v "$cmd" >/dev/null 2>&1 && have="$("$cmd" --version 2>/dev/null | head -1 | tr -d 'v')"
  if [ -n "$have" ] && [ "${have#"$ver"}" != "$have" ]; then
    say "$cmd already at pinned $ver"; return 0
  fi
  say "npm install -g $pkg@$ver${have:+ (replacing $have)}"
  npm install -g "$pkg@$ver" --no-audit --no-fund || die "npm install $pkg@$ver failed"
}

install_hermes() {
  local hh="${HERMES_HOME:-$HOME/.hermes}"
  if [ -x /opt/hermes/bin/hermes ] || [ -x "$hh/hermes-agent/.hermes/bin/hermes" ] \
     || [ -f "$hh/bin/hermes.exe" ] || [ -f "$hh/hermes-agent/.hermes/bin/hermes.exe" ]; then
    say "hermes present (official layout) — skip install"; return 0
  fi
  local tmp; tmp="$(mktemp -d)"
  if [[ "$(uname -s)" == MINGW* ]]; then
    # Windows(git-bash): 官方安装器 install.sh 拒 MINGW("On Windows use install.ps1")。
    # install.ps1 布局与 install.sh 同树: tree=$hh/hermes-agent, launcher→$hh/bin/hermes.exe。
    curl -fsSL --retry 2 --connect-timeout 20 --max-time 60 \
      https://hermes-agent.nousresearch.com/install.ps1 -o "$tmp/install.ps1" \
      || die "hermes install.ps1 download failed"
    _hps=(); [ -n "${HERMES_COMMIT:-}" ] && _hps=(-Commit "$HERMES_COMMIT")
    say "pinned hermes commit: ${HERMES_COMMIT:-<installer default>}"
    # 最小化: L3 只用 host + LLM + webhook 入站 + aimail preprocessor,
    # 浏览器栈(agent-browser/chromium)与 computer-use(cua-driver)零消费者,
    # 经 hermes 官方裁剪口 -SkipBrowser/-SkipComputerUse 裁掉(pm declined 记录)。
    HERMES_HOME="$hh" powershell -NoProfile -ExecutionPolicy Bypass -File "$tmp/install.ps1" \
      -SkipSetup -SkipBrowser -SkipComputerUse ${_hps[@]+"${_hps[@]}"} </dev/null \
      || die "hermes official install (install.ps1) failed"
    ok "hermes installed (official install.ps1) at $hh"
    return 0
  fi
  curl -fsSL --retry 2 --connect-timeout 20 --max-time 60 \
    https://hermes-agent.nousresearch.com/install.sh -o "$tmp/install.sh" \
    || die "hermes install.sh download failed"
  _hargs=(); [ -n "${HERMES_COMMIT:-}" ] && _hargs=(--commit "$HERMES_COMMIT")
  say "pinned hermes commit: ${HERMES_COMMIT:-<installer default>}"
  HERMES_HOME="$hh" bash "$tmp/install.sh" ${_hargs[@]+"${_hargs[@]}"} </dev/null \
    || die "hermes official install failed"
  ok "hermes installed (official install.sh) at $hh"
}

install_dsh() {
  command -v npm >/dev/null 2>&1 || die "npm missing — dsh install needs node/npm on PATH"
  npm_install_pinned @deepseek-ai/dsh "${DSH_VERSION:-}" dsh
  # pnpm is dsh's own plugin-manager backend: it is part of dsh's base environment and
  # must be prepared here (a cold container would otherwise pull it on first use).
  npm_install_pinned pnpm "${PNPM_VERSION:-}" pnpm
  command -v pnpm >/dev/null 2>&1 || die "pnpm unavailable — dsh plugin install needs pnpm on PATH"
  # profile skeleton = the LLM writer target AND the plugin install target. Bounded so a
  # cold pnpm pull cannot hang the preparation.
  _to=""; command -v timeout >/dev/null 2>&1 && _to="timeout 180"
  DSH_HOME="$HOME_DIR" $_to dsh --profile web --dump-config >/dev/null 2>&1 \
    || die "dsh profile warmup failed (profiles/web not created)"
  [ -f "$HOME_DIR/profiles/web/cordis.patch.yml" ] \
    || die "dsh warmup did not create profiles/web/cordis.patch.yml"
  ok "dsh installed (official npm) + pnpm present + profile warmed"
}

install_pi() {
  command -v npm >/dev/null 2>&1 || die "npm missing — pi install needs node/npm on PATH"
  npm_install_pinned @earendil-works/pi-coding-agent "${PI_VERSION:-}" pi
  mkdir -p "$HOME_DIR/agent"
  ok "pi installed (official npm)"
}

install_openclaw() {
  if command -v openclaw >/dev/null 2>&1; then ok "openclaw present (official image)"; return 0; fi
  # The official distribution is the published image; no separate runner installer is
  # verified here, and inventing one would be a guess.
  die "openclaw not present — its official distribution is the published image (ghcr.io/openclaw/openclaw)"
}

require_deerflow_base() {
  [ -x /app/backend/.venv/bin/python ] \
    || die "deerflow base missing — build the source-rebuilt base image first (tests/.../rebuild-deerflow-base.sh)"
  ok "deerflow base present (source-rebuilt)"
}

case "$AGENT" in
  hermes)   install_hermes ;;
  dsh)      install_dsh ;;
  pi)       install_pi ;;
  openclaw) install_openclaw ;;
  deerflow) require_deerflow_base ;;
  *) die "unknown agent: $AGENT" ;;
esac

# ── (5) remove package-manager release-age gates, once, in the environment ───────────
disable_age_gates() {
  local -a trees=()
  case "$AGENT" in
    hermes)   trees=(/opt/hermes "${HERMES_HOME:-$HOME/.hermes}/hermes-agent" /opt/data/hermes-agent) ;;
    deerflow) trees=(/app /app/backend) ;;
    *)        trees=(/app) ;;
  esac
  local t
  for t in "${trees[@]}"; do
    [ -d "$t" ] || continue
    [ -f "$t/pyproject.toml" ] || [ -f "$t/uv.toml" ] || continue
    # uv.toml in the same directory takes precedence over pyproject.toml's [tool.uv].
    # Canonical override = a far-future cutoff: every version ever published is older
    # than it, i.e. the gate is off. A far-future *timestamp string* is understood by
    # every uv (the hermes base ships 0.11.6, which rejects the newer `false` form with
    # "invalid type: boolean `false`, expected a timestamp string"); it is verified
    # functionally in verify.sh.
    if grep -qsE '^[[:space:]]*exclude-newer[[:space:]]*=[[:space:]]*(false|"2(1|2)[0-9][0-9])' "$t/uv.toml" 2>/dev/null; then
      continue
    fi
    printf '# Added by tests/agent-env/prepare.sh: disable the release-age gate.\n' >> "$t/uv.toml"
    printf 'exclude-newer = "2100-01-01T00:00:00Z"\n' >> "$t/uv.toml" || die "cannot write $t/uv.toml"
    say "uv release-age gate disabled: $t/uv.toml"
  done
  # node side: npm writes $HOME/.npmrc, which pnpm reads too; then pnpm's own config.
  if command -v npm >/dev/null 2>&1; then
    npm config set minimumReleaseAge 0 >/dev/null 2>&1 \
      && say "npmrc: minimumReleaseAge=0" \
      || say "npm: could not set minimumReleaseAge (verify will judge)"
  fi
  if command -v pnpm >/dev/null 2>&1; then
    pnpm config set minimumReleaseAge 0 --location=global >/dev/null 2>&1 \
      || pnpm config set minimumReleaseAge 0 >/dev/null 2>&1 \
      || say "pnpm: could not persist minimumReleaseAge (verify will judge)"
  fi
}
disable_age_gates

# ── (2) LLM configuration (single-source writer) ─────────────────────────────────────
write_llm_config() {
  if [ -z "$LLM_URL" ] || [ -z "$LLM_MODEL" ]; then
    say "LLM not configured (no --llm-url/--llm-model) — SKIP write; verify will report it"
    return 0
  fi
  local -a args=(write --platform "$AGENT" --home "$HOME_DIR" --base-url "$LLM_URL" --model "$LLM_MODEL")
  [ -n "$LLM_KEY" ] && args+=(--api-key "$LLM_KEY")
  local e
  for e in ${EXTRA_HOMES[@]+"${EXTRA_HOMES[@]}"}; do [ -n "$e" ] && args+=(--extra-home "$e"); done
  python3 "$CTX/lib/llm-config.py" "${args[@]}" || die "LLM config write failed ($AGENT)"
  ok "LLM config written ($AGENT home=$HOME_DIR)"
}
write_llm_config

echo "════ agent-env prepare: $AGENT (home=$HOME_DIR)"
echo "── verify (fail-closed) ──"
if [ "$NO_VERIFY" = 1 ]; then
  say "verify skipped (--no-verify)"
  exit 0
fi
local_args=("$AGENT" --home "$HOME_DIR")
[ -n "$LLM_URL" ] && local_args+=(--llm-url "$LLM_URL")
[ -n "$LLM_MODEL" ] && local_args+=(--llm-model "$LLM_MODEL")
[ -n "$LLM_KEY" ] && local_args+=(--llm-key "$LLM_KEY")
for e in ${EXTRA_HOMES[@]+"${EXTRA_HOMES[@]}"}; do [ -n "$e" ] && local_args+=(--extra-home "$e"); done
bash "$CTX/verify.sh" "${local_args[@]}"

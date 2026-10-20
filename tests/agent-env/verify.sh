#!/usr/bin/env bash
#
# agent-env/verify.sh — acceptance checks for a prepared agent test environment.
# Run it INSIDE the environment being judged (the image / the runner host).
#
# Usage: verify.sh <agent> [--home PATH] [--llm-url URL] [--llm-model M] [--llm-key K] [--offline]
# Exit : 0 = every check passed · 1 = at least one red · 2 = nothing judgeable (all skipped)
#
# Checked contract (README.md):
#   1. official installation present (source/CLI layout — NOT a pip/npm wheel substitute)
#   2. base environment installed AND configured (interpreter, package manager, LLM config)
#   3. the agent runs and its LLM answers (a live probe; SKIP-not-pass when unreachable)
#   4. negative assertion: no aimail CLI / pysdk / tssdk pre-installed anywhere
#   5. no package-manager release-age gate left (uv exclude-newer / pnpm minimumReleaseAge):
#      asserted structurally AND functionally (a dry-run must resolve the registry latest)
#   6. offline self-sufficiency is what prepare + build are accepted on (--offline)
#
set -uo pipefail
CTX="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0; SKIP=0
pass() { echo "  ✓ $*"; PASS=$((PASS + 1)); }
red()  { echo "  ✗ $*"; FAIL=$((FAIL + 1)); }
skip() { echo "  · SKIP: $*"; SKIP=$((SKIP + 1)); }

# uv discovery — single source (used by [2] package-manager and [5] gate checks).
# Layouts, in order of preference:
#   PATH (old bases, e.g. deer-flow's /usr/local/bin/uv; official host installs)
#   <tree>/tools/uv-*/uv        (new official base image layout: v0.22.2+ keeps uv
#                                beside the portable CPython, NOT on PATH)
#   <home>/tools/uv-*/uv(.exe)  (Windows install.ps1 layout, Get-PmStoreRoot)
find_uv_bin() {
  local c p
  if command -v uv >/dev/null 2>&1; then
    echo "uv"; return 0
  fi
  for c in "$HOME_DIR" ${EXTRA_HOMES[@]+"${EXTRA_HOMES[@]}"} "${HERMES_HOME:-}" /opt/hermes; do
    [ -n "$c" ] || continue
    for p in "$c"/tools/uv-*/uv "$c"/tools/uv-*/uv.exe; do
      [ -x "$p" ] && { echo "$p"; return 0; }
    done
  done
  return 1
}

AGENT="${1:-}"; shift || true
[ -n "$AGENT" ] || { echo "usage: verify.sh <hermes|openclaw|dsh|pi|deerflow> [--home PATH] [--llm-url URL] [--llm-model M] [--llm-key K] [--offline]" >&2; exit 2; }
HOME_DIR=""; LLM_URL=""; LLM_MODEL=""; LLM_KEY=""; OFFLINE=0; EXTRA_HOMES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --home)       HOME_DIR="${2:-}"; shift 2 ;;
    --llm-url)    LLM_URL="${2:-}"; shift 2 ;;
    --llm-model)  LLM_MODEL="${2:-}"; shift 2 ;;
    --llm-key)    LLM_KEY="${2:-}"; shift 2 ;;
    --extra-home) EXTRA_HOMES+=("${2:-}"); shift 2 ;;
    --offline)    OFFLINE=1; shift ;;
    *) echo "verify: unknown argument: $1" >&2; exit 2 ;;
  esac
done

echo "════ agent-env verify: $AGENT (home=${HOME_DIR:-<default>}, offline=$OFFLINE)"

# ── 1. official installation ─────────────────────────────────────────────────────────
echo "── [1] official installation"
case "$AGENT" in
  hermes)
    # Official install = the source tree (its gateway/platforms/webhook.py is what the CLI
    # adapter's health check reads) + a launcher. The source tree can sit under any home we
    # were given: --home is the config home in the L3 form while the install lives in
    # --extra-home, and the docker image keeps it at /opt/hermes.
    _tree=""
    for _cand in /opt/hermes ${EXTRA_HOMES[@]+"${EXTRA_HOMES[@]}"} "$HOME_DIR" "${HERMES_HOME:-}"; do
      [ -n "$_cand" ] || continue
      if [ -d "$_cand/hermes-agent/gateway" ]; then _tree="$_cand/hermes-agent"; break; fi
      if [ -d "$_cand/gateway" ]; then _tree="$_cand"; break; fi
    done
    if [ -n "$_tree" ] && [ -f "$_tree/gateway/platforms/webhook.py" ]; then
      pass "hermes official source tree present: $_tree"
    else
      red "hermes official source tree missing (no gateway/platforms/webhook.py under /opt/hermes or <home>/hermes-agent)"
    fi
    # launcher 候选顺序的**单真源** = tests/agent-env/agent-paths.sh。本文件曾自带一份副本,
    # 与 run-l3.sh 的那份各自漂移, 最终导致 CI `HERMES_BIN: unbound variable`(L3 9/9 红)。
    # shellcheck source=agent-paths.sh
    . "$(dirname "${BASH_SOURCE[0]}")/agent-paths.sh"
    _bin="$(agent_launcher hermes "$HOME_DIR" || true)"
    [ -n "$_bin" ] && pass "hermes launcher present: $_bin" || red "hermes launcher not found (.hermes/bin/hermes, /opt/hermes/bin/hermes, ~/.local/bin/hermes)" ;;
  dsh)
    command -v dsh >/dev/null 2>&1 && pass "dsh on PATH ($(dsh --version 2>/dev/null | head -1))" || red "dsh not on PATH"
    [ -f "$HOME_DIR/profiles/web/cordis.patch.yml" ] && pass "dsh profile skeleton warmed" \
      || red "dsh profile skeleton missing ($HOME_DIR/profiles/web/cordis.patch.yml)" ;;
  pi)
    command -v pi >/dev/null 2>&1 && pass "pi on PATH ($(pi --version 2>/dev/null | head -1))" || red "pi not on PATH"
    [ -d "$HOME_DIR/agent" ] && pass "pi home skeleton present" || red "pi home skeleton missing ($HOME_DIR/agent)" ;;
  openclaw)
    command -v openclaw >/dev/null 2>&1 && pass "openclaw on PATH ($(openclaw --version 2>/dev/null | head -1))" || red "openclaw not on PATH" ;;
  deerflow)
    (cd /app/backend && .venv/bin/python -c 'import app') >/dev/null 2>&1 \
      && pass "deerflow app imports via its own venv" || red "deerflow app does not import (/app/backend/.venv)" ;;
  *) red "unknown agent $AGENT" ;;
esac

# ── 2. base environment (interpreter + package manager) + LLM config ────────────────
echo "── [2] base environment + LLM config"
case "$AGENT" in
  hermes|deerflow)
    # the interpreter the agent actually runs: a venv beside the tree (docker image) or the
    # uv-provisioned portable CPython under <home>/tools/python-<ver>-*/bin (unix official
    # host install) or tools/python-<ver>-.../python.exe (Windows, no /bin segment).
    # Judged by running it, not by the path existing.
    _py=""
    for _cand in "$HOME_DIR" ${EXTRA_HOMES[@]+"${EXTRA_HOMES[@]}"} /opt/hermes /app/backend "${HERMES_HOME:-}"; do
      [ -n "$_cand" ] || continue
      for c in "$_cand/.venv/bin/python3" "$_cand/.hermes/venv/bin/python3" "$_cand"/tools/python-*/bin/python3 "$_cand"/tools/python-*/python.exe; do
        [ -x "$c" ] && { _py="$c"; break 2; }
      done
    done
    # uv: on PATH (unix), beside the tree (<tree>/tools/uv-*/uv, the v0.22.2+
    # official base layout), or the installer-pinned <home>/tools/uv-<ver>-<target>/uv.exe
    # (Windows: install.ps1 Get-PmStoreRoot = $HermesHome/tools, not on PATH).
    _uv_bin="$(find_uv_bin || true)"
    if [ -n "$_py" ] && "$_py" -c 'import sys' >/dev/null 2>&1; then
      pass "agent interpreter present and runs: $_py"
    elif [ -n "$_bin" ] && [ -n "$_uv_bin" ]; then
      # official host layout: no dedicated venv next to the tree — the installer drives its
      # own toolchain through uv, and it is uv that resolves the packages the agent imports
      pass "agent interpreter: official host layout (launcher + uv, no venv beside the tree)"
    else red "no agent interpreter found (venv, tools/python-*, or official host launcher + uv)"; fi
    if [ -n "$_uv_bin" ]; then pass "package manager: uv ($("$_uv_bin" --version 2>/dev/null | head -1))"
    elif [ -n "$_py" ] && "$_py" -m pip --version >/dev/null 2>&1; then pass "package manager: pip (venv)"
    else red "no package manager available for the agent interpreter"; fi ;;
  dsh|pi)
    command -v node >/dev/null 2>&1 && pass "interpreter: node $(node --version 2>/dev/null)" || red "node missing"
    if command -v pnpm >/dev/null 2>&1; then pass "package manager: pnpm $(pnpm --version 2>/dev/null | head -1)"
    elif command -v npm >/dev/null 2>&1; then pass "package manager: npm $(npm --version 2>/dev/null)"
    else red "no node package manager (npm/pnpm)"; fi ;;
  openclaw)
    command -v node >/dev/null 2>&1 && pass "interpreter: node $(node --version 2>/dev/null)" || red "node missing" ;;
esac
if [ -n "$LLM_URL" ] && [ -n "$LLM_MODEL" ]; then
  _llm_args=(verify --platform "$AGENT" --home "$HOME_DIR" --base-url "$LLM_URL" --model "$LLM_MODEL")
  for _e in ${EXTRA_HOMES[@]+"${EXTRA_HOMES[@]}"}; do [ -n "$_e" ] && _llm_args+=(--extra-home "$_e"); done
  python3 "$CTX/lib/llm-config.py" "${_llm_args[@]}" >/tmp/llm-verify.out 2>&1
  case $? in
    0) pass "LLM config in place and re-readable (single-source writer)" ;;
    2) skip "LLM config not judgeable for $AGENT ($(tail -1 /tmp/llm-verify.out 2>/dev/null))" ;;
    *) red "LLM config verify failed ($(tail -1 /tmp/llm-verify.out 2>/dev/null))" ;;
  esac
else
  skip "LLM config not checked (no --llm-url/--llm-model)"
fi

# ── 4. negative assertion: no aimail CLI / pysdk / tssdk pre-installed ──────────────
echo "── [4] negative: nothing of ours is pre-installed"
if command -v aimail >/dev/null 2>&1; then red "aimail CLI already on PATH: $(command -v aimail)"
else pass "no aimail CLI on PATH"; fi
_hits="$(find / -xdev -type f -name 'aimail' -not -path '/proc/*' -not -path '/opt/agent-env/*' 2>/dev/null | head -3)"
[ -z "$_hits" ] && pass "no aimail CLI binary on disk" || red "aimail CLI binary present: $_hits"
_py_hits=""
for c in python3 /opt/hermes/.venv/bin/python3 /app/backend/.venv/bin/python; do
  command -v "$c" >/dev/null 2>&1 || [ -x "$c" ] || continue
  # 判据必须是"真包"(owner 2026-10-11):CI 里 step 的 CWD 是 $GITHUB_WORKSPACE, 而 checkout
  # 落在 $GITHUB_WORKSPACE/aimail(无 __init__.py)⇒ Python 3 把它当**命名空间包**, 裸
  # `import aimail` 会"成功" ⇒ 本负向断言假红(L3 9/9 格全红即此:hermes 臂与 dsh 臂红在同一
  # 行同一判据, 与"臂间污染"无关)。故:①探测固定在 / 下执行(CWD 影响归零)②要求 find_spec
  # 有 origin(命名空间包的 origin 为 None)。实证矩阵:干净 py + CWD 含 aimail/ ⇒ 旧判据红/新判据绿;
  # 真装 SDK 时新判据在两种 CWD 下**都仍报红** ⇒ 判据只修错、不放宽。
  ( cd / && "$c" -c "import importlib.util,sys; s=importlib.util.find_spec('aimail'); sys.exit(0 if (s and s.origin) else 1)" ) >/dev/null 2>&1 && _py_hits="$_py_hits $c"
done
[ -z "$_py_hits" ] && pass "no interpreter can import the pysdk (aimail)" || red "pysdk importable by:$_py_hits"
_pip_hits="$( { python3 -m pip list 2>/dev/null; /opt/hermes/.venv/bin/python3 -m pip list 2>/dev/null; /app/backend/.venv/bin/python -m pip list 2>/dev/null; } | grep -i '^aimail' | head -3)"
[ -z "$_pip_hits" ] && pass "no aimailsdk distribution installed" || red "aimailsdk installed: $_pip_hits"
_ts_hits="$( { npm ls -g --depth=0 2>/dev/null; } | grep -i aimail | head -3)"
[ -z "$_ts_hits" ] && pass "no @aimail/* npm package installed globally" || red "tssdk present: $_ts_hits"

# ── 5. release-age gates are OFF (structural + functional) ──────────────────────────
echo "── [5] release-age gates removed"
_uv_trees=""
case "$AGENT" in
  hermes)   for t in /opt/hermes ${EXTRA_HOMES[@]+"${EXTRA_HOMES[@]}"} "${HERMES_HOME:-$HOME/.hermes}"; do
              [ -d "$t/hermes-agent" ] && t="$t/hermes-agent"
              [ -f "$t/pyproject.toml" ] && _uv_trees="$_uv_trees $t"
            done ;;
  deerflow) for t in /app /app/backend; do [ -f "$t/pyproject.toml" ] && _uv_trees="$_uv_trees $t"; done ;;
esac
for t in $_uv_trees; do
  if grep -qsE '^[[:space:]]*exclude-newer[[:space:]]*=[[:space:]]*(false|"2(1|2)[0-9][0-9])' "$t/uv.toml" 2>/dev/null; then
    pass "uv gate disabled in tree: $t"
  else
    red "uv gate still active (no far-future/disabled exclude-newer) in tree: $t"
  fi
done
_uv_gate="$(find_uv_bin || true)"
if [ -n "$_uv_trees" ] && [ -n "$_uv_gate" ]; then
  # A uv that cannot PARSE the override (e.g. the old base's 0.11.6 failed with
  # "data did not match any variant of untagged enum Helper") is a red even offline:
  # the environment then still filters, silently. Network errors are a SKIP, not a red.
  _t1="$(printf '%s' "$_uv_trees" | awk '{print $1}')"
  _target_py="${_py:-$(command -v python3)}"
  _dry="$(cd "$_t1" && "$_uv_gate" pip install --dry-run --python "$_target_py" aimailsdk 2>&1)"
  case "$_dry" in
    *"did not match any variant"*|*"Failed to parse"*|*"failed to parse"*)
      red "uv cannot express the release-age override (unsupported uv in the image): $(printf '%s' "$_dry" | tail -1)" ;;
    *)
      if [ "$OFFLINE" = 1 ]; then skip "uv functional check needs network (offline)"
      else
        _latest="$(python3 -c "import json,urllib.request;print(json.load(urllib.request.urlopen('https://pypi.org/pypi/aimailsdk/json',timeout=20))['info']['version'])" 2>/dev/null)"
        if [ -z "$_latest" ]; then skip "registry latest unreachable — cannot judge the gate functionally"
        else
          _resolved="$(printf '%s' "$_dry" | grep -oE 'aimailsdk==[0-9][0-9.]*' | head -1)"
          [ "$_resolved" = "aimailsdk==$_latest" ] \
            && pass "uv resolves the registry latest from inside the tree ($_resolved)" \
            || red "uv resolves $_resolved but registry latest is aimailsdk==$_latest (gate still filtering)"
        fi
      fi ;;
  esac
fi
if command -v pnpm >/dev/null 2>&1; then
  _v="$(pnpm config get minimumReleaseAge 2>/dev/null)"
  case "$_v" in
    0|false|undefined) pass "pnpm minimumReleaseAge disabled (value: ${_v:-<unset>})" ;;
    *) red "pnpm release-age gate active: minimumReleaseAge=$_v" ;;
  esac
fi
if command -v npm >/dev/null 2>&1; then
  _n="$(npm config get minimumReleaseAge 2>/dev/null)"
  case "$_n" in
    0|false|undefined) pass "npm minimumReleaseAge disabled (value: ${_n:-<unset>})" ;;
    *) red "npm release-age gate active: minimumReleaseAge=$_n" ;;
  esac
fi

# ── 3. agent runs and its LLM answers ──────────────────────────────────────────────
echo "── [3] agent runs + LLM answers"
case "$AGENT" in
  hermes)
    _probe=""
    _b="$(agent_launcher hermes "$HOME_DIR" || true)"   # 单真源(同上)
    [ -n "$_b" ] && _probe="$("$_b" --version 2>/dev/null)" || _probe="" ;;
  dsh)      _probe="$(dsh --version 2>/dev/null)" ;;
  pi)       _probe="$(pi --version 2>/dev/null)" ;;
  openclaw) _probe="$(openclaw --version 2>/dev/null)" ;;
  deerflow) _probe="$(cd /app/backend && .venv/bin/python -c 'import app; print("app import ok")' 2>/dev/null)" ;;
esac
[ -n "$_probe" ] && pass "agent runs: $(printf '%s' "$_probe" | head -1)" || red "agent did not run (version/probe empty)"
if [ -z "$LLM_URL" ] || [ -z "$LLM_MODEL" ]; then
  skip "live LLM probe not attempted (no --llm-url/--llm-model)"
elif [ "$OFFLINE" = 1 ]; then
  skip "live LLM probe not attempted (offline mode)"
else
  _body="$(curl -sS -m 60 -o /tmp/llm-probe.out -w '%{http_code}' -X POST "$LLM_URL/chat/completions" \
      -H 'content-type: application/json' \
      ${LLM_KEY:+-H "authorization: Bearer $LLM_KEY"} \
      -d "{\"model\":\"$LLM_MODEL\",\"max_tokens\":8,\"messages\":[{\"role\":\"user\",\"content\":\"reply with the word ready\"}]}" 2>/dev/null)"
  if [ "$_body" = "200" ] && grep -q '"content"' /tmp/llm-probe.out 2>/dev/null; then
    pass "LLM answered (HTTP 200 with content)"
  else
    skip "LLM endpoint unreachable/undecidable (http=${_body:-none}) — not counted as pass"
  fi
fi

# ── 6. pinned versions (the testing start point is reproducible) ─────────────────────
echo "── [6] pinned versions"
if [ ! -f "$CTX/pins.env" ]; then
  red "pins.env missing — cannot judge the pinned versions"
else
  . "$CTX/pins.env"
  _got_ver=""; _want_ver=""
  case "$AGENT" in
    hermes)   _want_ver="${HERMES_IMAGE_VERSION:-}"; [ -d /opt/hermes ] || _want_ver="${HERMES_HOST_VERSION:-}"
              _got_ver="$(printf '%s' "$_probe" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)" ;;
    dsh)      _want_ver="${DSH_VERSION:-}";      _got_ver="$(printf '%s' "$_probe" | tr -d 'v')" ;;
    pi)       _want_ver="${PI_VERSION:-}";       _got_ver="$(printf '%s' "$_probe" | tr -d 'v')" ;;
    openclaw) _want_ver="${OPENCLAW_VERSION:-}"; _got_ver="$(printf '%s' "$_probe" | grep -oE '[0-9]{4}\.[0-9]+(\.[0-9]+)?' | head -1)" ;;
    deerflow) _want_ver="" ;;   # built from source locally, pinned by its own <commit> tag
  esac
  if [ -z "$_want_ver" ]; then skip "no version pin recorded for $AGENT"
  elif [ "$_got_ver" = "$_want_ver" ]; then pass "pinned version installed: $AGENT $_got_ver"
  else red "version drift: $AGENT installed='${_got_ver:-none}' pinned=$_want_ver (bump deliberately with update-pins.sh)"; fi
  # pnpm is pinned only where WE install it (dsh). Elsewhere pnpm comes with the base
  # image, and that is already pinned by the base digest — asserting its version here would
  # red on a base-provided pnpm we do not control.
  if [ "$AGENT" = dsh ] && command -v pnpm >/dev/null 2>&1 && [ -n "${PNPM_VERSION:-}" ]; then
    _pv="$(pnpm --version 2>/dev/null | tr -d 'v')"
    [ "$_pv" = "$PNPM_VERSION" ] && pass "pinned pnpm: $_pv" \
      || red "pnpm version drift: installed='${_pv:-none}' pinned=$PNPM_VERSION (bump deliberately with update-pins.sh)"
  fi
fi

echo "── verify summary: $PASS passed, $FAIL failed, $SKIP skipped"
if [ "$FAIL" -gt 0 ]; then echo "AGENT-ENV VERIFY: FAIL"; exit 1; fi
[ "$PASS" -eq 0 ] && { echo "AGENT-ENV VERIFY: CANNOT JUDGE"; exit 2; }
echo "AGENT-ENV VERIFY: PASS"; exit 0

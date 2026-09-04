#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# update-host-venv.sh — update the Hermes-host runtime (venv aimail +
# gateway patches) from the local aimail repo.
#
# WHEN: run OUTSIDE the running Hermes gateway (after exiting a Hermes
# session) — step 5 restarts the gateway, which would kill this script
# if executed from inside it.
#
# What it does:
#   1. force-(re)install the pysdk `aimail` package into the host venv
#      (local repo, no registry)
#   2. SDK uninstall → revert any leftover patch files (git checkout of the
#      5 known patch targets) → SDK install (fresh patch text + register)
#   3. restart the Hermes gateway
#   4. verify: aimail check + welcome --no-wait
#
# Usage:
#   scripts/update-host-venv.sh            # full run incl. restart+verify
#   scripts/update-host-venv.sh --no-restart --skip-verify   # patch only
# Overrides: AIMAIL_REPO, HERMES_HOME, HERMES_VENV
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${AIMAIL_REPO:-$ROOT}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
VENV="${HERMES_VENV:-$HERMES_HOME/hermes-agent/venv}"
HA="$HERMES_HOME/hermes-agent"
PY="$VENV/bin/python"

NO_RESTART=0
SKIP_VERIFY=0
for a in "$@"; do
  case "$a" in
    --no-restart) NO_RESTART=1 ;;
    --skip-verify) SKIP_VERIFY=1 ;;
    -h|--help) grep '^#' "$0" | head -30 | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $a"; exit 2 ;;
  esac
done

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }

# ── 0. preflight ─────────────────────────────────────────────────────────
say "preflight"
[ -f "$REPO/pyproject.toml" ]      || { echo "repo pyproject missing: $REPO"; exit 1; }
[ -x "$PY" ]                        || { echo "venv python missing: $PY"; exit 1; }
[ -d "$HA/.git" ]                   || { warn "hermes-agent has no .git — exact-text unpatch path will run"; }
[ -f "$HA/gateway/platforms/webhook.py" ] || { echo "webhook.py missing: $HA/gateway/platforms/webhook.py"; exit 1; }
echo "  repo    = $REPO"
echo "  venv    = $VENV"
echo "  hermes  = $HERMES_HOME"

# ── 1. (re)install pysdk into host venv ─────────────────────────────────
say "1/6 pip install . — aimailsdk (local repo, force)"
(
  cd "$REPO"
  "$VENV/bin/pip" install --force-reinstall --no-deps . \
    || "$VENV/bin/pip" install --force-reinstall --no-deps --no-build-isolation . \
    || { warn "pip install failed — try: cd $REPO && python3 -m pip wheel . -w /tmp/w && $VENV/bin/pip install --force-reinstall /tmp/w/aimail-*.whl"; exit 1; }
)
"$PY" -c "import aimail; import aimail_base; print('  aimail_base ->', aimail_base.__file__)" \
  || warn "import check failed — aimail.install may still work; continue"

# ── 2. clean patch refresh (uninstall → revert → install) ───────────────
say "2/6 uninstall old patches (SDK, git-revert path)"
"$PY" -m aimail.install uninstall --type hermes --home "$HERMES_HOME" || {
  warn "uninstall returned non-zero — continue with explicit revert"
}

say "3/6 revert known patch targets (idempotent)"
if [ -d "$HA/.git" ]; then
  git -C "$HA" checkout -- gateway/platforms/webhook.py 2>/dev/null || true
  git -C "$HA" checkout -- toolsets.py 2>/dev/null || true
  git -C "$HA" checkout -- hermes_cli/profiles.py 2>/dev/null || true
  git -C "$HA" checkout -- cli/profiles.py 2>/dev/null || true
  leftover="$(git -C "$HA" status --short | grep -E '^ M|^M' | grep -vE 'webhook\.py|profiles\.py|toolsets\.py' || true)"
  if [ -n "$leftover" ]; then
    warn "hermes-agent has unrelated modified files (NOT reverted):"
    printf '%s\n' "$leftover" | head -10
  else
    echo "  working tree clean (patch targets reverted)"
  fi
fi

say "4/6 install fresh patches (SDK)"
"$PY" -m aimail.install install --type hermes --home "$HERMES_HOME" || {
  warn "install failed — gateway patch NOT applied; do NOT restart into a broken state"
  exit 1
}

# ── 3. restart gateway ──────────────────────────────────────────────────
if [ "$NO_RESTART" -eq 1 ]; then
  say "5/6 (skipped) restart — run later: hermes gateway restart"
else
  say "5/6 restart hermes gateway (new patches + package take effect)"
  hermes gateway restart || { warn "hermes gateway restart failed — run manually: hermes gateway restart"; exit 1; }
  echo "  gateway restarted; give it ~10s to come up"
  sleep 10
fi

# ── 4. verify ────────────────────────────────────────────────────────────
if [ "$SKIP_VERIFY" -eq 1 ]; then
  echo "verify skipped"
else
  say "6/6 verify"
  SID="${AIMAIL_SYSTEM_ID:-}"
  if [ -z "$SID" ]; then
    # 优先 Hermes 平台指针(本脚本刷的是 hermes 宿主),其次其他平台指针,
    # 兜底第一个含 agentmail_gateway.json 的系统目录。⚠ 不要 ls|head -1:
    # systems/ 下可能有 default 这类无网关配置的 board 资源目录(曾误选,
    # 导致 check/welcome 全 ✗ + welcome 无法发送)。
    for ptr in "$HERMES_HOME/.agentmail" "$HERMES_HOME"/profiles/*/.agentmail \
               "$HOME/.openclaw/.agentmail" "$HOME/.pi/.agentmail"; do
      [ -f "$ptr" ] || continue
      SID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("system_id",""))' "$ptr" 2>/dev/null)"
      [ -n "$SID" ] && break
    done
  fi
  if [ -z "$SID" ]; then
    for d in "$HOME"/.aimail/systems/*/; do
      [ -d "$d" ] || continue
      [ -f "${d}agentmail_gateway.json" ] || continue
      SID="$(basename "$d")"
      break
    done
  fi
  if [ -n "$SID" ]; then
    # check 显式 --home:本机若同时存在 openclaw/pi 等平台,check 自动探测
    # 会优先它们;这里验证的是 hermes 宿主更新,必须指到 hermes 平台
    python3 "$REPO/cli/aimail" check --home "$HERMES_HOME" --system-id "$SID" || warn "aimail check reported issues"
    python3 "$REPO/cli/aimail" welcome --system-id "$SID" --no-wait || warn "welcome send failed"
  else
    warn "no registered system found (no .agentmail pointer / no agentmail_gateway.json under ~/.aimail/systems) — run: aimail check"
  fi
fi

say "done"
echo "  next: open a new Hermes session — the host now runs d6d035d code + fresh patches"

#!/usr/bin/env bash
# configure_hermes.sh — Step 6: Configure Hermes for amail integration
# Called by integrate.sh (PATCH_STEP_PARENT=1 suppresses inner step_begins)
#
# Sub-steps:
#   1. Patch webhook.py — PREPROCESS_REGISTRY + ping-pong intercept
#   2. Patch profiles.py — trigger_profile_hooks
#   3. Register existing profiles as amail addresses
#   4. Ensure Hermes gateway is running with webhook support

# When sourced from integrate.sh, $0 is the parent script; BASH_SOURCE[0]
# is always this file (scripts/hermes/configure.sh) → repo root is ../.. .
# Callers below append /scripts/... so SCRIPT_DIR must be the repo root.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# ── 0. Stop any running gateways BEFORE patching ─────────────────
echo "  Stopping any running Hermes gateways..."
# 实际命令行是 "venv/bin/python -m hermes_cli.main --profile X gateway run",
# 旧模式 "hermes.*gateway.*run.*accept-hooks" 永不匹配(无 hermes 字样/无
# accept-hooks)→ 旧进程永不被杀,重启变双开。[.] 括号技巧避免 pkill 自匹配。
pkill -f "hermes_cli[.]main.*gateway run" 2>/dev/null || true
sleep 2

# ── 1. Patch webhook ────────────────────────────────────────────
source "$SCRIPT_DIR/scripts/hermes/patch-webhook.sh"

# ── 2. Patch profiles ───────────────────────────────────────────
source "$SCRIPT_DIR/scripts/hermes/patch-profiles.sh"

# ── 3. Register existing profiles ───────────────────────────────
REG_OUTPUT=$(python3 "$SCRIPT_DIR/scripts/hermes/register_profiles.py")
REG_COUNT=0
while IFS= read -r line; do
    case "$line" in
        registered:*) REG_COUNT="${line#registered:}" ;;
        failed:*)     echo "  ⚠ ${line#failed:}" ;;
        no_config)    echo "  No gateway config — skip" ;;
    esac
done <<< "$REG_OUTPUT"
if [ "${REG_COUNT:-0}" -gt 0 ]; then
    info "Registered amail addresses for ${REG_COUNT} profile(s)"
else
    info "All profiles already registered"
fi

# ── 4. Ensure gateway running ──────────────────────────────────
source "$SCRIPT_DIR/scripts/hermes/gateway.sh"

#!/usr/bin/env bash
# Release gate L0 — full test sweep before ANY publish (local + CI).
# Run from repo root (or anywhere; script locates the root itself).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "═══ [L0] python lint + unit tests ═══"
# 平台边界 gate:CLI 代码不得出现平台字面分支(新增平台/多 agent 注册只改
# cli/platforms.json + SDK,CLI 零改动)。白名单 = 空(cmd_reset 特例已随
# register_all 表化删除)——出现任何平台字面即红。
_LIT=$(grep -nE 'platform == "(hermes|openclaw|deerflow|dsh|pi)"' cli/aimail cli/check_status.py cli/repair.py 2>/dev/null || true)
if [ -n "$_LIT" ]; then
  echo "[L0] FAIL: platform literals leaked into CLI code (registry is the single platform source):"
  echo "$_LIT"; exit 1
fi
echo "[L0] platform-boundary: CLI clean of platform literals (registry-driven)"
# Core runtime modules: strict (no unused/undefined). Deploy-time patch
# scripts (hermes/patch_* etc.) intentionally import `aimail` for
# side-effect/eval use — syntax-check only those.
python3 -m pyflakes pysdk/aimail_base.py pysdk/aimail_board.py pysdk/aimail_tools.py \
  pysdk/amail_mcp_server.py pysdk/hermes/aimail_hermes.py pysdk/deer-flow/*.py 2>/dev/null \
  || { echo "[L0] FAIL: pyflakes (core)"; exit 1; }
for f in $(find pysdk -name '*.py' -not -path '*__pycache__*'); do
  python3 -m py_compile "$f" || { echo "[L0] FAIL: py_compile $f"; exit 1; }
done
python3 -m pytest tests/ -q 2>&1 | tail -2

echo "═══ [L0] tssdk: tsc (5 packages) + vitest ═══"
cd tssdk
pnpm exec tsc -b packages/mail-core packages/mail packages/dsh-aimail
pnpm exec tsc -p packages/openclaw-aimail/tsconfig.json
pnpm exec tsc -p packages/pi-aimail/tsconfig.json
pnpm test 2>&1 | tail -2

echo "═══ [L0] PASS — all gates green ═══"

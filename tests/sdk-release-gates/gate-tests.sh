#!/usr/bin/env bash
# Release gate L0 — full test sweep before ANY publish (local + CI).
# Run from repo root (or anywhere; script locates the root itself).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "═══ [L0] python lint + unit tests ═══"
# SDK 资源单一真源(2026-09-25): 真源=仓根 resources/, 4 处分发点是物化产物(已 gitignore)。
# clean clone 里产物不存在 ⇒ 先物化, 否则 pysdk/runtime_bundle 的资源释放与 S9 探针会红。
bash scripts/materialize-resources.sh
bash scripts/materialize-resources.sh --verify
# 平台边界 gate:CLI 代码不得出现平台字面分支(新增平台/多 agent 注册只改
# cli/platforms.json + SDK,CLI 零改动)。白名单 = 空(cmd_reset 特例已随
# register_all 表化删除)——出现任何平台字面即红。
_LIT=$(grep -nE '(platform|agent_type|kind|tgt) == "(hermes|openclaw|deerflow|dsh|pi)"' cli/aimail cli/check_status.py cli/repair.py 2>/dev/null || true)
if [ -n "$_LIT" ]; then
  echo "[L0] FAIL: platform literals leaked into CLI code (registry is the single platform source):"
  echo "$_LIT"; exit 1
fi
echo "[L0] platform-boundary: CLI clean of platform literals (registry-driven)"
# Core runtime modules: strict (no unused/undefined). Deploy-time patch
# scripts (hermes/patch_* etc.) intentionally import `aimail` for
# side-effect/eval use — syntax-check only those.
python3 -m pyflakes pysdk/aimail_base.py pysdk/aimail_board.py pysdk/aimail_tools.py \
  pysdk/aimail_mcp_server.py pysdk/hermes/aimail_hermes.py pysdk/deer-flow/*.py 2>/dev/null \
  || { echo "[L0] FAIL: pyflakes (core)"; exit 1; }
for f in $(find pysdk -name '*.py' -not -path '*__pycache__*'); do
  python3 -m py_compile "$f" || { echo "[L0] FAIL: py_compile $f"; exit 1; }
done
python3 -m pytest tests/ -q 2>&1 | tail -2

echo "═══ [L0] tssdk: build (pnpm build) + orphan guard + vitest ═══"
cd tssdk
# 单一入口: 根 package.json 的 build 脚本封装 5 包 tsc(避免三处清单各自维护 —— 审计 P2)
pnpm build
# 孤儿产物守卫(审计 2026-09-21): lib/dist 里的 *.js 必须有对应 src/*.ts。
# 反例: mail-core 的 install.ts 已删, 但陈旧 lib/install.js 仍被 package.json 的
# files glob 打进发布包(14.5KB 死代码 + 与 src 不符的 .d.ts)。
orphans=0
for pkg in packages/*/; do
  for d in lib dist; do
    [ -d "$pkg$d" ] || continue
    for f in "$pkg$d"/*.js; do
      [ -f "$f" ] || continue
      base=$(basename "$f" .js)
      find "$pkg/src" -name "$base.ts" -print -quit | grep -q . \
        || { echo "  ✗ orphan artifact (no src/$base.ts): $f"; orphans=$((orphans+1)); }
    done
  done
done
[ "$orphans" -eq 0 ] || { echo "[L0] FAIL: $orphans orphan artifact(s) in lib/dist"; exit 1; }
echo "[L0] orphan-artifact guard: 所有 lib/dist 产物均有对应 src"
pnpm test 2>&1 | tail -2

echo "═══ [L0] PASS — all gates green ═══"

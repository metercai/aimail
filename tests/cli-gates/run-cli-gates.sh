#!/usr/bin/env bash
# ▓ CLI 域门禁（owner 2026-10-04 裁决：CLI 与 SDK 的门禁拆开、各归各位）
#
# 本脚本只含**主体是 CLI** 的检查：
#   1) 共享边界块（契约单一真源棘轮 / zero-bridge / 文件归属 / docs↔impl）—— 边界两侧共用，故按共享件调用
#   2) platform-boundary：CLI 代码不含平台字面量（注册表才是平台单一来源）—— 原误放在 SDK 门禁里，已迁回本域
#   3) rust 电池：fmt --check · clippy -D warnings · test --all-targets（CLI 二进制自身的四层判据）
#   4) CLI 上线 L2 门禁：aimail-advanced/tests/cli/run-cli-gate.sh（黑盒：rust 二进制为被测物）
#
# 不进本脚本的（属 SDK 域，见 tests/sdk-release-gates/gate-tests.sh）：
#   materialize-resources · pysdk pyflakes/py_compile · wheel/版本/发版文档 · tssdk build/vitest。
# 产品面 pytest（tests/）目前仍是**共享**步骤（72 个用例文件 CLI/SDK 混排，按文件分流需一次审计，
# 已在两边都跑；分流后各归各位）——不猜、不静默漏。
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 2
fail=0

echo "═══ [CLI] 1) 共享边界块 ═══"
bash tests/gates/shared-boundary-checks.sh || fail=1

echo
echo "═══ [CLI] 2) platform-boundary: CLI 代码零平台字面量（注册表驱动）═══"
_LIT=$(grep -nE '(platform|agent_type|kind|tgt) == "(hermes|openclaw|deerflow|dsh|pi)"' \
  cli/aimail cli/check_status.py cli/repair.py 2>/dev/null || true)
# rust 侧的等价判据在 cli/tests（注册表不变量测试）与 cli/src/core/platforms.rs 内，随第 3 步跑。
if [ -n "$_LIT" ]; then
  echo "[CLI] FAIL: platform literals leaked into CLI code (registry is the single platform source):"
  echo "$_LIT"
  fail=1
else
  echo "[CLI] platform-boundary: CLI clean of platform literals (registry-driven)"
fi

echo
echo "═══ [CLI] 3) rust 电池（顺序不可换：cli 测试比对的是已构建产物）═══"
if [ -d cli ]; then
  (cd cli && cargo fmt --all --check \
        && cargo clippy --offline --all-targets -- -D warnings \
        && cargo build --offline \
        && cargo test --offline --all-targets) || fail=1
else
  echo "[CLI] SKIP: 无 cli/ 目录"
fi

echo
echo "═══ [CLI] 4) CLI 上线 L2 门禁（黑盒，被测物 = rust 二进制）═══"
if [ -x "$HOME/aimail-advanced/tests/cli/run-cli-gate.sh" ]; then
  (cd "$HOME/aimail-advanced" && bash tests/cli/run-cli-gate.sh) || fail=1
else
  echo "[CLI] SKIP: 未找到 aimail-advanced/tests/cli/run-cli-gate.sh"
fi

echo
if [ "$fail" = 0 ]; then echo "═══ [CLI] PASS — all CLI gates green ═══"; else echo "═══ [CLI] FAIL ═══"; fi
exit "$fail"

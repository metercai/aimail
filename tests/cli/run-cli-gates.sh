#!/usr/bin/env bash
# ▓ CLI 域门禁（owner 2026-10-04 裁决：CLI 与 SDK 的门禁拆开、各归各位）
#
# 本脚本只含**主体是 CLI** 的检查：
#   1) 共享边界块（契约单一真源棘轮 / zero-bridge / 文件归属 / docs↔impl）—— 边界两侧共用，故按共享件调用
#   2) rust 电池：fmt --check · clippy -D warnings · test --all-targets（CLI 二进制自身）
[ -n "${L2_JOURNEY:-}" ] && export CLI_JOURNEY=1
#   3) CLI 宿主 L1/L2 门禁：aimail-advanced/tests/cli/（黑盒：rust 二进制为被测物）
#
# 平台字面量判据不在本脚本：Rust 侧由 cli/src/core/platforms.rs 注册表不变量单测
# + adapters 单测守住（随第 2 步 cargo test 跑），2026-10-08 删除对已退役 Python 文件的死 grep。
#
# 不进本脚本的（属 SDK 域，见 tests/sdk/l0-gate-tests.sh）：
#   materialize-resources · pysdk pyflakes/py_compile · 产品面 pytest(tests/sdk/) · tssdk build/vitest。
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 2
fail=0

echo "═══ [CLI] 1) 共享边界块 ═══"
bash tests/shared/shared-boundary-checks.sh || fail=1

echo
echo "═══ [CLI] 2) rust 电池（顺序不可换：cli 测试比对的是已构建产物）═══"
if [ -d cli ]; then
  # rust 测试串行跑（--test-threads=1）：cli 单测里有 29 处 std::env::set_var（进程级全局）⇒
  # 并行时互相踩（实测 bridge_pids / drain_stuck 交替红）。TODO: 改为 per-test 环境隔离后去掉本开关。
  (cd cli && cargo fmt --all --check \
        && cargo clippy --offline --all-targets -- -D warnings \
        && cargo build --offline \
        && cargo test --offline --all-targets -- --test-threads=1) || fail=1
else
  echo "[CLI] SKIP: 无 cli/ 目录"
fi

echo
echo "═══ [CLI] 3) CLI 宿主 L1/L2 门禁（黑盒，被测物 = rust 二进制）═══"
if [ -x "$HOME/aimail-advanced/tests/cli/run-cli-gate.sh" ]; then
  (cd "$HOME/aimail-advanced" && bash tests/cli/run-cli-gate.sh) || fail=1
else
  echo "[CLI] SKIP: 未找到 aimail-advanced/tests/cli/run-cli-gate.sh"
fi

echo
if [ "$fail" = 0 ]; then echo "═══ [CLI] PASS — all CLI gates green ═══"; else echo "═══ [CLI] FAIL ═══"; fi
exit "$fail"

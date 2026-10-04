#!/usr/bin/env bash
# 共享边界门禁（CLI 与 SDK **共用**的一套）：判据主体是"边界/契约"本身，两侧都必须满足。
# 2026-10-04 owner 裁决：CLI 与 SDK 的门禁拆开、各归各位；这四个**边界**检查器不是 SDK 专有，
# 单列在此，由 tests/cli-gates/run-cli-gates.sh 与 tests/sdk-release-gates/gate-tests.sh 各自调用。
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
fail=0

echo "[shared] contract single-source: consts == manifest; frontmatter 5/5; literal ratchet"
python3 tests/contract/check-contract-single-source.py || fail=1

echo "[shared] zero-bridge: SDK/引用侧零桥符号"
python3 tests/contract/check-zero-bridge.py . || fail=1

echo "[shared] file-ownership: CLI ↛ 绑定文件 / SDK ↛ 系统 env (baseline 空)"
python3 tests/contract/check-file-ownership.py . || fail=1

echo "[shared] docs↔impl: symbols defined; en/zh heading structure equal; contract values == manifest"
python3 tests/contract/check-docs-consistency.py || fail=1

if [ "$fail" = 0 ]; then echo "[shared] PASS"; else echo "[shared] FAIL"; fi
exit "$fail"

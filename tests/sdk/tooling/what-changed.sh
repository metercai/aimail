#!/usr/bin/env bash
# 各 SDK 包"自上次各自 tag 以来是否有内容变化"判定（owner 2026-10-07 方针）
# 输出逐包 changed/unchanged；核心包（mail-core|mail）任一变动 ⇒ 五包齐升（R2'）
set -u
cd "$(dirname "$0")/../../.." || exit 1
core_changed=0
for p in mail-core mail dsh-aimail openclaw-aimail pi-aimail; do
  t=$(git tag -l "ts-$p-v*" | sort -V | tail -1)
  if [ -z "$t" ]; then echo "$p changed (no tag)"; c=1
  elif git diff --quiet "$t" -- "tssdk/packages/$p"; then echo "$p unchanged ($t)"; c=0
  else echo "$p changed ($t)"; c=1; fi
  case "$p" in mail-core|mail) [ "$c" = 1 ] && core_changed=1;; esac
done
if [ "$core_changed" = 1 ]; then echo "VERDICT: 核心包有变动 ⇒ 五个包全部升版/进 L2+L3/发版 (R2')"
else echo "VERDICT: 仅变动包各自升版/测试；其余包 SKIP (R3+R5)"; fi

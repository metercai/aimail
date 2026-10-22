#!/usr/bin/env bash
# SDK 包"自上次 tag 以来是否有内容变化"判定（owner 2026-10-07 方针）
# 2026-10-22: 5 包合并为单包 agent-aimail — 五包齐升规则 (R2') 随合并退役:
# 单包只判一次, 有变动 ⇒ 升版/进 L2+L3/发版。
set -u
cd "$(dirname "$0")/.." || exit 1
p=agent-aimail
t=$(git tag -l "ts-$p-v*" | sort -V | tail -1)
if [ -z "$t" ]; then echo "$p changed (no tag)"; c=1
elif git diff --quiet "$t" -- "tssdk/packages/$p"; then echo "$p unchanged ($t)"; c=0
else echo "$p changed ($t)"; c=1; fi
if [ "$c" = 1 ]; then echo "VERDICT: agent-aimail 有变动 ⇒ 升版/进 L2+L3/发版"
else echo "VERDICT: agent-aimail 无变动 ⇒ SKIP 发版"; fi

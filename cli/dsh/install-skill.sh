#!/usr/bin/env bash
# install-skill.sh — 安装 agentmail SKILL 到 dsh 技能目录(逐字拷贝,零改写)
# SKILL.md 源从包资源解析(pip aimail > 仓库 skills/,经 runtime_bundle.py)。
#
# 用法:
#   bash install-skill.sh [DSH_HOME]
#     DSH_HOME   默认 ~/.dsh(全局技能 <DSH_HOME>/skills/agentmail/)
set -euo pipefail

DSH_HOME="${1:-$HOME/.dsh}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_BUNDLE="$SCRIPT_DIR/../runtime_bundle.py"

SRC="$(python3 "$RUNTIME_BUNDLE" resource skills)/SKILL.md"
DST_DIR="$DSH_HOME/skills/agentmail"

[ -f "$SRC" ] || { echo "ERROR: SKILL.md 源未找到(aimail 包未装且仓库 skills/ 缺失): $SRC"; exit 1; }

mkdir -p "$DST_DIR"
if [ -f "$DST_DIR/SKILL.md" ] && cmp -s "$SRC" "$DST_DIR/SKILL.md"; then
    echo "  ✓ SKILL 已就位且一致(跳过拷贝)"
else
    cp "$SRC" "$DST_DIR/SKILL.md"
    echo "  ✓ SKILL 已拷贝 → $DST_DIR/SKILL.md"
fi
echo "  提示:preset 技能目录(<preset>/skills/)或项目级 skills/ 亦可,零改写即可"

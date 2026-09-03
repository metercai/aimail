#!/usr/bin/env bash
# install-skill.sh — 安装 aimail skill 到 OpenClaw 运行时目录
# SKILL.md 是通用邮件处理规范(与 Hermes 共用同一源),从包资源拷贝
# (源解析 pip aimail > 仓库 skills/,经 runtime_bundle.py resource skills)。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_BUNDLE="$SCRIPT_DIR/../runtime_bundle.py"
DST_DIR="${OPENCLAW_SKILLS_DIR:-$HOME/.openclaw/skills}/agentmail"

SRC_SKILL="$(python3 "$RUNTIME_BUNDLE" resource skills)/SKILL.md"
[ -f "$SRC_SKILL" ] || { echo "ERROR: SKILL.md 源未找到(aimail 包未装且仓库 skills/ 缺失)"; exit 1; }

mkdir -p "$DST_DIR"
if [ -f "$DST_DIR/SKILL.md" ] && cmp -s "$SRC_SKILL" "$DST_DIR/SKILL.md"; then
    echo "  ✓ SKILL 已就位且一致(跳过拷贝)"
else
    cp "$SRC_SKILL" "$DST_DIR/SKILL.md"
    echo "  ✓ SKILL 已拷贝 → $DST_DIR/SKILL.md"
fi
echo "verify: openclaw skills list | grep aimail"

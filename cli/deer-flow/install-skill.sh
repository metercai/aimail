#!/usr/bin/env bash
# install-skill.sh — 安装 agentmail skill 到 DeerFlow skills 目录
# SKILL.md 是通用邮件处理规范(与 Hermes/OpenClaw 共用同一源),从包资源拷贝
# (源解析 pip aimail > 仓库 skills/,经 runtime_bundle.py resource skills)。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_BUNDLE="$SCRIPT_DIR/../runtime_bundle.py"
DEER_FLOW_HOME="${DEER_FLOW_HOME:-$HOME/deer-flow}"
DST_DIR="${DEER_FLOW_SKILLS_DIR:-$DEER_FLOW_HOME/skills/public}/agentmail"

SKILLS_SRC="$(python3 "$RUNTIME_BUNDLE" resource skills)"
SRC_SKILL="$SKILLS_SRC/SKILL.md"
if [ ! -f "$SRC_SKILL" ]; then
  echo "SKILL source not found: $SRC_SKILL" >&2
  exit 1
fi
mkdir -p "$DST_DIR"
if [ -f "$DST_DIR/SKILL.md" ] && cmp -s "$SRC_SKILL" "$DST_DIR/SKILL.md"; then
  echo "  ✓ SKILL 已就位且一致(跳过拷贝)"
else
  cp "$SRC_SKILL" "$DST_DIR/SKILL.md"
  echo "  ✓ SKILL 已拷贝 → $DST_DIR/SKILL.md"
fi
cp "$SKILLS_SRC/DESCRIPTION.md" "$DST_DIR/DESCRIPTION.md" 2>/dev/null || true

echo "verify: ls $DST_DIR"

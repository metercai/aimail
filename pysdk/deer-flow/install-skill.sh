#!/usr/bin/env bash
# install-skill.sh — 安装 aimail skill 到 DeerFlow skills 目录
# SKILL.md 是通用邮件处理规范(与 Hermes/OpenClaw 共用同一源),从包资源拷贝
# (源解析 pip aimail > 仓根 resources/skills,经 `aimail install --payload resource skills`)。
# 目录名 = agent 侧契约名 agentmail(不是产品名 aimail):deer-flow 强制
# name == dirname(skills/export.py),SKILL.md frontmatter `name: agentmail`
# ⇒ 目录必须同名, 否则 deer-flow 加载不到该 skill。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# P3(2026-09-20, owner 定调): 改为 CLI 公开命令面(与 install-mcp.sh 同款说明)
command -v aimail >/dev/null 2>&1 || { echo "ERROR: 未找到 aimail 命令, 请先安装 CLI(bootstrap)。" >&2; exit 1; }
DEER_FLOW_HOME="${DEER_FLOW_HOME:-$HOME/deer-flow}"
# deer-flow 的 project_root(): $DEER_FLOW_PROJECT_ROOT 或 cwd(runtime_paths.py:7-17)
DF_PROJECT_ROOT="${DEER_FLOW_PROJECT_ROOT:-$PWD}"

# ── 0. 落点解析(与 install-mcp.sh:49-64 同序 + 一个运维显式覆盖)────────────
# deer-flow 真源(第三方只读参考):
#   config/skills_config.py:41-70 get_skills_path() =
#     ① config.skills.path → ② DEER_FLOW_SKILLS_PATH → ③ project_root()/skills
#       → ④ legacy candidates → 兜底 project_default
#   skills/storage/local_skill_storage.py:30-36 布局 = <skills 根>/public/<name>/SKILL.md
# 本脚本的选取顺序(与上面同序):
#   ① DEER_FLOW_SKILLS_DIR(本仓运维覆盖, 最高)
#   ② DEER_FLOW_SKILLS_PATH(deer-flow 自己最高优先的 env, 设了就必须写它)
#   ③ ${DEER_FLOW_PROJECT_ROOT}/skills/public(运维已断言项目根 ⇒ 在其中落盘)
#   ④ 原默认 ${DEER_FLOW_HOME}/skills/public(既有部署兼容)
# 最终落点与选取理由一律打印(运维核对)。
# 2026-09-30 实测根因: 没传 ③ 时落点是 ④($DEER_FLOW_HOME/skills/public),
#   而容器内会话恒读 project_root()/skills ⇒ skill 文件在盘、会话永远读不到。
if [ -n "${DEER_FLOW_SKILLS_DIR:-}" ]; then
  PUBLIC_DIR="$DEER_FLOW_SKILLS_DIR"
  SKILLS_REASON="DEER_FLOW_SKILLS_DIR (显式覆盖, 最高优先)"
elif [ -n "${DEER_FLOW_SKILLS_PATH:-}" ]; then
  PUBLIC_DIR="$DEER_FLOW_SKILLS_PATH/public"
  SKILLS_REASON="DEER_FLOW_SKILLS_PATH (deer-flow get_skills_path ② env; 设了就必须写它)"
elif [ -n "${DEER_FLOW_PROJECT_ROOT:-}" ]; then
  PUBLIC_DIR="$DF_PROJECT_ROOT/skills/public"
  SKILLS_REASON="DEER_FLOW_PROJECT_ROOT 显式声明 (deer-flow get_skills_path ③ project_root()/skills)"
else
  PUBLIC_DIR="$DEER_FLOW_HOME/skills/public"
  SKILLS_REASON="原默认 DEER_FLOW_HOME/skills/public (既有部署兼容)"
fi
DST_DIR="$PUBLIC_DIR/agentmail"
echo "skills landing: $DST_DIR/SKILL.md"
echo "  selected by: $SKILLS_REASON"

SKILLS_SRC="$(python3 -c 'import aimail, os, sys
d = os.path.dirname(aimail.__file__)
p = os.path.join(d, "resources", "skills")
sys.stdout.write(p if os.path.isdir(p) else "")')"
# owner 2026-10-06（目标态）：skills **取自已装包**（`site-packages/aimail/resources/skills`）✓
# —— 不再经 CLI 的 payload 拷贝 ✗（自包含载荷/仓库态一律取消 ✓）
if [ -z "$SKILLS_SRC" ]; then
  echo "ERROR: 已装包内缺 resources/skills（重装 aimailsdk 或检查包完整性）" >&2
  exit 1
fi
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

#!/usr/bin/env bash
# release-board-resources.sh — 释放 board 资源到系统配置目录(平台无关)。
#
# 把 runtime_bundle 的 board 资源(skills/prompt/soul × en/zh)释放到
#   ~/.agentmail/systems/${SYSTEM_ID}/board/{role_prompt,role_prompt_zh,
#   role_soul,role_soul_zh}
# 运行时(pysdk 与 tssdk 的 processInboundMail 同路径)从这里读取 —— 用户可
# 直接编辑这些文件个性化,SDK 内资源仅作种子。en 为默认生效,zh 供参考。
#
# 用法:
#   release-board-resources.sh <SYSTEM_ID>      # 或 SYSTEM_ID=xxx 环境变量
# 依赖: runtime_bundle.py(本目录同级)解析资源源(pip aimail > 仓库 pysdk/)。
set -euo pipefail

SYSTEM_ID="${1:-${SYSTEM_ID:-}}"
if [ -z "$SYSTEM_ID" ]; then
    echo "ERROR: release-board-resources: SYSTEM_ID 未指定" >&2
    exit 1
fi

RB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/runtime_bundle.py"
PY="${PYTHON:-python3}"
BOARD_BASE="$HOME/.agentmail/systems/$SYSTEM_ID/board"

# 资源名 → 释放子目录(board-role=en 默认,zh/soul 参考保留)
declare -A RES_DIRS=(
    [board-role]=role_prompt
    [board-role-zh]=role_prompt_zh
    [board-soul]=role_soul
    [board-soul-zh]=role_soul_zh
)

for res in "${!RES_DIRS[@]}"; do
    src=$("$PY" "$RB" resource "$res")
    dst="$BOARD_BASE/${RES_DIRS[$res]}"
    mkdir -p "$dst"
    for f in "$src"/*.md; do
        [ -f "$f" ] || continue
        fname="$(basename "$f")"
        if [ ! -f "$dst/$fname" ] || [ "$f" -nt "$dst/$fname" ]; then
            cp "$f" "$dst/$fname"
        fi
    done
done

echo "  board resources: $BOARD_BASE/{role_prompt,role_prompt_zh,role_soul,role_soul_zh}"

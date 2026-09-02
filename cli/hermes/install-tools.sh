#!/usr/bin/env bash
# install-tools.sh — 安装 aimail 运行时到 Hermes(pip 库 + toolsets 注册 + board role + skill)。
#
# 职责:
#   1. pip install aimail(本仓库)→ $HERMES_DIR/venv 的 site-packages
#      (gateway 进程以 venv python 运行;webhook 补丁经
#       `from aimail.hermes import aimail_hermes` 加载适配器,无 pysdk/ 拷贝)
#   2. toolsets.py 注册 _HERMES_CORE_TOOLS tool names(幂等)
#   3. board role 文件 → ~/.agentmail/systems/${SYSTEM_ID}/board/role_prompt
#   4. skill(SKILL.md+DESCRIPTION.md)→ 每个 Hermes profile 的 skills/agentmail
#
# 用法: install-tools.sh   (HERMES_DIR / SYSTEM_ID 经 env,CLI 传入)
set -euo pipefail

HERMES_DIR="${HERMES_DIR:-$HOME/.hermes/hermes-agent}"
SYSTEM_ID="${SYSTEM_ID:-default}"
TOOLSETS_PY="$HERMES_DIR/toolsets.py"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RB="$REPO_DIR/cli/runtime_bundle.py"
PY="${PYTHON:-python3}"

# ── 解释器:gateway 跑在 hermes venv 的 python 上;pip 形态必须装进 venv ──
# (system python3 装了也没用——webhook 进程用 venv python import aimail)
if [ -x "$HERMES_DIR/venv/bin/python" ]; then
    PY="$HERMES_DIR/venv/bin/python"
else
    echo "  [WARN] hermes venv 未找到($HERMES_DIR/venv),跳过 hermes 安装(pip 形态需 venv)"
    exit 0
fi

# ── 1. pip install aimail(运行时载荷 = venv site-packages/aimail)────────
# 从仓库目录构建(hatchling force-include pysdk/ → wheel),单一真源 = 本仓库;
# webhook 补丁 import aimail.hermes.aimail_hermes(pip 形态,与 tools/ 拷贝解耦)。
if ! $PY -m pip install --quiet --upgrade "$REPO_DIR"; then
    echo "  [WARN] pip install aimail 失败,跳过 hermes 安装"
    exit 1
fi
if ! $PY -c "import aimail.hermes.aimail_hermes" >/dev/null 2>&1; then
    echo "  [WARN] aimail.hermes.aimail_hermes 导入验证失败"
    exit 1
fi
echo "  hermes pip: aimail $($PY -c 'import aimail; print(aimail.__version__)') → $HERMES_DIR/venv"

# ── 2. toolsets.py 注册 _HERMES_CORE_TOOLS tool names(幂等)──────────
if [ -f "$TOOLSETS_PY" ]; then
    $PY - "$TOOLSETS_PY" <<'PYEOF'
import re, sys
path = sys.argv[1]
content = open(path, encoding="utf-8").read()
needs_write = False
tool_names = ["send_mail", "manage_contacts", "contact_profile",
              "set_contact_profile", "email_summary", "set_email_summary"]
for name in tool_names:
    if f'"{name}"' not in content:
        content = re.sub(r'(_HERMES_CORE_TOOLS\s*=\s*\[)',
                         r'\1\n    "' + name + '",', content, count=1)
        needs_write = True
if needs_write:
    open(path, "w", encoding="utf-8").write(content)
    print("  hermes toolsets: registered core tool names")
else:
    print("  hermes toolsets: already registered (skip)")
PYEOF
else
    echo "  hermes toolsets: $TOOLSETS_PY 缺失(跳过注册)"
fi

# ── 3. board 资源 → systems/${SYSTEM_ID}/board/ ────────────────────
# 运行时从配置目录读取(用户可个性化)。en 为默认生效;zh 参考保留。
# 映射: runtime_bundle 资源名 → 释放子目录
#   board-role(en) → role_prompt     board-role-zh → role_prompt_zh
#   board-soul(en) → role_soul       board-soul-zh → role_soul_zh
BOARD_BASE="$HOME/.agentmail/systems/$SYSTEM_ID/board"
declare -A ROLE_RES=(
    [board-role]=role_prompt
    [board-role-zh]=role_prompt_zh
    [board-soul]=role_soul
    [board-soul-zh]=role_soul_zh
)
for res in "${!ROLE_RES[@]}"; do
    src=$($PY "$RB" resource "$res")
    dst="$BOARD_BASE/${ROLE_RES[$res]}"
    mkdir -p "$dst"
    for f in "$src"/*.md; do
        [ -f "$f" ] || continue
        fname="$(basename "$f")"
        if [ ! -f "$dst/$fname" ] || [ "$f" -nt "$dst/$fname" ]; then
            cp "$f" "$dst/$fname"
        fi
    done
done
echo "  hermes board resources: $BOARD_BASE/{role_prompt,role_prompt_zh,role_soul,role_soul_zh}"

# ── 4. skill → 每个 Hermes profile 的 skills/agentmail ──────────────
SKILL_SRC=$($PY "$RB" resource skills)
for prof_dir in "$HOME/.hermes/profiles"/*/; do
    [ -d "$prof_dir" ] || continue
    prof_skill_dir="$prof_dir/skills/agentmail"
    for fname in SKILL.md DESCRIPTION.md; do
        [ -f "$SKILL_SRC/$fname" ] || continue
        dst="$prof_skill_dir/$fname"
        if [ ! -f "$dst" ] || ! cmp -s "$SKILL_SRC/$fname" "$dst"; then
            mkdir -p "$prof_skill_dir"
            cp "$SKILL_SRC/$fname" "$dst"
        fi
    done
done
echo "  hermes skill: ~/.hermes/profiles/*/skills/agentmail"

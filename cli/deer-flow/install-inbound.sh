#!/usr/bin/env bash
# install-inbound.sh — DeerFlow agentmail 入站捆绑安装(幂等)
#
# 背景:DeerFlow 入站预处理并入其本地 gateway(8001)进程。宿主 app.py 经
#   `from .routers import aimail_inbound` 加载 router,router 必须留在
#   backend/app/gateway/routers/ 且与共享 core 同目录(bootstrap case-3 自举)。
#
# 本脚本:
#   1. 用 runtime_bundle.py 把 deer-flow 捆绑(core5 + router + 适配层,扁平)
#      铺进宿主 routers/,落版本戳;源解析 pip 包 > 仓库 pysdk/(单一真源)。
#   2. patch backend/app/gateway/app.py:import + include_router(幂等,宿主侧)。
#   3. 校验(语法 + 锚点存在)。
# 安装后需重启 8001 生效。
#
# 用法:
#   bash install-inbound.sh [DEER_FLOW_ROOT]
#     DEER_FLOW_ROOT  默认 ~/deer-flow
set -euo pipefail

DEER_FLOW_ROOT="${1:-$HOME/deer-flow}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_BUNDLE="$SCRIPT_DIR/../runtime_bundle.py"
G_DIR="$DEER_FLOW_ROOT/backend/app/gateway"   # 'gateway' 段变量化避免误匹配
DST_ROUTERS="$G_DIR/routers"
APP_PY="$G_DIR/app.py"

[ -f "$RUNTIME_BUNDLE" ] || { echo "ERROR: runtime_bundle.py not found: $RUNTIME_BUNDLE"; exit 1; }
[ -f "$APP_PY" ] || { echo "ERROR: app.py not found: $APP_PY"; exit 1; }

# ── 1. 安装 deer-flow 捆绑到宿主 routers/(源 pip>repo,版本戳,漂移可检出)──
python3 "$RUNTIME_BUNDLE" install deer-flow --dest "$DST_ROUTERS"

# ── 2. patch app.py(幂等:import + include_router)────────────────────
PY_PATCH=$(cat <<'PY'
import sys

app_py = sys.argv[1]
src = open(app_py, encoding="utf-8").read()
changed = False

# 2a. import 行:挂在 agents 之后(字母序相邻)
import_marker = "    agents,\n"
import_line = "    aimail_inbound,\n"
if import_line not in src:
    if import_marker in src:
        src = src.replace(import_marker, import_marker + import_line, 1)
        changed = True
    else:
        raise SystemExit("ERROR: import anchor 'agents,' not found in routers import block")

# 2b. include_router 行:挂在 agents.router 之后
route_marker = "    app.include_router(agents.router)\n"
route_line = "    app.include_router(aimail_inbound.router)\n"
if route_line not in src:
    if route_marker in src:
        src = src.replace(route_marker, route_marker + route_line, 1)
        changed = True
    else:
        raise SystemExit("ERROR: include_router anchor 'agents.router' not found")

if changed:
    open(app_py, "w", encoding="utf-8").write(src)
    print("  ✓ app.py patched (import + include_router)")
else:
    print("  ✓ app.py 已含 aimail_inbound(跳过)")
PY
)
"$DEER_FLOW_ROOT/backend/.venv/bin/python" -c "$PY_PATCH" "$APP_PY"

# ── 3. 校验 ─────────────────────────────────────────────────────────
"$DEER_FLOW_ROOT/backend/.venv/bin/python" -m py_compile "$DST_ROUTERS/aimail_inbound.py" "$APP_PY"
grep -q "aimail_inbound" "$APP_PY" || { echo "ERROR: app.py 锚点缺失"; exit 1; }
echo "  ✓ 语法校验通过 + 锚点确认"
echo ""
echo "完成。重启 DeerFlow gateway(8001)后生效:"
echo "  kill <uvicorn-pid> && cd $DEER_FLOW_ROOT/backend && DEER_FLOW_AUTH_DISABLED=1 \\"
echo "    PYTHONPATH=. PYTHONIOENCODING=utf-8 .venv/bin/uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001"

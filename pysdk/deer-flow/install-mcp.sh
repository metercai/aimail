#!/usr/bin/env bash
# install-mcp.sh — 写 DeerFlow extensions_config.json 的 aimail MCP server 块
# 复用共享 aimail_mcp_server.py(兜底 MCP 服务,平台无关,零适配),指向
# 自包含载荷 <程序根>/mcp/(经 runtime_bundle.py 安装,源 pip>repo,版本戳;
# 与仓库路径解耦,改名/mv 不影响运行;落点由 runtime_bundle 单点给出)。
# 幂等: 已存在 aimail server 块则更新路径/env,否则追加。
#
# 落点(2026-09-27 修): 必须与 deer-flow 自己的
# `ExtensionsConfig.resolve_config_path()` 同序,否则写进去的文件 deer-flow
# 永远不读 ⇒ 工具一块都不加载(且 install_steps 是 on_error=warn ⇒ 静默)。
# deer-flow 真实解析(第三方只读参考):
#   backend/packages/harness/deerflow/config/extensions_config.py:419+
#     ① 显式 config_path → ② DEER_FLOW_EXTENSIONS_CONFIG_PATH →
#     ③ project_root() 下**已存在**的 extensions_config.json / mcp_config.json
#        (`existing_project_file`,先 extensions_config.json)→ ④ backend/ 与仓根兜底
#   backend/packages/harness/deerflow/config/runtime_paths.py:7-17
#     project_root() = $DEER_FLOW_PROJECT_ROOT 或 cwd
#     runtime_home() = $DEER_FLOW_HOME 或 project_root()/.deer-flow
#     ⇒ DEER_FLOW_HOME 在 deer-flow 侧是**状态目录**,从不是 extensions-config 目录
#       (本仓把它当"deer-flow 检出根"用, 既有部署据此落盘 ⇒ 保留为最后兜底)。
#
# 本脚本的选取顺序(与上面同序 + 一个运维显式覆盖):
#   ① DEER_FLOW_EXT_CFG(本仓运维覆盖,最高)
#   ② DEER_FLOW_EXTENSIONS_CONFIG_PATH(deer-flow 自己最高优先的 env,设了就必须写它)
#   ③ ${DEER_FLOW_PROJECT_ROOT:-$PWD}/extensions_config.json
#        已存在该文件 ⇒ 用(deer-flow 会优先读它,写别处等于没写)
#        DEER_FLOW_PROJECT_ROOT 显式声明 ⇒ 在其中新建(运维已断言项目根)
#        (不无条件用 $PWD: 那是本脚本运行时的 cwd,与 deer-flow 进程的 cwd
#         不一定相同 —— 无条件新建会既不被读、又丢掉既有默认)
#   ④ 原默认 ${DEER_FLOW_HOME}/extensions_config.json(既有部署兼容)
# 最终落点与选取理由一律打印(运维核对),并提示会被 legacy mcp_config.json 抢读的情形。
# env 必须**自足**(2026-10-02 J4e 根因取证):
#   deer-flow 起 stdio server 只带本块 env(第三方只读参考
#   backend/packages/harness/deerflow/mcp/client.py:32-34 `params["env"] = config.env`),
#   子进程拿不到安装侧环境 ⇒ 少写 AIMAIL_HOME 时 aimail_base.aimail_home()
#   (pysdk/aimail_base.py:242-243)回落 ~/.aimail ⇒ 扫不到本机绑定 ⇒ 每次工具调用
#   抛 `agent '<id>' not registered`(aimail_base.py:573-575) ⇒ agent 永远发不出信
#   (deerflow J4e 历史 13 红 0 绿; 反事实实测: 补 AIMAIL_HOME 即解)。
#   AIMAIL_SYSTEM_ID 同理: 钉住安装时的 system —— 多 system 同 agent_id 时才不会被
#   _scan_systems_for_agent 的"按目录序首匹配"挑到别的地址(缺失 ⇒ server 回退全扫描, 同旧行为)。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# P3(2026-09-20, owner 定调): 不再拼仓库相对路径去调 cli/runtime_bundle.py ——
# 一律走 CLI 公开命令面(aimail install --payload …); rust 化后同一命令由新二进制实现, 本脚本无需再改。
command -v aimail >/dev/null 2>&1 || {
    echo "ERROR: 未找到 aimail 命令。请先安装 CLI(bootstrap)后再跑本脚本。" >&2
    echo "       (不再回退到仓库内 cli/runtime_bundle.py —— 那正是要消除的耦合)" >&2
    exit 1
}
DEER_FLOW_HOME="${DEER_FLOW_HOME:-$HOME/deer-flow}"
# deer-flow 的 project_root(): $DEER_FLOW_PROJECT_ROOT 或 cwd(runtime_paths.py)
DF_PROJECT_ROOT="${DEER_FLOW_PROJECT_ROOT:-$PWD}"
LEGACY_CFG="$DEER_FLOW_HOME/extensions_config.json"
AGENT_ID="${AIMAIL_AGENT_ID:-default}"
# = pysdk/aimail_base.aimail_home() 语义(env 优先, 否则 ~/.aimail)—— 安装侧知道的
# home 必须随 env 写进去, 否则 MCP 子进程按它自己的 HOME 猜出另一个根(见文件头)。
AIMAIL_HOME_RESOLVED="${AIMAIL_HOME:-$HOME/.aimail}"
# 装配步(pysdk/install.py::_assembly_env)会 setdefault; 这里容忍空(空 ⇒ 不写键,
# server 侧回退全 systems 扫描 = 旧行为)。
AIMAIL_SYSTEM_ID_RESOLVED="${AIMAIL_SYSTEM_ID:-}"

# ── 0. 落点解析(见文件头;打印最终落点 + 理由)─────────────────────────
if [ -n "${DEER_FLOW_EXT_CFG:-}" ]; then
  CFG="$DEER_FLOW_EXT_CFG"
  CFG_REASON="DEER_FLOW_EXT_CFG (显式覆盖, 最高优先)"
elif [ -n "${DEER_FLOW_EXTENSIONS_CONFIG_PATH:-}" ]; then
  CFG="$DEER_FLOW_EXTENSIONS_CONFIG_PATH"
  CFG_REASON="DEER_FLOW_EXTENSIONS_CONFIG_PATH (deer-flow 自身最高优先 env; 设了就必须写它)"
elif [ -f "$DF_PROJECT_ROOT/extensions_config.json" ]; then
  CFG="$DF_PROJECT_ROOT/extensions_config.json"
  CFG_REASON="project root 已有 extensions_config.json (deer-flow resolve_config_path 会优先读它)"
elif [ -n "${DEER_FLOW_PROJECT_ROOT:-}" ]; then
  CFG="$DF_PROJECT_ROOT/extensions_config.json"
  CFG_REASON="DEER_FLOW_PROJECT_ROOT 显式声明 (在其中新建; deer-flow 只搜 project root)"
else
  CFG="$LEGACY_CFG"
  CFG_REASON="原默认 DEER_FLOW_HOME/extensions_config.json (既有部署兼容)"
fi
CFG="${CFG/#\~/$HOME}"          # 变量里的 ~ 兜底展开(env 传参不经 shell 展开)
echo "extensions config: $CFG"
echo "  selected by: $CFG_REASON"
echo "  deer-flow project root: $DF_PROJECT_ROOT (DEER_FLOW_PROJECT_ROOT 或 cwd)"
echo "  deer-flow state home  : $DEER_FLOW_HOME (DEER_FLOW_HOME; deer-flow 侧=状态目录)"
# 运维核对: 落点不在 deer-flow 的 project root 下、而那里躺着 legacy mcp_config.json 时,
# deer-flow 的 search 会命中那个文件 → 本次写入不生效(必须显式指路,不许静默)。
if [ "$CFG" != "$DF_PROJECT_ROOT/extensions_config.json" ] \
   && [ -f "$DF_PROJECT_ROOT/mcp_config.json" ]; then
  echo "WARNING: $DF_PROJECT_ROOT/mcp_config.json 存在 —— deer-flow 会读它而不是本次落点" >&2
  echo "         设 DEER_FLOW_EXTENSIONS_CONFIG_PATH=$CFG 让 deer-flow 与本次写入指向同一文件" >&2
fi

# ── 1. MCP 服务取自已装包（owner 2026-10-06 目标态：**零拷贝** ✗ 自包含载荷/仓库态一律取消 ✓）──
SERVER="$("${PY:-python3}" -c 'import aimail, os, sys
d = os.path.dirname(aimail.__file__)
p = os.path.join(d, "aimail_mcp_server.py")
sys.stdout.write(p if os.path.isfile(p) else "")')"
# owner 2026-10-06（目标态）：MCP 服务**取自已装包**（`site-packages/aimail/aimail_mcp_server.py`）✓ ——
# 不再使用 `<程序根>/mcp/` 的 SDK 文件拷贝 ✗（自包含载荷/仓库态一律取消 ✓）。
if [ -z "$SERVER" ]; then
  echo "ERROR: 已装包内缺 aimail_mcp_server.py（重装 aimailsdk 或检查包完整性）" >&2
  exit 1
fi
[ -f "$SERVER" ] || { echo "MCP payload missing: $SERVER" >&2; exit 1; }

# 真实版本检测(只报检测结果,不猜测):backend/pyproject.toml 的 version
# 候选顺序: deer-flow 检出根(=DEER_FLOW_HOME, 本仓既有约定) → project root(仅当
# 它看起来是 deer-flow 检出时, 免得在别的仓的 pyproject.toml 上误报身份)。
DF_VERSION="unknown"
_VER_CANDIDATES=("$DEER_FLOW_HOME/backend/pyproject.toml" "$DEER_FLOW_HOME/pyproject.toml")
if [ -d "$DF_PROJECT_ROOT/backend" ]; then
  _VER_CANDIDATES+=("$DF_PROJECT_ROOT/backend/pyproject.toml" "$DF_PROJECT_ROOT/pyproject.toml")
fi
for pp in "${_VER_CANDIDATES[@]}"; do
  if [ -f "$pp" ]; then
    DF_VERSION="$(grep -m1 '^version' "$pp" | sed -E 's/.*=\s*"([^"]+)".*/\1/' || true)"
    if [ -n "$DF_VERSION" ]; then
      break
    fi
    DF_VERSION="unknown"
  fi
done
IDENTITY="deerflow/${DF_VERSION:-unknown}"

# 确保配置文件存在(缺失时以示例为模板;示例先在落点同目录找,再回退 DEER_FLOW_HOME)
if [ ! -f "$CFG" ]; then
  mkdir -p "$(dirname "$CFG")"
  TPL=""
  for t in "$(dirname "$CFG")/extensions_config.example.json" \
           "$DEER_FLOW_HOME/extensions_config.example.json"; do
    if [ -f "$t" ]; then
      TPL="$t"
      break
    fi
  done
  if [ -n "$TPL" ]; then
    cp "$TPL" "$CFG"
    echo "created $CFG from $TPL"
  else
    echo '{"mcpServers": {}}' > "$CFG"
    echo "created empty $CFG"
  fi
fi

python3 - "$CFG" "$SERVER" "$AGENT_ID" "$IDENTITY" \
    "$AIMAIL_HOME_RESOLVED" "$AIMAIL_SYSTEM_ID_RESOLVED" <<'PY'
import json, sys

(cfg_path, server, agent_id, identity,
 aimail_home, system_id) = sys.argv[1:7]
with open(cfg_path) as f:
    data = json.load(f)

servers = data.setdefault("mcpServers", {})
# AIMAIL_SYSTEM_ID = **首次**装配该宿主时的 system = 主身份, set-if-absent:
# 之后为别的 system 重装(例: 旅程 J2 的 `install -c` 共享域买家系统)不许改写 ——
# last-writer 会让 MCP 子进程钉到共享 system, 把欢迎信回信身份带偏到共享地址
# (2026-10-02 j4e4 实测: outbound 落在 agent.j*@shared-e2e.local ⇒ 本轮绑定 0 行 ⇒ J4e 仍红)。
# 钉住的 system 里没有绑定时 server 侧**响亮回退**全扫描(aimail_mcp_server._agent_ctx)。
_prev_env = ((servers.get("aimail") or {}).get("env")) or {}
_sid_out = _prev_env.get("AIMAIL_SYSTEM_ID") or system_id
_sid_note = ("kept previous pin" if _prev_env.get("AIMAIL_SYSTEM_ID")
             else ("pinned at first assembly" if system_id else
                   "absent => server scans all systems"))
servers["aimail"] = {
    "enabled": True,
    "type": "stdio",
    "command": "python3",
    "args": [server],
    # env 名与 server 读取一致(AIMAIL_*)。deer-flow 只把这份 env 交给 stdio
    # 子进程(见文件头), 所以这里必须自带 server 定位绑定所需的全部变量。
    "env": {"AIMAIL_AGENT_ID": agent_id,
            "AIMAIL_AGENT_IDENTITY": identity,
            "AIMAIL_HOME": aimail_home}
           | ({"AIMAIL_SYSTEM_ID": _sid_out} if _sid_out else {}),
    "tool_name_prefix": True,
    "session_init_timeout": 60,
    "tool_call_timeout": 60,
    "description": "AIMail email tools (shared fallback MCP server)",
}

with open(cfg_path, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"wrote mcpServers.aimail → {cfg_path}")
print(f"  server: {server}")
print(f"  AIMAIL_AGENT_ID: {agent_id}")
print(f"  AIMAIL_AGENT_IDENTITY: {identity}")
print(f"  AIMAIL_HOME: {aimail_home}")
print(f"  AIMAIL_SYSTEM_ID: {_sid_out or '(absent => server scans all systems)'} [{_sid_note}]")
PY

echo "landing point: $CFG"
echo "verify: python3 -c \"import json; d=json.load(open('$CFG')); print(d['mcpServers']['aimail']['args'])\""

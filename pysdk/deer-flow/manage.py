#!/usr/bin/env python3
"""manage.py — DeerFlow 平台生命周期/入站安装单一管理模块(pysdk 自足)。

由 cli/deer-flow/{register_agent.py, reconcile.py, deregister_agent.py,
install-inbound.sh} 迁移聚合而来(2026-09-02),去掉对 cli/runtime_core.py 的
依赖:运行时核心/适配层经双形态自举定位(见 _bootstrap_runtime),之后
裸导入即可用 —— import amail_base(适配层)/ import aimail_tools(核心)。
业务逻辑逐字保留(幂等/错误处理/输出行),仅做结构性搬移。

函数:
  register_agents(manager, system_id, agent) -> int    ← register_agent.py main
  reconcile(system_id, manager, dry_run) -> int         ← reconcile.py main
  deregister_agents(agent, manager, system_id) -> int   ← deregister_agent.py main
  patch_backend_app(backend_dir) -> bool                ← install-inbound.sh 内嵌 python(patch 段)
  install_bundle(backend_dir, source_root, force) -> int← install-inbound.sh 前段(runtime_bundle 简化)

子命令分发(if __name__ == '__main__',旧脚本用法兼容):

  python3 manage.py register    [--agent <id>|--all] [--manager M] [--system-id SID]
  python3 manage.py reconcile   [--system-id SID] [--manager M] [--dry-run]
  python3 manage.py deregister  --agent <id> [--manager M] [--system-id SID]
  python3 manage.py patch       [DEER_FLOW_ROOT]          # 仅 app.py patch 段(幂等)
  python3 manage.py install     [DEER_FLOW_ROOT] [--source-root DIR] [--force]
                               # 完整流:捆绑安装 + app.py patch + 语法校验(原 install-inbound.sh)

双形态自举(去 runtime_core):
  形态1 仓库 dev:本文件在 pysdk/deer-flow/ 下,dirname(dirname(__file__)) =
        pysdk/(含 aimail_base.py)即核心目录,直接挂 sys.path。
  形态2 pip:site-packages/aimail/deer-flow/,aimail/ 包目录同样含
        aimail_base.py;`import aimail`(glue,__init__ 把 aimail/ 插 sys.path)
        兜底。本模块自身目录(适配层,含 amail_base.py)一并挂上。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sys
from datetime import datetime, timezone

STAMP_NAME = ".aimail-runtime.json"
MIN_PAYLOAD_VERSION = "0.1.0"

# 捆绑定义:源相对路径(相对核心目录/源根)→ 目标文件名(宿主 routers/aimail/ 下)
_BUNDLE_FILES = [
    ("deer-flow/aimail_inbound.py", "aimail_inbound.py"),
    ("deer-flow/amail_base.py", "amail_base.py"),
    ("aimail_base.py", "aimail_base.py"),
    ("aimail_tools.py", "aimail_tools.py"),
    ("aimail_board.py", "aimail_board.py"),
    ("gateway_api.py", "gateway_api.py"),
    ("_aimail_bootstrap.py", "_aimail_bootstrap.py"),
]


# ── 双形态自举(替代 runtime_core.load_core/load_adapter)────────────────
def _bootstrap_runtime() -> tuple[str, str]:
    """定位 (适配层目录, 核心目录) 并挂 sys.path(幂等)。

    形态1 仓库 pysdk:本文件在 <core>/deer-flow/manage.py,父目录含
        aimail_base.py → 核心即 dirname(dirname(__file__))。
    形态2 pip site-packages/aimail/deer-flow/:aimail/ 包目录同样含
        aimail_base.py(同一公式命中);再以 `import aimail` glue 兜底
        (aimail/__init__ 把包目录插 sys.path)。
    核心缺失 → SystemExit(与原 runtime_core 同风格)。
    """
    _here = os.path.dirname(os.path.abspath(__file__))          # 适配层目录
    _parent = os.path.dirname(_here)
    _core = _parent if os.path.isfile(os.path.join(_parent, "aimail_base.py")) else ""
    if not _core:
        try:
            import aimail  # glue:__init__ 把 aimail/ 目录插 sys.path  # noqa: F401
            _pkg = os.path.dirname(os.path.abspath(aimail.__file__))
            if os.path.isfile(os.path.join(_pkg, "aimail_base.py")):
                _core = _pkg
        except Exception:
            pass
    if not _core:
        raise SystemExit("ERROR: aimail 运行时核心未找到(仓库 pysdk/ 与 pip aimail 均不可用)")
    for _d in (_here, _core):
        if _d not in sys.path:
            sys.path.insert(0, _d)
    return _here, _core


_ADAPTER_DIR, _CORE_DIR = _bootstrap_runtime()

import amail_base as _base          # noqa: E402   (deer-flow 适配层,本目录)
import aimail_base as _core         # noqa: E402   (共享核心,父目录)
import aimail_tools as _tools       # noqa: E402   (共享核心,父目录)
# 注:resolve_register_webhook_url / register_bridge_route 定义在共享核心
# aimail_base(deer-flow 适配层 amail_base 未转发,源 cli 脚本同款调用在其上
# 会 AttributeError)——本模块这两处调用直接走 _core。


# ══════════════════════════════════════════════════════════════════════
# 注册(register_agent.py 逐字移植)
# ══════════════════════════════════════════════════════════════════════
def email_for_agent(agent_id: str, domain: str, system_name: str) -> str:
    """地址派生(公共核心 email_for_agent;DeerFlow 默认名 default → agent)。"""
    return _base.email_for_agent(agent_id, domain, system_name,
                                 default_aliases=("default",))


def discover_deerflow_agents() -> list:
    """列出 DeerFlow agents: 扫描 skills 目录 + SOUL.md 判定(默认 lead agent)。
    简化实现: 返回 ["default"](DeerFlow 默认 lead_agent);后续 reconcile()
    做完整目录对账。"""
    return ["default"]


def register_one(client, system_id: str, agent_id: str, email: str,
                 webhook_url: str, webhook_secret: str, manager_address: str,
                 domain: str, system_name: str, gateway_url: str,
                 local_webhook_url: str) -> dict:
    """注册单个 agent: 核心链走公共 register_agent_email,平台部分只做本地落盘组装。

    webhook_url(注册参数)三态(见 resolve_register_webhook_url);落盘的一律是
    local_webhook_url(本地接收端点,唯一信任源,给 bridge 路由)。
    """
    reg = _base.register_agent_email(
        client, system_id, email, webhook_url, webhook_secret,
        manager_address,
    )
    api_key = reg.get("api_key", "")

    cfg = {
        "email": email,
        "gateway_url": gateway_url,
        "domain": domain,
        "system_id": system_id,
        "system_name": system_name,
        "manager_address": manager_address,
        "api_key": api_key,
        # agentmail.json webhook_url = 本地接收端点(唯一信任源,给 bridge 路由)
        "webhook_url": local_webhook_url,
        "webhook_secret": webhook_secret,
        "assistant_id": os.environ.get("DEERFLOW_ASSISTANT_ID", "lead_agent"),
    }
    return cfg


def save_agent_config(agent_id: str, cfg: dict, system_id: str) -> None:
    """落盘地址键 agentmail.json(共享布局,与 OpenClaw 同约定)。"""
    cfg = dict(cfg)
    cfg["agent_id"] = agent_id
    cleaned = re.sub(r"[^\w.\-]", "_", cfg["email"])
    path = os.path.expanduser(f"~/.aimail/systems/{system_id}/{cleaned}/agentmail.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  ✓ saved {path}")


def register_agents(manager: str = "", system_id: str = "", agent: str = "") -> int:
    """注册 DeerFlow agent(s) 到 amail(register_agent.py main 逐字移植,去 argparse)。

    注册链(register_email → 已存在更新 webhook → manager 白名单 → activate_address)
    走公共核心 amail_base.register_agent_email(所有平台共用)→ 落盘地址键
    agentmail.json(systems/{sid}/{cleaned_addr}/agentmail.json)。

    Args:
        agent:     agent id;传 "all" 等价原 --all(discover_deerflow_agents());
                  空串 → SystemExit "need --agent <id> or --all"。
        manager:   manager_address(审批联系人);缺省读 AIMAIL_MANAGER 环境变量。
        system_id: 缺省读 AIMAIL_SYSTEM_ID 环境变量 / detect_system_id()。

    Returns:
        0(原脚本恒返回 0;计数见输出行 registered: N/M)。

    例:
      register_agents(manager="admin@x.com", agent="default")
      register_agents(manager="admin@x.com", agent="all")
      register_agents(manager="admin@x.com", agent="work", system_id="SID")
    """
    if not agent:
        raise SystemExit("need --agent <id> or --all")

    system_id = system_id or _base.detect_system_id()
    gw = _base.load_gateway_config(system_id)
    if not gw:
        raise SystemExit(f"gateway config not found (agentmail_gateway.json) for {system_id} — run aimail install first")
    manager = manager or os.environ.get("AIMAIL_MANAGER", "")
    if not manager:
        raise SystemExit("need --manager <addr> or AIMAIL_MANAGER env (审批联系人)")

    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))
    agents = discover_deerflow_agents() if agent == "all" else [agent]
    if not agents:
        agents = ["default"]

    # 本地接收端点(进程内预处理,DeerFlow 本地 gateway /aimail/inbound;
    # DEERFLOW_INBOUND_URL 可覆盖);注册参数三态由 resolve_register_webhook_url 决定
    inbound_base = os.environ.get("DEERFLOW_INBOUND_URL", "http://127.0.0.1:8001")
    local_webhook_url = inbound_base.rstrip("/") + "/aimail/inbound"
    reg_url = _core.resolve_register_webhook_url(gw, local_webhook_url)

    created = 0
    for agent_id in agents:
        email = email_for_agent(agent_id, gw["domain"], gw.get("system_name", ""))
        webhook_secret = secrets.token_hex(32)
        cfg = register_one(
            client, system_id, agent_id, email,
            reg_url, webhook_secret, manager,
            gw["domain"], gw.get("system_name", ""), gw["gateway_url"],
            local_webhook_url,
        )
        if cfg["api_key"]:
            save_agent_config(agent_id, cfg, system_id)
            created += 1
            print(f"  ✓ {agent_id} → {email} (api_key ok)")
            # 铁律:有 bridge 时注册后必须向 bridge 注册入站 hook 路由
            _core.register_bridge_route(system_id, email, gw, local_webhook_url)
        else:
            print(f"  ⚠ {agent_id} → {email} registered but no api_key (activation pending)")

    print(f"registered: {created}/{len(agents)}")
    return 0


# ══════════════════════════════════════════════════════════════════════
# 对账(reconcile.py 逐字移植)
# ══════════════════════════════════════════════════════════════════════
def _local_agents(system_id: str) -> dict:
    """读本地 amail 注册表: {agent_id: cfg}。"""
    out = {}
    base = os.path.expanduser(f"~/.aimail/systems/{system_id}")
    if not os.path.isdir(base):
        return out
    for addr_dir in sorted(os.listdir(base)):
        aj = os.path.join(base, addr_dir, "agentmail.json")
        if not os.path.isfile(aj):
            continue
        try:
            cfg = json.load(open(aj))
            if cfg.get("agent_id"):
                out[cfg["agent_id"]] = cfg
        except Exception:
            pass
    return out


def _save_agent_config(agent_id: str, cfg: dict, system_id: str) -> None:
    """落盘地址键 agentmail.json(共享布局)。"""
    cfg = dict(cfg)
    cfg["agent_id"] = agent_id
    cleaned = re.sub(r"[^\w.\-]", "_", cfg["email"])
    path = os.path.expanduser(f"~/.aimail/systems/{system_id}/{cleaned}/agentmail.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def reconcile(system_id: str = "", manager: str = "", dry_run: bool = False) -> int:
    """DeerFlow 生命周期对账(reconcile.py main 逐字移植,去 argparse;cron 调度用)。

    DeerFlow 无事件总线(无 created/deleted 回调),agent 由目录定义(SOUL.md/
    agents/)。以"目录为真相源"做幂等对账:
      1. 扫描 DeerFlow agents 目录(默认只识别 lead agent "default")
      2. 读 amail 注册表(systems/{sid}/*/agentmail.json)
      3. 差异动作(公共链幂等):
         有/无 → register_agent_email(4 步链)→ 落盘 agentmail.json
         无/有 → deregister_agent_email(3 步链)→ 清理本地

    Args:
        system_id: 缺省读 AIMAIL_SYSTEM_ID 环境变量 / detect_system_id()。
        manager:   manager_address;缺省读 AIMAIL_MANAGER。
        dry_run:   只打印差异,不执行。

    Returns:
        0(成功;缺 system_id / gateway config → 1,stderr 输出)。

    cron 示例(系统 crontab,每 30 分钟):
      */30 * * * * python3 .../manage.py reconcile --system-id SID
    """
    system_id = system_id or _base.detect_system_id()
    if not system_id:
        print("need --system-id (or AIMAIL_SYSTEM_ID / pointer)", file=sys.stderr)
        return 1
    gw = _base.load_gateway_config(system_id)
    if not gw:
        print(f"gateway config not found for {system_id}", file=sys.stderr)
        return 1

    # 1. 目录真相源(DeerFlow lead agent = "default";扩展扫描留待后续)
    desired = {"default": {"assistant_id": gw.get("assistant_id", "lead_agent")}}

    # 2. 本地注册表
    local = _local_agents(system_id)

    manager = manager or os.environ.get("AIMAIL_MANAGER", "")
    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))

    changes = 0
    for agent_id, meta in desired.items():
        if agent_id in local:
            continue  # 已注册,幂等跳过
        if dry_run:
            print(f"  [dry] would register {agent_id}")
            changes += 1
            continue
        email = _base.email_for_agent(agent_id, gw["domain"], gw.get("system_name", ""),
                                      default_aliases=("default",))
        webhook_secret = secrets.token_hex(32)
        # 本地接收端点(进程内预处理,DeerFlow 本地 gateway /aimail/inbound;
        # DEERFLOW_INBOUND_URL 可覆盖,2026-08-18 重构)
        inbound_base = os.environ.get("DEERFLOW_INBOUND_URL", "http://127.0.0.1:8001")
        local_webhook_url = inbound_base.rstrip("/") + "/aimail/inbound"
        # 注册参数三态:push=bridge 公网入口 / pull=空 / 无 bridge=本地端点
        reg_url = _core.resolve_register_webhook_url(gw, local_webhook_url)
        reg = _base.register_agent_email(
            client, system_id, email, reg_url, webhook_secret, manager,
        )
        if reg.get("api_key"):
            cfg = {
                "email": email,
                "gateway_url": gw["gateway_url"],
                "domain": gw["domain"],
                "system_id": system_id,
                "system_name": gw.get("system_name", ""),
                "manager_address": manager,
                "api_key": reg["api_key"],
                # agentmail.json webhook_url = 本地接收端点(唯一信任源,给 bridge 路由)
                "webhook_url": local_webhook_url,
                "webhook_secret": webhook_secret,
                "assistant_id": meta.get("assistant_id", "lead_agent"),
            }
            _save_agent_config(agent_id, cfg, system_id)
            changes += 1
            print(f"  ✓ registered {agent_id} → {email}")
            # 铁律:有 bridge 时注册后必须向 bridge 注册入站 hook 路由
            _core.register_bridge_route(system_id, email, gw, local_webhook_url)
        else:
            print(f"  ⚠ {agent_id} → {email} no api_key (activation pending)")

    # 3. 本地有而目录无 → 注销(当前仅 default,扩展后启用)
    for agent_id in local:
        if agent_id not in desired:
            if dry_run:
                print(f"  [dry] would deregister {agent_id}")
                changes += 1
                continue
            email = local[agent_id].get("email", "")
            result = _base.deregister_agent_email(client, system_id, email,
                                                  local[agent_id].get("manager_address", ""))
            cleaned = re.sub(r"[^\w.\-]", "_", email)
            path = os.path.expanduser(f"~/.aimail/systems/{system_id}/{cleaned}/agentmail.json")
            if os.path.isfile(path):
                os.remove(path)
            changes += 1
            print(f"  ✓ deregistered {agent_id} ({email}): {json.dumps(result, ensure_ascii=False)}")

    if changes == 0:
        print("  no changes")
    return 0


# ══════════════════════════════════════════════════════════════════════
# 注销(deregister_agent.py 逐字移植)
# ══════════════════════════════════════════════════════════════════════
def deregister_agents(agent: str, manager: str = "", system_id: str = "") -> int:
    """注销 DeerFlow agent 从 amail(deregister_agent.py main 逐字移植,去 argparse)。

    注销链(api-key → domain → whitelist)走公共核心
    amail_base.deregister_agent_email(所有平台共用,幂等),随后清理本地
    agentmail.json。

    Args:
        agent:     agent id(默认名 default);空串 → SystemExit "need --agent <id>"。
        manager:   manager_address;缺省读本地 cfg.manager_address / AIMAIL_MANAGER。
        system_id: 缺省读 AIMAIL_SYSTEM_ID 环境变量 / detect_system_id()。

    Returns:
        0(成功;本地未注册也返回 0 并提示 nothing to do)。

    例:
      deregister_agents(agent="default")
      deregister_agents(agent="default", system_id="SID")
    """
    if not agent:
        raise SystemExit("need --agent <id>")

    system_id = system_id or _base.detect_system_id()
    gw = _base.load_gateway_config(system_id)
    if not gw:
        raise SystemExit(f"gateway config not found for {system_id}")

    cfg = _base.load_agent_config(agent, system_id)
    if not cfg:
        print(f"  agent '{agent}' not registered locally — nothing to do")
        return 0

    email = cfg.get("email", "")
    manager = manager or cfg.get("manager_address", "") or os.environ.get("AIMAIL_MANAGER", "")

    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))
    result = _base.deregister_agent_email(client, system_id, email, manager)
    print(f"  deregister {agent} ({email}): {json.dumps(result, ensure_ascii=False)}")

    # 清理本地 agentmail.json
    cleaned = re.sub(r"[^\w.\-]", "_", email)
    path = os.path.expanduser(f"~/.aimail/systems/{system_id}/{cleaned}/agentmail.json")
    if os.path.isfile(path):
        os.remove(path)
        print(f"  ✓ removed {path}")
    return 0


# ══════════════════════════════════════════════════════════════════════
# app.py patch(install-inbound.sh 内嵌 python 段完整提取,幂等)
# ══════════════════════════════════════════════════════════════════════
def _gateway_layout(backend_dir: str) -> tuple[str, str]:
    """解析 DeerFlow gateway 目录与 app.py 路径。

    backend_dir 可传 DeerFlow 仓库根(install-inbound.sh 的 DEER_FLOW_ROOT,
    默认 ~/deer-flow)或已含 backend 的目录(如 .../deer-flow/backend);两者
    均自动识别。返回 (gateway_dir, app_py)。
    """
    root = os.path.abspath(os.path.expanduser(backend_dir))
    for g in (os.path.join(root, "backend", "app", "gateway"),
              os.path.join(root, "app", "gateway")):
        if os.path.isfile(os.path.join(g, "app.py")):
            return g, os.path.join(g, "app.py")
    # 缺省按仓库根布局返回(错误信息展示期望路径)
    g = os.path.join(root, "backend", "app", "gateway")
    return g, os.path.join(g, "app.py")


def patch_backend_app(backend_dir: str) -> bool:
    """patch DeerFlow backend/app/gateway/app.py(幂等;install-inbound.sh patch 段)。

    2a. import 行: 在 "    agents,\\n" 后插 "    aimail_inbound,\\n"(字母序相邻)。
    2b. include_router 行: 在 "    app.include_router(agents.router)\\n" 后插
        "    app.include_router(aimail_inbound.router)\\n"。

    Args:
        backend_dir: DeerFlow 仓库根(或含 backend 的目录)。

    Returns:
        True  = 本次应用了修改;False = app.py 已含 aimail_inbound(幂等跳过)。

    Raises:
        SystemExit: app.py 不存在或锚点缺失(文案与原脚本一致)。
    """
    g_dir, app_py = _gateway_layout(backend_dir)
    if not os.path.isfile(app_py):
        raise SystemExit(f"ERROR: app.py not found: {app_py}")
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
        return True
    print("  ✓ app.py 已含 aimail_inbound(跳过)")
    return False


# ══════════════════════════════════════════════════════════════════════
# 捆绑安装(install-inbound.sh 前段 runtime_bundle 调用的简化移植)
# ══════════════════════════════════════════════════════════════════════
def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_kind(core_dir: str) -> str:
    """源类型判定(repo pysdk/ vs pip site-packages/aimail/,仅影响版本戳)。"""
    if os.path.basename(os.path.abspath(core_dir)) == "pysdk":
        return "repo"
    try:
        import aimail  # type: ignore
        if os.path.abspath(os.path.dirname(os.path.abspath(aimail.__file__))) == os.path.abspath(core_dir):
            return "pip"
    except Exception:
        pass
    return "pip"


def _bundle_version(core_dir: str, kind: str) -> str:
    """捆绑源版本: pip → aimail.__version__;repo → git describe(失败 dev)。"""
    if kind == "pip":
        try:
            import aimail  # type: ignore
            return getattr(aimail, "__version__", "0.0.0")
        except Exception:
            return "0.0.0"
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "-C", core_dir, "describe", "--always", "--dirty"],
            text=True, timeout=10, stderr=subprocess.DEVNULL).strip()
        return out or "dev"
    except Exception:
        return "dev"


def install_bundle(backend_dir: str, source_root: str = "", force: bool = False) -> int:
    """安装 deer-flow 运行时捆绑到宿主 routers/aimail/(幂等,md5 漂移可检出)。

    源(pysdk 单一真源,7 文件)→ 目标 <gateway>/routers/aimail/:
      deer-flow/{aimail_inbound.py, amail_base.py}
      {aimail_base.py, aimail_tools.py, aimail_board.py, gateway_api.py,
       _aimail_bootstrap.py}
    落 .aimail-runtime.json 版本戳(bundle/version/source/installed_at/
    min_version/files md5),与原 runtime_bundle 戳同构。

    Args:
        backend_dir: DeerFlow 仓库根(或含 backend 的目录)。
        source_root: 源根显式指定(须含 aimail_base.py);缺省用本模块自举出的
                     核心目录(仓库 pysdk/ 或 pip aimail/ 包目录)。
        force:       强制覆盖(忽略 md5 一致跳过)。

    Returns:
        0 = 完成(全部一致或已更新);1 = 源文件缺失。
    """
    if source_root:
        root = os.path.abspath(os.path.expanduser(source_root))
        if not os.path.isfile(os.path.join(root, "aimail_base.py")):
            raise SystemExit(f"ERROR: --source-root 无效(无 aimail_base.py): {root}")
        kind = _source_kind(root)
    else:
        root, kind = _CORE_DIR, _source_kind(_CORE_DIR)
    version = _bundle_version(root, kind)

    g_dir, _app_py = _gateway_layout(backend_dir)
    dst_dir = os.path.join(g_dir, "routers", "aimail")

    changed, missing_src = [], []
    for src_rel, dst_name in _BUNDLE_FILES:
        src = os.path.join(root, src_rel)
        dst = os.path.join(dst_dir, dst_name)
        if not os.path.isfile(src):
            missing_src.append(src_rel)
            continue
        need = force or not os.path.isfile(dst) or _md5(src) != _md5(dst)
        if need:
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copyfile(src, dst)
            changed.append(dst_name)

    if missing_src:
        print(f"  ✗ deer-flow: 源缺失 {missing_src}(源根 {root})")
        return 1

    stamp = {
        "bundle": "deer-flow",
        "version": version,
        "source": kind,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_version": MIN_PAYLOAD_VERSION,
        "files": {name: _md5(os.path.join(dst_dir, name))
                  for _rel, name in _BUNDLE_FILES
                  if os.path.isfile(os.path.join(dst_dir, name))},
    }
    os.makedirs(dst_dir, exist_ok=True)
    tmp = os.path.join(dst_dir, STAMP_NAME) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stamp, f, indent=2, ensure_ascii=False)
    os.replace(tmp, os.path.join(dst_dir, STAMP_NAME))

    if changed:
        print(f"  ✓ deer-flow: 更新 {len(changed)} 文件 → {dst_dir} (v{version}, {kind})")
    else:
        print(f"  ✓ deer-flow: 已一致(跳过)→ {dst_dir} (v{version}, {kind})")
    return 0


# ══════════════════════════════════════════════════════════════════════
# CLI 子命令分发(原脚本用法兼容)
# ══════════════════════════════════════════════════════════════════════
def main(argv: list | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="DeerFlow aimail 管理(注册/对账/注销/入站安装)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # register_agent.py 同款参数
    p = sub.add_parser("register", description="注册 DeerFlow agent 到 amail")
    p.add_argument("--agent", default="")
    p.add_argument("--all", action="store_true", help="注册全部 DeerFlow agents")
    p.add_argument("--manager", default="", help="manager_address(审批联系人);缺省读 AIMAIL_MANAGER 环境变量")
    p.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    p.set_defaults(func=lambda a: _cmd_register(a))

    # reconcile.py 同款参数
    p = sub.add_parser("reconcile", description="DeerFlow 生命周期对账")
    p.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    p.add_argument("--manager", default="", help="manager_address;缺省读 AIMAIL_MANAGER")
    p.add_argument("--dry-run", action="store_true", help="只打印差异,不执行")
    p.set_defaults(func=lambda a: reconcile(system_id=a.system_id, manager=a.manager,
                                            dry_run=a.dry_run))

    # deregister_agent.py 同款参数
    p = sub.add_parser("deregister", description="注销 DeerFlow agent 从 amail")
    p.add_argument("--agent", required=True, help="agent id(默认名 default)")
    p.add_argument("--manager", default="", help="manager_address;缺省读 AIMAIL_MANAGER")
    p.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    p.set_defaults(func=lambda a: deregister_agents(agent=a.agent, manager=a.manager,
                                                    system_id=a.system_id))

    # install-inbound.sh: patch 段(仅 app.py)
    p = sub.add_parser("patch", description="patch DeerFlow backend app.py(幂等;import + include_router)")
    p.add_argument("deerflow_root", nargs="?", default="~/deer-flow", help="DeerFlow 仓库根(默认 ~/deer-flow)")
    p.set_defaults(func=lambda a: _cmd_patch(a))

    # install-inbound.sh: 完整流(捆绑安装 + patch + 校验)
    p = sub.add_parser("install", description="安装 deer-flow 入站捆绑 + patch app.py + 校验")
    p.add_argument("deerflow_root", nargs="?", default="~/deer-flow", help="DeerFlow 仓库根(默认 ~/deer-flow)")
    p.add_argument("--source-root", default="", help="捆绑源根(缺省=pysdk/pip aimail 包目录)")
    p.add_argument("--force", action="store_true", help="强制覆盖(忽略 md5 一致跳过)")
    p.set_defaults(func=lambda a: _cmd_install(a))

    args = ap.parse_args(argv)
    return args.func(args)


def _cmd_register(a) -> int:
    """register 子命令: --agent/--all 互斥校验(原 argparse 文案)后转函数。"""
    if not a.agent and not a.all:
        raise SystemExit("need --agent <id> or --all")
    if a.agent and a.all:
        raise SystemExit("--agent and --all are mutually exclusive")
    agent = a.agent or ("all" if a.all else "")
    return register_agents(manager=a.manager, system_id=a.system_id, agent=agent)


def _cmd_patch(a) -> int:
    """patch 子命令: 仅 patch app.py + 锚点确认。"""
    patch_backend_app(a.deerflow_root)
    # 锚点确认(原脚本 step-3 grep;patch 本身成功即锚点存在,此处复核)
    _g, app_py = _gateway_layout(a.deerflow_root)
    if os.path.isfile(app_py) and "aimail_inbound" in open(app_py, encoding="utf-8").read():
        print("  ✓ 锚点确认(app.py 含 aimail_inbound)")
    return 0


def _cmd_install(a) -> int:
    """install 子命令: 复刻 install-inbound.sh 完整流(捆绑 → patch → 校验)。"""
    rc = install_bundle(a.deerflow_root, source_root=a.source_root, force=a.force)
    if rc != 0:
        return rc
    try:
        patch_backend_app(a.deerflow_root)
    except SystemExit as e:
        print(e)
        return 1

    # step-3 校验:语法(宿主 venv 存在时)+ 锚点;venv 缺失仅跳过语法编译
    g_dir, app_py = _gateway_layout(a.deerflow_root)
    venv_py = os.path.join(os.path.dirname(g_dir), "..", ".venv", "bin", "python")
    venv_py = os.path.abspath(venv_py)
    ok = True
    if os.path.isfile(venv_py):
        targets = [os.path.join(g_dir, "routers", "aimail", "aimail_inbound.py"), app_py]
        r = os.system(f'"{venv_py}" -m py_compile {" ".join(targets)}')
        if r != 0:
            ok = False
    else:
        print("  (宿主 .venv 未找到,跳过 py_compile 语法校验)")
    if "aimail_inbound" not in open(app_py, encoding="utf-8").read():
        print("ERROR: app.py 锚点缺失")
        ok = False
    if ok:
        print("  ✓ 语法校验通过 + 锚点确认")
    root = os.path.expanduser(a.deerflow_root)
    print("")
    print("完成。重启 DeerFlow gateway(8001)后生效:")
    print(f"  kill <uvicorn-pid> && cd {root}/backend && DEER_FLOW_AUTH_DISABLED=1 \\")
    print("    PYTHONPATH=. PYTHONIOENCODING=utf-8 .venv/bin/uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

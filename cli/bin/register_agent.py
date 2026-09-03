#!/usr/bin/env python3
"""register_agent.py — OpenClaw agent 注册到 amail（步骤 6）。

注册链（register_email → 已存在更新 webhook → manager 白名单 → activate_address）
走公共核心 aimail_base.register_agent_email（Hermes/OpenClaw 共用）：
  1. 计算 email（main → agent@{domain}，其余 {agentId}@{domain}；共享域加 .{system_name}）
  2. 注册链（公共，幂等）→ api_key
  3. 落盘地址键 agentmail.json（systems/{sid}/{addr}/agentmail.json，含 agent_id）

用法:
  python3 register_agent.py --agent main --manager admin@x.com
  python3 register_agent.py --all --manager admin@x.com    # 注册全部 OpenClaw agents
  python3 register_agent.py --agent work --manager admin@x.com --system-id SID
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/scripts"
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from runtime_core import load_core, load_adapter  # noqa: E402
load_core()
load_adapter("openclaw")

import amail_base as _base            # noqa: E402
import aimail_tools as _tools      # noqa: E402


def email_for_agent(agent_id: str, domain: str, system_name: str) -> str:
    """地址派生（公共核心 email_for_agent；OpenClaw 默认名 main → agent，
    其余保持原名；非法字符清洗含 '.' → '_'）。"""
    return _base.email_for_agent(agent_id, domain, system_name,
                                 default_aliases=("main",))


def discover_openclaw_agents() -> list:
    """列出 OpenClaw agents（openclaw agents list --json）；失败回退 [main]。"""
    try:
        out = subprocess.run(["openclaw", "agents", "list", "--json"],
                             capture_output=True, timeout=15, text=True)
        if out.returncode == 0 and out.stdout.strip():
            data = json.loads(out.stdout)
            agents = data if isinstance(data, list) else data.get("agents", [])
            ids = [a.get("id") or a.get("agentId") for a in agents]
            if ids:
                return ids
    except Exception:
        pass
    return ["main"]


def register_one(client, system_id: str, agent_id: str, email: str,
                 webhook_url: str, webhook_secret: str, manager_address: str,
                 domain: str, system_name: str, gateway_url: str,
                 local_webhook_url: str) -> dict:
    """注册单个 agent：核心链走公共 register_agent_email，平台部分只做本地落盘组装。

    webhook_url(注册参数)由 resolve_register_webhook_url 三态决定(push=bridge
    公网入口 / pull=空 / 无 bridge=本地端点);agentmail.json 落盘的一律是
    local_webhook_url(本地接收端点,唯一信任源,给 bridge 路由表)。
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
        # agentmail.json webhook_url = 本地接收端点(唯一信任源,给 bridge 路由);
        # 与地址注册参数是两个值(见 resolve_register_webhook_url)。
        "webhook_url": local_webhook_url,
        "webhook_secret": webhook_secret,
    }
    return cfg


def register_bridge_route(system_id: str, email: str, gw: dict,
                          local_webhook_url: str) -> dict:
    """注册后向本机 bridge POST 路由(共享实现,见 aimail_base)。"""
    return _base.register_bridge_route(system_id, email, gw, local_webhook_url)


def main() -> int:
    ap = argparse.ArgumentParser(description="注册 OpenClaw agent 到 amail")
    ap.add_argument("--agent", default="")
    ap.add_argument("--all", action="store_true", help="注册全部 OpenClaw agents")
    ap.add_argument("--manager", default="", help="manager_address（审批联系人）；缺省读 AIMAIL_MANAGER 环境变量")
    ap.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    args = ap.parse_args()

    if not args.agent and not args.all:
        raise SystemExit("need --agent <id> or --all")
    if args.agent and args.all:
        raise SystemExit("--agent and --all are mutually exclusive")

    system_id = args.system_id or _base.detect_system_id()
    gw = _base.load_gateway_config(system_id)
    if not gw:
        raise SystemExit(f"gateway config not found (agentmail_gateway.json) for {system_id} — run activate.py first")
    manager = args.manager or os.environ.get("AIMAIL_MANAGER", "")
    if not manager:
        raise SystemExit("need --manager <addr> or AIMAIL_MANAGER env (审批联系人)")
    print(f"system_id={system_id} domain={gw.get('domain')}")

    # admin client（register_email/activate_address 全在 _GatewayClient）
    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))

    agents = [args.agent] if args.agent else discover_openclaw_agents()
    if not agents:
        agents = ["main"]

    # 本地接收端点(平台常量:TS 插件 openclaw-aimail 在 OpenClaw gateway HTTP
    # 上注册 /aimail/deliver;pull 模式下注册参数应为空值,此默认仅作
    # 无 bridge 兜底)。旧 amail_openclaw_bridge.py :8799/hook 已退役。
    local_webhook_url = "http://127.0.0.1:18789/aimail/deliver"
    # 注册参数三态:push=bridge 公网入口 / pull=空 / 无 bridge=本地端点
    reg_url = _base.resolve_register_webhook_url(gw, local_webhook_url)
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
            _base.save_agent_config(agent_id, cfg, system_id)
            created += 1
            print(f"  ✓ {agent_id} → {email} (api_key ok)")
            # 注册后向本机 bridge 注册路由(email → 本地接收端点全 URL)
            register_bridge_route(system_id, email, gw, local_webhook_url)
        else:
            print(f"  ⚠ {agent_id} → {email} registered but no api_key (activation pending)")

    print(f"registered: {created}/{len(agents)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

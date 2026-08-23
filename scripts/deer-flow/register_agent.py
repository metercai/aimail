#!/usr/bin/env python3
"""register_agent.py — DeerFlow agent 注册到 amail(生命周期)。

注册链(register_email → 已存在更新 webhook → manager 白名单 → activate_address)
走公共核心 aimail_base.register_agent_email(所有平台共用):
  1. 计算 email(default → agent@{domain},其余 {agentId}@{domain};共享域加 .{system_name})
  2. 注册链(公共,幂等)→ api_key
  3. 落盘地址键 agentmail.json(systems/{sid}/{cleaned_addr}/agentmail.json,
     含 agent_id)— MCP server 与 bridge 按此布局发现 agent

用法:
  python3 register_agent.py --agent default --manager admin@x.com
  python3 register_agent.py --all --manager admin@x.com
  python3 register_agent.py --agent work --manager admin@x.com --system-id SID

注:DeerFlow 生命周期以"对账"为主(reconcile.py),本脚本供即时注册/单 agent 场景。
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from runtime_core import load_core, load_adapter  # noqa: E402
load_core()
load_adapter("deer-flow")

import amail_base as _base            # noqa: E402
import aimail_tools as _tools      # noqa: E402


def email_for_agent(agent_id: str, domain: str, system_name: str) -> str:
    """地址派生(公共核心 email_for_agent;DeerFlow 默认名 default → agent)。"""
    return _base.email_for_agent(agent_id, domain, system_name,
                                 default_aliases=("default",))


def discover_deerflow_agents() -> list:
    """列出 DeerFlow agents: 扫描 skills 目录 + SOUL.md 判定(默认 lead agent)。
    简化实现: 返回 ["default"](DeerFlow 默认 lead_agent);后续 reconcile.py
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
    import re
    cfg = dict(cfg)
    cfg["agent_id"] = agent_id
    cleaned = re.sub(r"[^\w.\-]", "_", cfg["email"])
    path = os.path.expanduser(f"~/.agentmail/systems/{system_id}/{cleaned}/agentmail.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  ✓ saved {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="注册 DeerFlow agent 到 amail")
    ap.add_argument("--agent", default="")
    ap.add_argument("--all", action="store_true", help="注册全部 DeerFlow agents")
    ap.add_argument("--manager", default="", help="manager_address(审批联系人);缺省读 AIMAIL_MANAGER 环境变量")
    ap.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    args = ap.parse_args()

    if not args.agent and not args.all:
        raise SystemExit("need --agent <id> or --all")
    if args.agent and args.all:
        raise SystemExit("--agent and --all are mutually exclusive")

    system_id = args.system_id or _base.detect_system_id()
    gw = _base.load_gateway_config(system_id)
    if not gw:
        raise SystemExit(f"gateway config not found (agentmail_gateway.json) for {system_id} — run agentmail install first")
    manager = args.manager or os.environ.get("AIMAIL_MANAGER", "")
    if not manager:
        raise SystemExit("need --manager <addr> or AIMAIL_MANAGER env (审批联系人)")

    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))
    agents = [args.agent] if args.agent else discover_deerflow_agents()
    if not agents:
        agents = ["default"]

    # 本地接收端点(进程内预处理,DeerFlow 本地 gateway /agentmail/inbound;
    # DEERFLOW_INBOUND_URL 可覆盖);注册参数三态由 resolve_register_webhook_url 决定
    inbound_base = os.environ.get("DEERFLOW_INBOUND_URL", "http://127.0.0.1:8001")
    local_webhook_url = inbound_base.rstrip("/") + "/agentmail/inbound"
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
            save_agent_config(agent_id, cfg, system_id)
            created += 1
            print(f"  ✓ {agent_id} → {email} (api_key ok)")
            # 铁律:有 bridge 时注册后必须向 bridge 注册入站 hook 路由
            _base.register_bridge_route(system_id, email, gw, local_webhook_url)
        else:
            print(f"  ⚠ {agent_id} → {email} registered but no api_key (activation pending)")

    print(f"registered: {created}/{len(agents)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

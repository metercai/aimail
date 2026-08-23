#!/usr/bin/env python3
"""reconcile.py — DeerFlow 生命周期对账(cron 调度)。

DeerFlow 无事件总线(无 created/deleted 回调),agent 由目录定义(SOUL.md/
agents/)。本脚本以"目录为真相源"做幂等对账:
  1. 扫描 DeerFlow agents 目录(默认只识别 lead agent "default")
  2. 读 amail 注册表(systems/{sid}/*/agentmail.json)
  3. 差异动作(公共链幂等):
     有/无 → register_agent_email(4 步链)→ 落盘 agentmail.json
     无/有 → deregister_agent_email(3 步链)→ 清理本地

用法:
  python3 reconcile.py [--system-id SID] [--manager M] [--dry-run]

cron 示例(系统 crontab,每 30 分钟):
  */30 * * * * python3 /home/ubuntu/agentmail/scripts/deer-flow/reconcile.py --system-id SID
"""
from __future__ import annotations

import argparse
import json
import os
import re
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


def _local_agents(system_id: str) -> dict:
    """读本地 amail 注册表: {agent_id: cfg}。"""
    out = {}
    base = os.path.expanduser(f"~/.agentmail/systems/{system_id}")
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
    path = os.path.expanduser(f"~/.agentmail/systems/{system_id}/{cleaned}/agentmail.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="DeerFlow 生命周期对账")
    ap.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    ap.add_argument("--manager", default="", help="manager_address;缺省读 AIMAIL_MANAGER")
    ap.add_argument("--dry-run", action="store_true", help="只打印差异,不执行")
    args = ap.parse_args()

    system_id = args.system_id or _base.detect_system_id()
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

    manager = args.manager or os.environ.get("AIMAIL_MANAGER", "")
    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))

    changes = 0
    for agent_id, meta in desired.items():
        if agent_id in local:
            continue  # 已注册,幂等跳过
        if args.dry_run:
            print(f"  [dry] would register {agent_id}")
            changes += 1
            continue
        email = _base.email_for_agent(agent_id, gw["domain"], gw.get("system_name", ""),
                                      default_aliases=("default",))
        webhook_secret = secrets.token_hex(32)
        # 本地接收端点(进程内预处理,DeerFlow 本地 gateway /agentmail/inbound;
        # DEERFLOW_INBOUND_URL 可覆盖,2026-08-18 重构)
        inbound_base = os.environ.get("DEERFLOW_INBOUND_URL", "http://127.0.0.1:8001")
        local_webhook_url = inbound_base.rstrip("/") + "/agentmail/inbound"
        # 注册参数三态:push=bridge 公网入口 / pull=空 / 无 bridge=本地端点
        reg_url = _base.resolve_register_webhook_url(gw, local_webhook_url)
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
            _base.register_bridge_route(system_id, email, gw, local_webhook_url)
        else:
            print(f"  ⚠ {agent_id} → {email} no api_key (activation pending)")

    # 3. 本地有而目录无 → 注销(当前仅 default,扩展后启用)
    for agent_id in local:
        if agent_id not in desired:
            if args.dry_run:
                print(f"  [dry] would deregister {agent_id}")
                changes += 1
                continue
            email = local[agent_id].get("email", "")
            result = _base.deregister_agent_email(client, system_id, email,
                                                  local[agent_id].get("manager_address", ""))
            cleaned = re.sub(r"[^\w.\-]", "_", email)
            path = os.path.expanduser(f"~/.agentmail/systems/{system_id}/{cleaned}/agentmail.json")
            if os.path.isfile(path):
                os.remove(path)
            changes += 1
            print(f"  ✓ deregistered {agent_id} ({email}): {json.dumps(result, ensure_ascii=False)}")

    if changes == 0:
        print("  no changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""deregister_agent.py — OpenClaw agent 注销（步骤 10，补全 Hermes 缺口）。

注销链：
  1. DELETE /api/v1/admin/api-keys/:id（删 agent 身份令牌）
  2. DELETE /api/v1/admin/systems/:sid/domains/<email>（停收信）
  3. 白名单清理（DELETE /api/v1/whitelists/:id 或按值删）
  4. 删本地地址键 agentmail.json（systems/{sid}/{addr}/agentmail.json）
  5. openclaw agents delete <agentId>（workspace/会话进 Trash）

用法:
  python3 deregister_agent.py --agent <agentId> [--system-id SID] [--no-openclaw-delete]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from runtime_core import load_core, load_adapter  # noqa: E402
load_core()
load_adapter("openclaw")

import amail_base as _base            # noqa: E402
import aimail_tools as _tools      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="注销 OpenClaw agent 的 amail 注册")
    ap.add_argument("--agent", required=True)
    ap.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    ap.add_argument("--no-openclaw-delete", action="store_true", help="不执行 openclaw agents delete")
    args = ap.parse_args()

    system_id = args.system_id or _base.detect_system_id()
    gw = _base.load_gateway_config(system_id)
    if not gw:
        raise SystemExit(f"gateway config not found (agentmail_gateway.json) for {system_id}")
    cfg = _base.load_agent_config(args.agent, system_id)
    if not cfg:
        print(f"agent {args.agent} not registered locally — nothing to do")
        return 0

    email = cfg["email"]
    admin_key = gw.get("admin_key", "")
    client = _tools._GatewayClient(gw["gateway_url"], admin_key)

    # 1-3. API 注销链（api-key → domain → whitelist，公共核心幂等）
    mgr = gw.get("manager_address", "")
    status = _base.deregister_agent_email(client, system_id, email, manager_address=mgr)
    print(f"  api-key: {status.get('api_key')} | domain: {status.get('domain')} | whitelist: {status.get('whitelist')}")

    # 4. 本地清理（地址键 agentmail.json）
    cfg_path = _base.agent_config_path(system_id, email)
    if cfg_path.is_file():
        cfg_path.unlink()
        print(f"  removed {cfg_path}")

    # 5. OpenClaw agent 删除
    if not args.no_openclaw_delete:
        r = subprocess.run(["openclaw", "agents", "delete", args.agent, "--force"],
                           capture_output=True, text=True, timeout=30)
        print(f"  openclaw agents delete: exit {r.returncode}")

    print(f"deregistered: {args.agent} ({email})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

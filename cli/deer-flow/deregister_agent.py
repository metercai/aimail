#!/usr/bin/env python3
"""deregister_agent.py — DeerFlow agent 从 amail 注销(生命周期)。

注销链(api-key → domain → whitelist)走公共核心
aimail_base.deregister_agent_email(所有平台共用,幂等)。

用法:
  python3 deregister_agent.py --agent default [--system-id SID] [--manager M]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from runtime_core import load_core, load_adapter  # noqa: E402
load_core()
load_adapter("deer-flow")

import amail_base as _base            # noqa: E402
import aimail_tools as _tools      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="注销 DeerFlow agent 从 amail")
    ap.add_argument("--agent", required=True, help="agent id(默认名 default)")
    ap.add_argument("--manager", default="", help="manager_address;缺省读 AIMAIL_MANAGER")
    ap.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    args = ap.parse_args()

    system_id = args.system_id or _base.detect_system_id()
    gw = _base.load_gateway_config(system_id)
    if not gw:
        raise SystemExit(f"gateway config not found for {system_id}")

    cfg = _base.load_agent_config(args.agent, system_id)
    if not cfg:
        print(f"  agent '{args.agent}' not registered locally — nothing to do")
        return 0

    email = cfg.get("email", "")
    manager = args.manager or cfg.get("manager_address", "") or os.environ.get("AIMAIL_MANAGER", "")

    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))
    result = _base.deregister_agent_email(client, system_id, email, manager)
    print(f"  deregister {args.agent} ({email}): {json.dumps(result, ensure_ascii=False)}")

    # 清理本地 agentmail.json
    cleaned = re.sub(r"[^\w.\-]", "_", email)
    path = os.path.expanduser(f"~/.agentmail/systems/{system_id}/{cleaned}/agentmail.json")
    if os.path.isfile(path):
        os.remove(path)
        print(f"  ✓ removed {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

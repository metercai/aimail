#!/usr/bin/env python3
"""unbind_agent.py — 解绑 dsh session(网关注销 + 删 agentmail.json)。

用法:
  python3 scripts/dsh/unbind_agent.py --email <addr> [--system-id <sid>]
"""
import argparse
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from runtime_core import load_core  # noqa: E402
load_core()

import aimail_base as _base            # noqa: E402
import aimail_tools as _tools          # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="解绑 dsh session 与 aimail 地址")
    ap.add_argument("--email", required=True, help="aimail 地址(agent.dsh@domain)")
    ap.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    args = ap.parse_args()

    system_id = args.system_id or _base.detect_system_id()
    gw = _base.load_gateway_config(system_id)
    if not gw:
        raise SystemExit(f"gateway config not found for {system_id}")

    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))
    st = _base.deregister_agent_email(client, system_id, args.email,
                                      manager_address=gw.get("manager_address", ""))
    print(f"  ✓ gateway deregister {args.email} (api-key={st.get('api_key')} "
          f"domain={st.get('domain')} whitelist={st.get('whitelist')})")

    p = os.path.expanduser(f"~/.aimail/systems/{system_id}/{_base._clean_agent_dir_name(args.email)}/agentmail.json")
    if os.path.isfile(p):
        os.remove(p)
        print(f"  ✓ removed {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

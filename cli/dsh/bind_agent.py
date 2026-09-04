#!/usr/bin/env python3
"""bind_agent.py — 绑定 dsh session 到 aimail 地址(注册链 + 落盘 + 路由)。

流程(2026-08-18 方案 §5.4):
  1. 解析 system/gw(preset 默认 mail)
  2. session_id:--session-id 指定(复用已存在 dsh session)或生成 uuid
     (落盘后提示在 dsh 侧用该 id 创建 session)
  3. 地址:email_for_agent('agent', ...)(dsh 无内置默认,主 agent 约定绑 agent)
  4. gateway 注册链:register_agent_email(注册参数 = webhook_host 三态)
  5. agentmail.json 落盘(email/api_key/webhook_url/webhook_secret/session_id/preset)
  6. register_bridge_route(email → 本地接收端点)——铁律

用法:
  python3 scripts/dsh/bind_agent.py [--session-id <uuid>] [--preset mail]
      [--manager <addr>] [--system-id <sid>]
"""
import argparse
import json
import os
import secrets
import sys
import uuid

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from runtime_core import load_core  # noqa: E402
load_core()

import aimail_base as _base            # noqa: E402
import aimail_tools as _tools          # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="绑定 dsh session 到 aimail 地址")
    ap.add_argument("--session-id", default="", help="dsh session id(复用已存在 session);缺省生成 uuid")
    ap.add_argument("--preset", default="mail", help="dsh preset 名(agent 定义层,默认 mail)")
    ap.add_argument("--manager", default="", help="manager_address(审批联系人);缺省读 AIMAIL_MANAGER env")
    ap.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    args = ap.parse_args()

    system_id = args.system_id or _base.detect_system_id()
    gw = _base.load_gateway_config(system_id)
    if not gw:
        raise SystemExit(f"gateway config not found (aimail_gateway.json) for {system_id} — run aimail install first")
    manager = args.manager or os.environ.get("AIMAIL_MANAGER", "")
    if not manager:
        raise SystemExit("need --manager <addr> or AIMAIL_MANAGER env (审批联系人)")

    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))
    email = _base.email_for_agent("agent", gw["domain"], gw.get("system_name", ""))
    session_id = args.session_id or uuid.uuid4().hex
    webhook_secret = secrets.token_hex(32)

    # 本地接收端点(mail-inbound,默认 9099;AIMAIL_INBOUND_URL 可覆盖)。
    # 入站路径 = /aimail/inbound(tssdk dsh-aimail/src/mail-service.ts:55
    # INBOUND_PATH 唯一真相源)。
    inbound_base = os.environ.get("AIMAIL_INBOUND_URL", "http://127.0.0.1:9099")
    local_webhook_url = inbound_base.rstrip("/") + "/aimail/inbound"
    # 注册参数三态:push=bridge 公网入口 / pull=空 / 无 bridge=本地端点
    reg_url = _base.resolve_register_webhook_url(gw, local_webhook_url)

    reg = _base.register_agent_email(client, system_id, email, reg_url, webhook_secret, manager)
    if not reg.get("api_key"):
        print(f"  ⚠ {email} registered but no api_key (activation pending)")
        return 1

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
        # dsh 绑定:session_id(实例身份)+ preset(定义层)
        "session_id": session_id,
        "preset": args.preset,
    }
    # 落盘 agentmail.json(地址键路径,原子 tmp+replace,600)
    p = os.path.expanduser(f"~/.aimail/systems/{system_id}/{_base._clean_agent_dir_name(email)}/agentmail.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    print(f"  ✓ 注册 {email} (api_key ok)")
    print(f"  ✓ session_id = {session_id} (preset={args.preset})")

    # 铁律:注册后向 bridge 注册入站 hook 路由
    _base.register_bridge_route(system_id, email, gw, local_webhook_url)

    print()
    print("  dsh 侧步骤(绑定生效):")
    print(f"    1. 若 session 未创建:dsh 中创建 session id={session_id}(preset={args.preset})")
    print("    2. 挂载 mail 插件:preset 配置含 dsh-mail + dsh-tool-mail + dsh-mail-inbound")
    print("    3. 入站端点:http://127.0.0.1:9099/aimail/inbound(bridge 路由已注册)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

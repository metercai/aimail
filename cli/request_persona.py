#!/usr/bin/env python3
"""request_persona.py — 角色管理闭环薄触发:替 manager 发 "update persona" 邮件。

闭环:
  本脚本替 manager 发 subject="update persona" 邮件(SMTP,from=manager)
  → 网关不拦截(不含 manager 指令触发词)→ 送 agent
  → 预处理注入 Role_Calibrator 角色 prompt(SOUL + skills 自动填充)
  → LLM 会话内归纳 draft persona + signature,回复 manager
  → manager 修订后回 "approve persona\npersona:...\nsignature:..."
  → 网关拦截入库(domain_addr_meta 唯一权威),不再通知 agent

send API 无法代发(from 必须匹配 API key 邮箱),故复用 send_welcome 的
SMTP 路径:advanced 版 auth.local 认证(agent api_key),base 版 manager
直发(注册时 manager 已自动加白)。

用法:
  python3 request_persona.py [--system-id SID] [--agent-home DIR]
                             [--agent ADDR] [--to ADDR] [--manager ADDR]
                             [--timeout SECS] [--no-wait]
退出码: 0=成功(收到 agent draft 回复; --no-wait 时=发送成功), 1=失败
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 复用 send_welcome 的 SMTP 发送 / 版式探测 / 回复轮询(同一 scripts/ 目录,
# 其模块级 load_core() 为共享核心加载器,import 无副作用)。
import send_welcome as _sw  # noqa: E402

SUBJECT = "update persona"

BODY = """请根据你的 SOUL 与已加载 skills,归纳你的角色自述(persona)与签名(signature)草案。

回复格式(可直接修改内容后发回,我会以 approve persona 指令批准生效):

approve persona
persona: <角色自述,1-3 句,对外介绍你是谁、能做什么>
signature: <出站邮件签名>
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description="角色管理闭环薄触发:替 manager 发 'update persona' 邮件给 agent")
    ap.add_argument("--system-id", default="")
    ap.add_argument("--agent-home", default="",
                    help="agent 系统 home(Hermes=~/.hermes, OpenClaw=~/.openclaw)")
    ap.add_argument("--agent", default="", help="agent 标识(定位 mail 目录/地址)")
    ap.add_argument("--to", default="", help="直接指定收件地址(优先)")
    ap.add_argument("--manager", default="", help="manager 地址(发件人),默认 config.manager_address")
    ap.add_argument("--timeout", type=int, default=300, help="等待 draft 回复秒数")
    ap.add_argument("--no-wait", action="store_true", help="发送后不等待回复,直接退出")
    args = ap.parse_args()

    agent_home = Path(args.agent_home or os.environ.get("AGENT_HOME", str(Path.home() / ".hermes")))

    # ── 解析系统身份(与 send_welcome 一致):--to > --agent > 指针 > env ──
    sid = args.system_id
    email = args.agent
    pointer = agent_home / ".agentmail"
    if pointer.is_file():
        try:
            pd = json.loads(pointer.read_text())
            sid = sid or pd.get("system_id", "")
            email = email or pd.get("email", "")
        except Exception:
            pass
    sid = sid or os.environ.get("SYSTEM_ID", "") or os.environ.get("AIMAIL_SYSTEM_ID", "")
    if not sid:
        print("✗ system_id 未解析(需 --system-id 或 {agent-home}/.agentmail 指针)")
        return 1

    config_path = _sw.SYSTEMS_DIR / sid / "agentmail_gateway.json"
    if not config_path.exists():
        print(f"✗ agentmail_gateway.json not found: {config_path}")
        return 1
    cfg = json.loads(config_path.read_text())
    gw_url = cfg.get("gateway_url", "")
    manager = args.manager or os.environ.get("AIMAIL_MANAGER_ADDRESS") \
        or os.environ.get("MANAGER") or cfg.get("manager_address", "")
    if not manager:
        print("✗ manager 地址未解析(--manager / AIMAIL_MANAGER_ADDRESS / config.manager_address)")
        return 1

    recipient = args.to or email
    if not recipient:
        recipient = _sw._main_agent_email(cfg)
    if not gw_url:
        print("✗ Missing gateway_url")
        return 1

    # auth.local 认证 key:agent api_key(与 send_welcome --smtp 一致的最小权限路径)
    ak = ""
    try:
        agent_cfg_path = _sw.SYSTEMS_DIR / sid / _sw._clean_agent_dir_name(recipient) / "agentmail.json"
        if agent_cfg_path.is_file():
            ak = json.loads(agent_cfg_path.read_text()).get("api_key", "") or ""
    except Exception:
        ak = ""
    if not ak:
        print("✗ agent api_key 未找到(需 systems/{sid}/{agent}/agentmail.json)")
        return 1

    edition = _sw._detect_edition(gw_url)
    print(f"  Gateway:     {gw_url}")
    print(f"  Edition:     {edition}({'auth.local 认证' if edition == 'advanced' else '白名单直发'})")
    print(f"  From:        {manager}")
    print(f"  To:          {recipient}")
    print(f"  Subject:     {SUBJECT}")

    resp = _sw._smtp_send(gw_url, ak, recipient, manager, edition, SUBJECT, BODY)
    # base 版回落:auth.local 前缀会被当普通发件人拒(550),回落 manager 直发
    if not resp.startswith("250") and edition == "advanced":
        print(f"  ⚠ auth.local 发送失败({resp[:50]}),回落 base 白名单直发")
        resp = _sw._smtp_send(gw_url, ak, recipient, manager, "base", SUBJECT, BODY)
    if not resp.startswith("250"):
        print(f"✗ SMTP send failed: {resp}")
        return 1
    print("  ✓ 'update persona' email sent via SMTP (agent 将归纳 draft 并回复 manager)")

    if args.no_wait:
        return 0

    ok, email_id, _to = _sw._poll_reply(recipient, args.timeout)
    if ok:
        print(f"  ✓ Agent draft reply received (email_id={email_id or '?'})")
        return 0
    print(f"  ✗ No reply within {args.timeout}s (log: {_sw._agent_log_path(recipient)})")
    return 1


if __name__ == "__main__":
    sys.exit(main())

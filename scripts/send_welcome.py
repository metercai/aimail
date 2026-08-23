#!/usr/bin/env python3
"""send_welcome.py — 通用欢迎邮件验证工具(各 agent 系统通用)。

与 ping_test.py 同一设计:SMTP 入站发送欢迎邮件,验证投递+回复。
自动识别 gateway 版本并选择入站方式:
  advanced (/health version 含 "advanced-"):auth.local 认证发送
    (base64(admin_key)=encoded_manager@auth.local)——三层校验后绕过
    SPF/白名单(认证即信任)。
  base:普通 MAIL FROM:<manager> 发送——依赖 manager 地址自动加白
    (register_address 自动写白名单,无需认证)。

用法:
  python3 send_welcome.py [--system-id SID] [--agent-home DIR]
                          [--agent ADDR] [--to ADDR] [--manager ADDR]
  --agent-home: agent 系统 home(Hermes=~/.hermes,OpenClaw=~/.openclaw);
               指针文件 {agent-home}/.agentmail 提供 system_id/email
  --agent:      agent 标识(定位 mail 目录,默认从指针 email)
  --to:         直接指定收件地址(优先于 --agent/指针)
  --manager:    发件人(manager)地址,默认 config.manager_address
  --timeout:    等待回复秒数(默认 120)
  --no-wait:    发送后不等待回复,直接退出
退出码: 0=成功, 1=失败
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import sys
import time
import urllib.request
import uuid
from pathlib import Path

# ── 共享核心复用(带降级):email_for_agent 地址派生规则单源 ──────
# 优先 import tools/aimail_base 的共享实现;不可用时(如纯离线
# 环境)复刻同一规则,保证共享域/非共享域行为一致。
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from runtime_core import load_core  # noqa: E402
load_core()
try:
    from aimail_base import email_for_agent  # noqa: E402
except Exception:
    def email_for_agent(agent_id: str, domain: str, system_name: str = "",
                        default_aliases: tuple = ("default",)) -> str:
        """复刻 aimail_base.email_for_agent(仅主 agent 场景)。"""
        base = "agent" if agent_id in default_aliases else agent_id
        base = re.sub(r"[^A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]", "_", base) or "agent"
        if system_name:
            return f"{base}.{system_name}@{domain}"
        return f"{base}@{domain}"

def _main_agent_email(cfg: dict) -> str:
    """主 agent 地址,自适应共享域/非共享域:
    共享域(system_name 非空) → agent.{system_name}@{domain}
    非共享域(system_name 空)   → agent@{domain}
    (Hermes 默认 agent_id=default,OpenClaw 默认 agent_id=main,均归一 agent)"""
    return email_for_agent("default", cfg.get("domain", ""),
                           cfg.get("system_name", ""))

AGENTMAIL_HOME = Path.home() / ".agentmail"
SYSTEMS_DIR = AGENTMAIL_HOME / "systems"


def _clean_agent_dir_name(addr: str) -> str:
    """agent 地址 → 目录名(与 tools/aimail_base._clean_agent_dir_name 一致)。"""
    return re.sub(r"[^\w.\-]", "_", addr)


def _smtp_cmd(s: socket.socket, c: str) -> str:
    """发送 SMTP 命令并完整读取多行响应。

    响应可能粘包(如 '250-server...\\r\\n250 8BITMIME' 一条 recv)。
    按行拆分,末行 'NNN '(第 4 字符非 '-')即响应完成。
    """
    s.sendall(f"{c}\r\n".encode())
    all_lines: list = []
    while True:
        chunk = s.recv(4096).decode(errors="replace")
        if not chunk:
            break
        all_lines.extend(chunk.splitlines())
        last = all_lines[-1] if all_lines else ""
        if len(last) < 4 or last[3] != "-":
            break
    return " | ".join(l.strip() for l in all_lines)


def _detect_edition(gateway_url: str) -> str:
    """GET /health → version → 'advanced' | 'base'。失败默认 base。"""
    try:
        with urllib.request.urlopen(f"{gateway_url.rstrip('/')}/health", timeout=10) as r:
            data = json.loads(r.read())
        ver = data.get("version", "")
        return "advanced" if "advanced-" in ver else "base"
    except Exception:
        return "base"


def _smtp_send(gateway_url: str, admin_key: str, agent_email: str,
               manager: str, edition: str, subject: str, body: str) -> str:
    """SMTP 发送。edition=advanced 用 auth.local 认证;base 用普通发件人。"""
    host = gateway_url.replace("https://", "").replace("http://", "").split("/")[0]
    port = 25

    if edition == "advanced":
        # auth.local 认证:网关要求 key 的 scope 含 system/platform
        # (advanced/strategy.rs resolve_sender)——agent scope 会被拒。
        # 因此这里必须用系统 admin_key,不能用 agent api_key。
        key_bytes = bytes.fromhex(admin_key)
        b64_key = base64.b64encode(key_bytes).decode().rstrip("=")
        encoded_manager = manager.replace("@", "=")
        mail_from = f"{b64_key}={encoded_manager}@auth.local"
    else:
        # base:普通发件人(manager 已由 register_address 自动加白)
        mail_from = manager

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(15)
    try:
        s.connect((host, port))
        banner = s.recv(4096).decode(errors="replace").strip()
        if not banner.startswith("220"):
            return f"SMTP banner failed: {banner}"
        _smtp_cmd(s, "EHLO amail-welcome")
        resp = _smtp_cmd(s, f"MAIL FROM:<{mail_from}>")
        if not resp.startswith("250"):
            return f"MAIL FROM failed: {resp}"
        resp = _smtp_cmd(s, f"RCPT TO:<{agent_email}>")
        if not resp.startswith("250"):
            return f"RCPT TO failed: {resp}"
        resp = _smtp_cmd(s, "DATA")
        if not resp.startswith("354"):
            return f"DATA failed: {resp}"
        s.sendall(body.replace("\n", "\r\n").encode())
        if not body.endswith("\n"):
            s.sendall(b"\r\n")
        s.sendall(b".\r\n")
        return _smtp_cmd(s, "")
    finally:
        try:
            s.sendall(b"QUIT\r\n")
        except Exception:
            pass
        s.close()


def _agent_log_path(agent_email: str) -> str:
    """Per-agent processing log: ~/.agentmail/logs/agentmail.{cleaned_addr}.log."""
    cleaned = re.sub(r"[^\w.\-]", "_", agent_email)
    return os.path.expanduser(f"~/.agentmail/logs/agentmail.{cleaned}.log")


def _poll_reply(agent_email: str, timeout_secs: int) -> tuple:
    """轮询 agent 侧 agentmail.log,直到 welcome 之后出现新 outbound 记录(agent 回复)。

    取代旧 stats API 轮询:/api/v1/stats/agent/me 是 advanced 独有端点,
    且 agent 级 key 查他人 stats 必 403(scope 先判 agent)→ 旧逻辑静默
    返回空 dict,sent/received 恒 0。send_mail 成功后共享核心写
    dir=outbound(含 email_id)到每个 agent 独立的 agentmail.log,
    各 agent 类型通用。返回 (ok, email_id, to)。
    """
    log_path = _agent_log_path(agent_email)
    baseline = 0
    if os.path.isfile(log_path):
        with open(log_path, encoding="utf-8") as f:
            baseline = sum(1 for _ in f)
    print(f"  Polling reply from log: {log_path} (baseline {baseline} lines)")

    start = time.time()
    while time.time() - start < timeout_secs:
        time.sleep(5)
        try:
            with open(log_path, encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            continue
        for ln in lines[baseline:]:
            try:
                e = json.loads(ln)
            except Exception:
                continue
            if e.get("dir") == "outbound":
                eid = e.get("email_id", "")
                print(f"  ✓ Agent replied (outbound logged, email_id={eid or '?'})")
                return True, eid, e.get("to", "")
    print(f"  ⚠ Timeout — no outbound reply in log within {timeout_secs}s")
    return False, "", ""


def main() -> int:
    ap = argparse.ArgumentParser(description="欢迎邮件验证工具(共享,自动识别 gateway 版本)")
    ap.add_argument("--system-id", default="")
    ap.add_argument("--agent-home", default="",
                    help="agent 系统 home(Hermes=~/.hermes, OpenClaw=~/.openclaw)")
    ap.add_argument("--agent", default="", help="agent 标识(定位 mail 目录/地址)")
    ap.add_argument("--to", default="", help="直接指定收件地址(优先)")
    ap.add_argument("--manager", default="", help="发件人(manager)地址,默认 config.manager_address")
    ap.add_argument("--timeout", type=int, default=120, help="等待回复秒数")
    ap.add_argument("--no-wait", action="store_true", help="发送后不等待回复")
    args = ap.parse_args()

    agent_home = Path(args.agent_home or os.environ.get("AGENT_HOME", str(Path.home() / ".hermes")))

    # ── 解析系统身份:--to > --agent > 指针 > env ──
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

    # ── 读 gateway 配置 ──
    config_path = SYSTEMS_DIR / sid / "agentmail_gateway.json"
    if not config_path.exists():
        print(f"✗ agentmail_gateway.json not found: {config_path}")
        return 1
    cfg = json.loads(config_path.read_text())
    gw_url = cfg.get("gateway_url", "")
    manager = args.manager or os.environ.get("AIMAIL_MANAGER_ADDRESS") \
        or os.environ.get("MANAGER") or cfg.get("manager_address", "")

    # 收件地址:--to > --agent > 指针 email > config 派生
    recipient = args.to or email
    if not recipient:
        # 主 agent 地址,自适应共享域/非共享域(见 _main_agent_email)
        recipient = _main_agent_email(cfg)

    # ── SMTP auth.local 认证 key:只用 agent 的 api_key ─────────────
    # auth.local 模拟 manager 发信与 agent 一对一,必须用 agent 自己的
    # api_key(最小权限,无 admin_key 回退——回退会破坏 1:1 语义)。
    # agent api_key 是 64 位 hex(bytes.fromhex 兼容)。
    ak = ""
    try:
        agent_cfg_path = SYSTEMS_DIR / sid / _clean_agent_dir_name(recipient) / "agentmail.json"
        if agent_cfg_path.is_file():
            _acfg = json.loads(agent_cfg_path.read_text())
            ak = _acfg.get("api_key", "") or ""
    except Exception:
        ak = ""
    if not ak:
        print("✗ agent api_key 未找到(需 systems/{sid}/{agent}/agentmail.json)——auth.local 只接受 agent key")
        return 1

    if not manager:
        print("✗ 无 manager 地址(需 --manager 或 config.manager_address)")
        return 1
    if not all([gw_url, ak]):
        print("✗ Missing gateway_url/agent api_key")
        return 1

    # ── 识别 gateway 版本 → 选择 SMTP 入站方式 ──
    edition = _detect_edition(gw_url)
    print(f"  Gateway:     {gw_url}")
    print(f"  Edition:     {edition}({'auth.local 认证' if edition == 'advanced' else '白名单直发'})")
    print(f"  To:          {recipient}")
    print(f"  Manager:     {manager}")

    msg_id = f"<welcome-{int(time.time())}-{uuid.uuid4().hex[:4]}@amail>"
    body = f"""From: {manager}
To: {recipient}
Message-ID: {msg_id}
Subject: Welcome! Your amail integration is live

Hello! This is your first email delivered through your new amail system.

Please reply with the current server time to confirm the mail loop is working.

--
This confirms: ✓ SMTP inbound  ✓ Webhook delivery  ✓ Agent processing  ✓ Outbound reply
"""

    resp = _smtp_send(gw_url, ak, recipient, manager, edition, "Welcome!", body)
    # base 版回落:auth.local 前缀会被当普通发件人拒(550),回落 manager 直发
    if not resp.startswith("250") and edition == "advanced":
        print(f"  ⚠ auth.local 发送失败({resp[:50]}),回落 base 白名单直发")
        resp = _smtp_send(gw_url, ak, recipient, manager, "base", "Welcome!", body)
    if not resp.startswith("250"):
        print(f"✗ SMTP send failed: {resp}")
        return 1
    print("  ✓ Welcome email sent via SMTP")

    if args.no_wait:
        return 0

    ok, email_id, _to = _poll_reply(recipient, args.timeout)
    if ok:
        print(f"  ✓ Bidirectional send/receive verified (email_id={email_id or '?'})")
        return 0
    print(f"  ✗ No reply within {args.timeout}s (log: {_agent_log_path(recipient)})")
    return 1


if __name__ == "__main__":
    sys.exit(main())

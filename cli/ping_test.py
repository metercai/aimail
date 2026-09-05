#!/usr/bin/env python3
"""ping_test.py — 独立共享 Ping-Pong End-to-End 测试(各 agent 系统通用)。

从 check_status.py 的 _run_ping_test 与 verify-e2e.py 独立出来。与
send_welcome.py 同一机制:SMTP auth 入站(auth.local 认证 FROM)发送
__aimail_ping__ 邮件,验证全链路后 pong 回发。

Ping/Pong 语义:ping 邮件走完全部中间链(富化/附件/存储)才在调用
agent 前最后一刻被吞;pong 只在全链路正常时回复 —— 测试通过 =
进入 agent 之前的所有处理正常。

判定口径(用户定调 2026-08-16):**只信 agent 侧 aimail.log 的
三阶段事件** —— ping_intercepted → pong_sent → pong_returned。
这是 agent 预处理链真实执行 + pong 回环的唯一权威证据,与入站
投递方式(push 直推 / pull 经 bridge 拉取)无关——无论哪种方式,
邮件最终都到达 agent 侧接收端触发拦截,三阶段事件必然产生。
不依赖云端 pending 队列观测(那只是外围证据,不证明 agent 真正
处理),因此无 mode 参数可选。

用法:
  python3 ping_test.py [--system-id SID] [--agent-home DIR] [--timeout 120]
  --agent-home:  agent 系统 home(Hermes=~/.hermes,OpenClaw=~/.openclaw)
                 指针文件 {agent-home}/.agentmail 提供 system_id/email
  --agent:       可选的 agent 标识(定位 mail 目录,默认从指针 email)
  --timeout:     等待处理的秒数(默认 120)
  --no-snapshot: 跳过原始邮件快照检查
退出码: 0=通过, 1=失败
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── 共享核心复用(带降级):email_for_agent 地址派生规则单源 ──────
# 优先 import pysdk/aimail_base 的共享实现;不可用时(如纯离线
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

# ── 路径 ──────────────────────────────────────────────────────────
# 与 aimail_base.aimail_home() 同语义:空 env 回退 ~/.aimail。
# (旧写法 Path("")=PosixPath('.') 恒真,or 回退永不生效——bug。)
_AH_ENV = os.environ.get("AIMAIL_HOME", "")
AIMAIL_HOME = Path(_AH_ENV).expanduser() if _AH_ENV else Path.home() / ".aimail"
SYSTEMS_DIR = AIMAIL_HOME / "systems"
MAIL_DIR = AIMAIL_HOME / "mail"

PING_PREFIX = "__aimail_ping__:"


def _clean_agent_dir_name(addr: str) -> str:
    """agent 地址 → 目录名(与 pysdk/aimail_base._clean_agent_dir_name 一致)。"""
    return re.sub(r"[^\w.\-]", "_", addr, flags=re.ASCII)


def _smtp_cmd(s: socket.socket, c: str) -> str:
    """发送 SMTP 命令并完整读取多行响应。

    响应可能是多条独立 recv 包,也可能一条包含全部行
    (如 '250-server...\\r\\n250 8BITMIME' 粘包)。按行拆分后逐行
    判定:行首 'NNN-' 表示还有后续行,'NNN ' 是末行。
    """
    s.sendall(f"{c}\r\n".encode())
    all_lines: list = []
    while True:
        chunk = s.recv(4096).decode(errors="replace")
        if not chunk:
            break
        all_lines.extend(chunk.splitlines())
        # 末行 'NNN ' 或 'NNN'(第 4 字符非 '-')即响应完成。
        # 粘包时整条含多行,只有拆行后的最后一行能决定是否结束。
        last = all_lines[-1] if all_lines else ""
        if len(last) < 4 or last[3] != "-":
            break
    return " | ".join(l.strip() for l in all_lines)


def _smtp_send_ping(gw_url: str, admin_key: str, email: str,
                    manager: str, ping_id: str, edition: str = "advanced") -> str:
    """SMTP auth 发送 ping(与 send_welcome.py 同机制)。返回 DATA end 响应。

    edition=advanced:auth.local 认证发送(base64(admin_key)=manager@auth.local)。
    edition=base:普通 MAIL FROM:<manager>(manager 已自动加白)。
    """
    host = gw_url.replace("https://", "").replace("http://", "").split("/")[0]
    if edition == "advanced":
        # auth.local 认证:网关要求 key 的 scope 含 system/platform
        # (advanced/strategy.rs resolve_sender)——agent scope 会被拒。
        # 因此这里必须用系统 admin_key,不能用 agent api_key。
        key_bytes = bytes.fromhex(admin_key)
        b64_key = base64.b64encode(key_bytes).decode().rstrip("=")
        encoded_manager = manager.replace("@", "=")
        auth_from = f"{b64_key}={encoded_manager}@auth.local"
    else:
        auth_from = manager

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(15)
    s.connect((host, 25))
    try:
        # Banner: read directly (no command sent)
        banner = s.recv(4096).decode(errors="replace").strip()
        if not banner.startswith("220"):
            return f"SMTP banner failed: {banner}"
        resp = _smtp_cmd(s, "EHLO amail-ping-test")
        # 生产网关对 auth.local 发件人强制 STARTTLS(550 ... requires TLS);
        # 服务器通告 STARTTLS 则升级 TLS,升级后按 RFC 3207 重新 EHLO
        # (会话扩展在 STARTTLS 后重置)。证书校验严格(网关证书 ACME 管理)。
        if "STARTTLS" in resp.upper():
            resp = _smtp_cmd(s, "STARTTLS")
            if not resp.startswith("220"):
                return f"STARTTLS failed: {resp}"
            try:
                s = ssl.create_default_context().wrap_socket(s, server_hostname=host)
            except ssl.SSLError as e:
                return f"STARTTLS TLS handshake failed: {e}"
            _smtp_cmd(s, "EHLO amail-ping-test")
        resp = _smtp_cmd(s, f"MAIL FROM:<{auth_from}>")
        if not resp.startswith("250"):
            return f"MAIL FROM failed: {resp}"
        resp = _smtp_cmd(s, f"RCPT TO:<{email}>")
        if not resp.startswith("250"):
            return f"RCPT TO failed: {resp}"
        resp = _smtp_cmd(s, "DATA")
        if not resp.startswith("354"):
            return f"DATA failed: {resp}"
        body = (f"From: {manager}\nTo: {email}\n"
                f"Subject: {PING_PREFIX}{ping_id}\n"
                f"Message-ID: <ping-{ping_id}@amail.token.tm>\n"
                f"\nPing test message\n")
        s.sendall(body.replace("\n", "\r\n").encode())
        s.sendall(b".\r\n")
        return _smtp_cmd(s, "")
    finally:
        try:
            s.sendall(b"QUIT\r\n")
        except Exception:
            pass
        s.close()


def _detect_edition(gateway_url: str) -> str:
    """GET /health → version → 'advanced' | 'base'。失败默认 advanced
    (auth.local 认证发送;若 base 版返回 550 可 --mode 不强求,由
    base 版白名单直发兜底)。"""
    try:
        with urllib.request.urlopen(f"{gateway_url.rstrip('/')}/health", timeout=10) as r:
            data = json.loads(r.read())
        ver = data.get("version", "")
        return "advanced" if "advanced-" in ver else "base"
    except Exception:
        return "advanced"


def main() -> int:
    ap = argparse.ArgumentParser(description="Ping-Pong End-to-End Test(共享)")
    ap.add_argument("--system-id", default="")
    ap.add_argument("--agent-home", default="",
                    help="agent 系统 home(Hermes=~/.hermes, OpenClaw=~/.openclaw)")
    ap.add_argument("--agent", default="", help="agent 标识(定位 mail 目录)")
    ap.add_argument("--manager", default="", help="发件人(manager)地址,默认 config.manager_address")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--no-snapshot", action="store_true")
    args = ap.parse_args()

    # ── 解析系统身份(平台无关默认链,与 send_welcome 一致)──
    sid = args.system_id
    email = args.agent
    agent_home = (Path(args.agent_home).expanduser() if args.agent_home
                  else (Path(os.environ["AGENT_HOME"]).expanduser()
                        if os.environ.get("AGENT_HOME") else None))
    if agent_home is not None:
        pointer = agent_home / ".agentmail"
        if pointer.is_file():
            try:
                pd = json.loads(pointer.read_text())
                sid = sid or pd.get("system_id", "")
                email = email or pd.get("email", "")
            except Exception:
                pass

    if not sid:
        from runtime_core import resolve_system_id as _resolve_sid
        sid = _resolve_sid(sid, str(agent_home) if agent_home else "")

    if not sid:
        print("✗ system_id 未解析(需 --system-id,或本机单系统/平台指针可自动判定)")
        return 1

    # ── 读 gateway 配置 ──
    config_path = SYSTEMS_DIR / sid / "aimail_gateway.json"
    if not config_path.exists():
        print(f"✗ aimail_gateway.json not found: {config_path}")
        return 1
    cfg = json.loads(config_path.read_text())
    gw_url = cfg.get("gateway_url", "")
    if not email:
        # 主 agent 地址,自适应共享域/非共享域(见 _main_agent_email)
        email = _main_agent_email(cfg)
    manager = args.manager or cfg.get("manager_address", "")

    # ── SMTP auth.local 认证 key:只用 agent 的 api_key ─────────────
    # auth.local 模拟 manager 发信与 agent 一对一,必须用 agent 自己的
    # api_key(最小权限,无 admin_key 回退——回退会破坏 1:1 语义)。
    # agent api_key 是 64 位 hex(bytes.fromhex 兼容)。
    ak = ""
    try:
        agent_cfg_path = SYSTEMS_DIR / sid / _clean_agent_dir_name(email) / "agentmail.json"
        if agent_cfg_path.is_file():
            _acfg = json.loads(agent_cfg_path.read_text())
            ak = _acfg.get("api_key", "") or ""
    except Exception:
        ak = ""
    if not ak:
        print("✗ agent api_key 未找到(需 systems/{sid}/{agent}/agentmail.json)——auth.local 只接受 agent key")
        return 1

    if not all([gw_url, ak, email, manager]):
        print("✗ Missing required config fields(gateway_url/admin_key/email/manager)")
        return 1

    mail_dir = MAIL_DIR / _clean_agent_dir_name(email)          # 快照目录(mail 数据)
    amail_log = AIMAIL_HOME / "logs" / f"aimail.{_clean_agent_dir_name(email)}.log"

    # ── 识别 gateway 版本 → 选择 SMTP 入站方式 ──
    edition = _detect_edition(gw_url)
    ping_id = uuid.uuid4().hex[:12]

    # ── SMTP auth 发送(带 base 回落) ──
    print(f"  edition={edition} system_id={sid} email={email}")
    t_sent = time.time()
    resp = _smtp_send_ping(gw_url, ak, email, manager, ping_id, edition)
    # base 版回落:auth.local 前缀会被当普通发件人拒(550),回落 manager 直发
    if not resp.startswith("250") and edition == "advanced":
        print(f"  ⚠ auth.local 发送失败({resp[:50]}),回落 base 白名单直发")
        resp = _smtp_send_ping(gw_url, ak, email, manager, ping_id, "base")
    if not resp.startswith("250"):
        print(f"✗ SMTP ping send failed: {resp}")
        return 1
    print(f"  Ping sent: {PING_PREFIX}{ping_id}")
    dt_sent = datetime.fromtimestamp(t_sent, tz=timezone.utc)

    deadline = time.time() + args.timeout
    found_ping = found_pong = found_sent = False

    def _parse_ts(s: str):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(s[:26], fmt[:len(s[:26])])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
        return None

    def _fmt_secs(ts_str: str, t0: datetime) -> float:
        dt = _parse_ts(ts_str)
        return (dt - t0).total_seconds() if dt else 0.0

    while time.time() < deadline:
        # ── 三阶段事件:aimail.log(唯一权威判定,用户定调)──
        if amail_log.exists():
            for line in reversed(amail_log.read_text().splitlines()):
                if ping_id not in line:
                    continue
                try:
                    entry = json.loads(line)
                    d = entry.get("dir", "")
                    ts = entry.get("ts", "")
                    if d == "ping_intercepted" and not found_ping:
                        found_ping = True
                        print(f"  +{_fmt_secs(ts, dt_sent):5.1f}s    Webhook Receive (ping)         ✓")
                    if d == "pong_sent" and found_ping and not found_sent:
                        found_sent = True
                        print(f"  +{_fmt_secs(ts, dt_sent):5.1f}s    Pong Sent (send_mail)          ✓")
                    if d == "pong_returned" and found_ping and not found_pong:
                        found_pong = True
                        print(f"  +{_fmt_secs(ts, dt_sent):5.1f}s    Webhook Return (pong)          ✓")
                        print(f"  +{_fmt_secs(ts, dt_sent):5.1f}s    Total round-trip: {_fmt_secs(ts, dt_sent):.1f}s")
                except Exception:
                    pass

        if found_ping and found_pong:
            break
        time.sleep(3)

    # ── 结果判定:只信三阶段日志事件(用户定调 2026-08-16)──
    if found_ping and found_pong:
        print(f"  ✓ Full pipeline verified — ping intercepted & pong returned (ping_id={ping_id})")
        result_ok = True
    elif found_ping:
        print(f"  ✗ Ping intercepted, but pong not returned within {args.timeout}s")
        result_ok = False
    else:
        print(f"  ✗ No ping/pong events in {amail_log} within {args.timeout}s")
        result_ok = False
    if not result_ok:
        return 1

    # ── 原始邮件快照检查 ──
    if not args.no_snapshot:
        snap_ok = 0
        snap_total = 0
        if mail_dir.exists():
            now_ts = time.time()
            for entry in mail_dir.rglob("*"):
                if entry.is_file():
                    snap_total += 1
                    if now_ts - entry.stat().st_mtime < 300:
                        snap_ok += 1
        if snap_ok > 0:
            print(f"  ✓ Snapshots: {snap_ok} new file(s) in mail/{_clean_agent_dir_name(email)}/ (total {snap_total})")
        else:
            print(f"  ⚠ Snapshots: {snap_total} total file(s), none from last 5min")

    return 0


if __name__ == "__main__":
    sys.exit(main())

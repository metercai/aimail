"""CLI 内部共享工具（P4 收口：消除各模块复制的同名 helper）。

边界说明（重要）：
- 本模块只服务 `cli/` 下的模块（它们以**扁平导入**互相引用，例如 repair.py 的
  `import runtime_bundle`），因此这里也走扁平导入：`from _common import ...`。
- **不**反向依赖 `pysdk`/`aimailsdk`：CLI 自带运行时必须能独立工作；SDK 若在
  sys.path 上（venv/宿主环境），下面的 `aimail_home()` 会优先用它，否则走本模块回退。
- 只有"实现完全一致或语义可参数化"的函数才提到这里；耦合调用方局部闭包的
  （如 `_main_agent_email` 依赖各模块的 `email_for_agent`）留在原处，避免制造循环依赖。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import os
import re
import socket
import urllib.request
from pathlib import Path


def aimail_home() -> Path:
    """程序根：优先用 SDK 的单一实现（aimail_base.aimail_home），否则按同一公式回退。

    回退优先级：$AIMAIL_HOME → ~/.aimail（与 pysdk/aimail_base.aimail_home 一致）。
    """
    try:
        from aimail_base import aimail_home as _sdk_home  # noqa: E402
        return Path(_sdk_home())
    except Exception:
        env = os.environ.get("AIMAIL_HOME", "")
        return Path(env).expanduser() if env else Path.home() / ".aimail"


def clean_agent_dir_name(addr: str) -> str:
    """agent 地址 → 目录名（与 pysdk/aimail_base._clean_agent_dir_name 一致）。"""
    return re.sub(r"[^\w.\-]", "_", addr, flags=re.ASCII)


# ── 桥的生命周期契约客户端(P2, 2026-09-20) ─────────────────────────────
# 契约(aimail-bridge >= 0.7.4): --status [--json] / --stop --pid-file <p> / --check-config
# 退出码: 0 成功|运行中 · 1 错误|拒绝(pid 不属桥) · 2 配置非法 · 3 未运行
# 不支持的旧桥: 回退到"pid 文件 + 存活探测"(并告警), CLI 侧不再自行 kill 进程。

def bridge_ctl_supported(bin_path: str) -> bool:
    """桥二进制是否支持生命周期契约。"""
    try:
        r = subprocess.run([bin_path, "--status", "--json"], capture_output=True, text=True, timeout=10)
        return r.returncode in (0, 3) and '"running"' in (r.stdout or "")
    except Exception:
        return False


def bridge_status(bin_path: str, pid_path: str) -> dict:
    """桥运行状态。契约优先; 旧桥回退为 pid 文件 + 存活探测。"""
    if bridge_ctl_supported(bin_path):
        try:
            r = subprocess.run([bin_path, "--status", "--json", "--pid-file", pid_path],
                               capture_output=True, text=True, timeout=10)
            out = (r.stdout or "").strip().splitlines()
            d = json.loads(out[-1]) if out else {}
            d.setdefault("running", False)
            d["_via"] = "bridge-ctl"
            return d
        except Exception as e:
            return {"running": False, "reason": f"status-failed: {e}", "_via": "bridge-ctl"}
    pid = _read_pid_file(pid_path)
    return {"running": bool(pid and pid_alive(pid)), "pid": pid,
            "reason": "legacy-pid-check", "_via": "pid-file"}


def bridge_stop(bin_path: str, pid_path: str) -> int:
    """停止桥。契约优先(含身份校验, 非桥拒绝); 旧桥回退 kill 15→9。"""
    if bridge_ctl_supported(bin_path):
        try:
            r = subprocess.run([bin_path, "--stop", "--pid-file", pid_path],
                               capture_output=True, text=True, timeout=40)
            for line in (r.stderr or "").strip().splitlines()[-2:]:
                if line.strip():
                    print(f"    {line.strip()}")
            return r.returncode
        except Exception as e:
            print(f"    stop failed: {e}")
            return 1
    pid = _read_pid_file(pid_path)
    if not pid or not pid_alive(pid):
        return 0
    print("    (旧桥无契约能力 → 回退 TERM/KILL; 建议升级桥到 >= 0.7.4)")
    try:
        os.kill(pid, 15)
    except OSError:
        pass
    time.sleep(1)
    if pid_alive(pid):
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    return 0


def _read_pid_file(pid_path: str):
    try:
        with open(pid_path) as f:
            return int(f.read().strip())
    except Exception:
        return None


def pid_alive(pid: int) -> bool:
    """进程存活探测(跨平台: unix=signal 0; windows=tasklist)。"""
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, timeout=10)
            return str(pid) in (r.stdout or "")
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_readable_file(p) -> bool:
    """True if p is a readable regular file —— 权限/IO 错误一律视为"不存在"。

    注意：直接用 `Path.is_file()` 在目录无 x 权限时会抛 PermissionError/OSError，
    调用方若没包 try 就会把整个循环打断（repair 阶梯 8 曾因此整步中断）。
    """
    try:
        return p.is_file()
    except OSError:
        return False


def smtp_cmd(s: socket.socket, c: str) -> str:
    """发送 SMTP 命令并完整读取多行响应。

    响应可能是多条独立 recv 包，也可能一条包含全部行（如
    '250-server...\\r\\n250 8BITMIME' 粘包）。按行拆分后逐行判定：
    行首 'NNN-' 表示还有后续行，'NNN '（第 4 字符非 '-'）是末行。
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


def detect_edition(gateway_url: str, default: str = "base") -> str:
    """GET /health → version → 'advanced' | 'base'。

    default：探测失败时的兜底（调用方语义不同，必须显式给）——
    ping_test 用 "advanced"（按 auth.local 认证发送，失败由 base 版白名单直发兜底），
    send_welcome 用 "base"。
    """
    try:
        with urllib.request.urlopen(f"{gateway_url.rstrip('/')}/health", timeout=10) as r:
            data = json.loads(r.read())
        ver = data.get("version", "")
        return "advanced" if "advanced-" in ver else "base"
    except Exception:
        return default

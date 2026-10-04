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
import sys
import time
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


_NO_PGREP_WARNED: list = []   # one-shot 提示开关(模块级: 多个调用点只提示一次)


def pids_by_pattern(pattern: str) -> list[int]:
    """按正则匹配进程 cmdline, 返回 PID 列表。

    首选 `pgrep -f <pattern>`; 宿主没装 procps(pgrep) 时**回退扫 /proc/<pid>/cmdline**,
    用的是同一个正则 ⇒ "精确匹配、不误杀"的口径不变。

    F12(2026-09-25, CLI 门禁 L2 真宿主层抓到): deerflow 宿主镜像不带 procps, 原实现
    只捕 CalledProcessError ⇒ `subprocess.check_output` 抛的 FileNotFoundError 一路冒泡,
    `aimail bridge` 整条命令带 traceback 崩掉。

    调用方必须给**锚定可执行文件**的正则(如 `^[^ ]*/aimail-bridge( |$)`); 这里不做
    任何宽松匹配, 避免误杀(2026-08-16/09-21 两次实测事故的口径)。
    """
    try:
        out = subprocess.check_output(["pgrep", "-f", pattern], text=True, timeout=5)
        return [int(l.strip()) for l in out.splitlines() if l.strip().isdigit()]
    except subprocess.CalledProcessError:
        return []
    except (FileNotFoundError, OSError):
        pass  # 无 pgrep/不可执行 → /proc 回退
    if not _NO_PGREP_WARNED:
        _NO_PGREP_WARNED.append(True)
        print("  ⚠ 宿主无 pgrep(未装 procps)——回退扫描 /proc/<pid>/cmdline")
    try:
        names = os.listdir("/proc")
    except OSError:
        return []
    pat = re.compile(pattern)
    pids: list[int] = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if pat.search(cmd):
            pids.append(int(name))
    return pids


# ── SDK 侧可执行门（进程命令契约）──────────────────────────────────────────
# 2026-10-04 owner 裁决 A'：SDK 域算法（绑定枚举 / secret 自供 / 注册值派生 / 注册链 / 回填）
# 的单一真源在 pysdk，调用方只许"调"不许"抄"。TS 侧的平台包早已是此形态（register-cli.js）；
# Python 侧的对等门 = `python -m aimail.sdk_ops`（stdout 恰一行 JSON / exit 0|1|2）。
# 本函数只负责"怎么调"：定位门 → spawn → 解析信封 → 按 kind 分档复原失败语义。
_EXC_CACHE: dict = {}


def _sdk_call_error(name: str, message: str) -> BaseException:
    """按门上报的异常类名复原一个同名异常（调用方 `type(e).__name__` 与 Python 侧一致）。"""
    cls = _EXC_CACHE.get(name)
    if cls is None:
        cls = type(name, (RuntimeError,), {})
        _EXC_CACHE[name] = cls
    return cls(message)


def _sdk_ops_command(op: str, args: dict) -> list:
    """门的 argv。**同源优先**：核心目录里有 sdk_ops.py 就直接跑它（调用方与被调方同一棵树，
    见 install.py 的混装教训）；否则回退 pip 装的 `-m aimail.sdk_ops`（aimailsdk ≥ 0.1.35）。"""
    argv = [sys.executable]
    try:
        from runtime_core import resolve_core_dir  # 扁平导入：仅 CLI 内部
        door = os.path.join(str(resolve_core_dir()), "sdk_ops.py")
        argv.append(door) if os.path.isfile(door) else argv.extend(["-m", "aimail.sdk_ops"])
    except Exception:  # noqa: BLE001 — 核心目录不可解析 ⇒ 交给门自身报 import 错
        argv.extend(["-m", "aimail.sdk_ops"])
    argv += [op, "--args", json.dumps(args, ensure_ascii=False)]
    return argv


def sdk_ops(op: str, args: dict, *, timeout: int = 120):
    """调用 SDK 侧可执行门的一个 op，返回 result。

    - `kind=usage` / `kind=import`（用法错 / SDK 不可用或版本过老）⇒ `SystemExit`：与既有
      `load_core()` 的失败形态一致（CLI 直接退出并打印原因，不静默降级）。
    - `kind=call` ⇒ 抛**同名**异常（如缺 manager 的 `ManagerRequiredError`），消息取门上报的
      error 去掉 'ExcName: ' 前缀 —— 调用方的 `({type(e).__name__}: {e})` 文案因此逐字不变。
    """
    argv = _sdk_ops_command(op, args)
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise SystemExit(f"ERROR: SDK 可执行门不可用({op}): {e}") from None
    except subprocess.TimeoutExpired:
        raise SystemExit(f"ERROR: SDK 可执行门超时({op}, {timeout}s)") from None
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    if len(lines) != 1:
        raise SystemExit(f"ERROR: SDK 可执行门输出异常({op}, rc={r.returncode}, "
                         f"{len(lines)} 行): {(r.stdout or '')[:200]}{(r.stderr or '')[:200]}")
    try:
        env = json.loads(lines[0])
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"ERROR: SDK 可执行门输出不是 JSON({op}): {e}") from None
    if env.get("ok") is True:
        return env.get("result")
    kind = str(env.get("kind") or "")
    exc = str(env.get("exc") or "SdkOpsError")
    err = str(env.get("error") or "")
    if kind in ("usage", "import"):
        raise SystemExit(f"ERROR: SDK 可执行门 {kind} 失败({op}): {err}")
    prefix = exc + ": "
    raise _sdk_call_error(exc, err[len(prefix):] if err.startswith(prefix) else err)


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


def is_local_gateway(url: str) -> bool:
    """True when the gateway runs on this machine / local network (direct push, no bridge).

    Single implementation for install (cli/aimail) and repair (owner ruling
    2026-09-27: repair's bridge step must judge the mode the same way install
    does). An unparsable/empty url is NOT local — callers must handle "cannot
    judge" themselves instead of assuming direct mode.

    Host extraction uses urlparse().hostname: it already strips userinfo and the
    port, and unwraps bracketed IPv6. The previous hand-rolled split left
    "[::1]:8080" as a literal and therefore called an IPv6 loopback gateway
    remote (found by tests/test_repair_bridge_mode.py, 2026-09-27).
    """
    try:
        from urllib.parse import urlparse
        host = urlparse(url if "//" in url else f"//{url}").hostname or ""
    except Exception:
        return False
    if not host:
        return False
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    try:
        # 本机所有 IP 也算本地
        for info in socket.getaddrinfo(socket.gethostname(), None):
            if info[4][0] == host:
                return True
    except Exception:
        pass
    return False

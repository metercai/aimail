#!/usr/bin/env python3
"""Deploy aimail-bridge: bridge config, startup."""
import sys, os, json, re, subprocess, socket, time

# 运行时核心(repo pysdk/ 优先 > pip aimail 兜底);维护脚本从 cli/ 调用
_SCRIPTS_DIR = str(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from runtime_core import load_core  # noqa: E402
load_core()
from gateway_api import create_api_key

# Machine home: AIMAIL_HOME env wins, else ~/.aimail (single authoritative layout).
_AM_HOME = os.environ.get("AIMAIL_HOME") or os.path.expanduser("~/.aimail")


def log_step(msg: str):
    print(f"  {msg}")

def log_ok(msg: str):
    print(f"  ✓ {msg}")

def _ensure_binary(bridge_bin: str, bridge_dir: str) -> bool:
    """Deploy the bridge binary (extract from local zip if missing). Idempotent."""
    if not os.access(bridge_bin, os.X_OK):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        bridge_dir_local = os.path.join(project_root, "bridge")
        import platform
        machine = platform.machine()
        if machine == "x86_64":
            arch = "amd64"
        elif machine in ("aarch64", "arm64"):
            arch = "arm64"
        else:
            arch = "amd64"
        zip_path = latest_bridge_zip(bridge_dir_local, arch)
        if not zip_path:
            log_warn(f"No bridge zip found in {bridge_dir_local} "
                     f"(aimail-bridge-v*-linux-{arch}.zip)")
            return False
        log_step(f"Extracting bridge from {zip_path}...")
        try:
            subprocess.run(
                ["unzip", "-o", zip_path, "-d", bridge_dir],
                capture_output=True, timeout=30)
            os.chmod(bridge_bin, 0o755)
        except Exception as e:
            log_warn(f"Failed to extract bridge: {e}")
            return False
    return os.access(bridge_bin, os.X_OK)

def _config_lines(addr: str, mode: str, merged: list, log_path: str) -> list:
    """TOML body for a single bridge serving the given system entries."""
    lines = [
        f'bind = "{addr}"',
        f'mode = "{mode}"',
        '',
        '[logging]',
        f'file = "{log_path}"',
        'level = "info"',
        '',
        '[pull]',
        '# 单 bridge 多系统:每系统一条,独立 key/system_id/dedup/backoff',
        'systems = [',
    ]
    for i, s in enumerate(merged):
        comma = ',' if i < len(merged) - 1 else ''
        parts = [
            f'amail_url = "{s["amail_url"]}"',
            f'admin_key = "{s["admin_key"]}"',
            f'system_id = "{s["system_id"]}"',
            f'poll_interval_sec = {s.get("poll_interval_sec", 2)}',
        ]
        if s.get("api_key"):
            parts.append(f'api_key = "{s["api_key"]}"')
        if s.get("webhook_secret"):
            parts.append(f'webhook_secret = "{s["webhook_secret"]}"')
        lines.append(f'  {{ {", ".join(parts)} }}{comma}')
    lines.extend([
        ']',
        '',
        '[health]',
        'check_interval_sec = 30',
        'fail_threshold = 6',
        'connect_timeout_sec = 3',
    ])
    return lines

def _version_key(name: str):
    """Extract (major, minor, patch) sort key from 'aimail-bridge-vX.Y[.Z]-...'."""
    m = re.search(r"-v(\d+)\.(\d+)(?:\.(\d+))?", name)
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))

def latest_bridge_zip(bridge_dir_local: str, arch: str) -> str:
    """Pick the newest aimail-bridge-v*-linux-{arch}.zip in the bridge dir.

    Scans actual files (not a hardcoded version) so a new release is picked
    up automatically — bumping the version only requires adding the zip.
    """
    prefix = "aimail-bridge-v"
    suffix = f"-linux-{arch}.zip"
    candidates = []
    for f in os.listdir(bridge_dir_local):
        if f.startswith(prefix) and f.endswith(suffix):
            candidates.append(f)
    if not candidates:
        return ""
    # Highest semantic version wins (v0.6.1 > v0.6)
    best = max(candidates, key=lambda f: _version_key(f))
    return os.path.join(bridge_dir_local, best)

def log_warn(msg: str):
    print(f"  ⚠ {msg}")

def detect_ip() -> str:
    """Detect best public IP. Returns IPv4 or IPv6 or 127.0.0.1."""
    try:
        out = subprocess.check_output(
            ["ip", "-4", "addr", "show", "scope", "global"], text=True, timeout=5)
        m = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', out)
        if m and m.group(1) != "127.0.0.1":
            return m.group(1)
    except: pass
    try:
        out = subprocess.check_output(
            ["ip", "-6", "addr", "show", "scope", "global"], text=True, timeout=5)
        m = re.search(r'inet6 ([\da-f:]+)', out)
        if m and "::1" not in m.group(1) and not m.group(1).startswith("fe80"):
            return m.group(1)
    except: pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except:
        return "127.0.0.1"

def format_webhook_host(ip: str) -> str:
    """Format IP as webhook_host with port."""
    if ":" in ip and "." not in ip:  # IPv6
        return f"[{ip}]:38081"
    else:
        return f"{ip}:38081"

def write_bridge_config(path: str, mode: str, addr: str, gw: str,
                        ak: str, sid: str, api_key: str = "",
                        webhook_secret: str = ""):
    """Write/merge aimail_bridge.toml — SINGLE bridge, MULTI-system.

    2026-08-16 用户定调: 本机只安装一个 bridge,不管几套 agent 系统
    (bridge 已支持多系统透传)。因此本函数**合并**而非覆盖:
      - 已有 [pull].systems 数组 → 追加/更新当前 sid 的条目,保留其他系统
      - 无 systems 数组(旧单系统格式)→ 迁移为 systems 数组(保留顶层
        字段作为兼容,resolved_systems 空数组回退单系统)
    重启由 start_bridge 幂等处理(先杀旧进程再起新,单实例)。
    """
    log_path = os.path.join(_AM_HOME, "logs/aimail-bridge.log")

    def _entry() -> dict:
        e = {
            "amail_url": gw,
            "admin_key": ak,
            "system_id": sid,
            "poll_interval_sec": 2,
        }
        if api_key:
            e["api_key"] = api_key
        if webhook_secret:
            e["webhook_secret"] = webhook_secret
        return e

    new_entry = _entry()
    existing_systems = []

    # 读取已有配置(若存在):保留其他系统的 systems 条目
    if os.path.exists(path):
        try:
            import tomllib
            with open(path, "rb") as f:
                old = tomllib.load(f)
            old_pull = old.get("pull", {})
            old_systems = old_pull.get("systems", [])
            if isinstance(old_systems, list):
                existing_systems = [dict(s) for s in old_systems]
            # 旧单系统格式:顶层字段已在 systems 里则跳过,否则保留为
            # 兼容字段(resolved_systems 空数组时回退使用)
            old_flat_sid = old_pull.get("system_id", "")
            if old_flat_sid and old_flat_sid != sid:
                # 旧配置是另一个系统的单系统格式 → 迁移:把旧系统加入数组
                legacy = {
                    "amail_url": old_pull.get("amail_url", gw),
                    "admin_key": old_pull.get("admin_key", ak),
                    "system_id": old_flat_sid,
                    "poll_interval_sec": old_pull.get("poll_interval_sec", 2),
                }
                if old_pull.get("api_key"):
                    legacy["api_key"] = old_pull["api_key"]
                if old_pull.get("webhook_secret"):
                    legacy["webhook_secret"] = old_pull["webhook_secret"]
                existing_systems.append(legacy)
        except Exception:
            pass

    # 更新/追加当前系统条目(按 system_id 去重)
    merged = [s for s in existing_systems if s.get("system_id") != sid]
    merged.append(new_entry)

    with open(path, 'w') as f:
        f.write('\n'.join(_config_lines(addr, mode, merged, log_path)) + '\n')

def start_bridge(bin_path: str, cfg_path: str, pid_path: str) -> bool:
    """Start bridge process — SINGLE instance. Returns True if running.

    2026-08-16 双进程教训: pkill 模式匹配可能漏杀(旧进程 cmdline
    是旧路径),残留进程与新进程双拉同一 pending = 重复投递风险。
    因此: ① 按 pid 文件精确杀 ② pgrep -af 兜底列出全部 aimail-bridge
    进程按 PID 逐个 kill(不依赖模式匹配)③ 再启动。
    """
    # 1) pid 文件精确杀
    old_pid = -1
    if os.path.exists(pid_path):
        try:
            old_pid = int(open(pid_path).read().strip())
            os.kill(old_pid, 15)
            try:
                os.waitpid(old_pid, 0)
            except (ChildProcessError, OSError):
                pass
        except (ValueError, ProcessLookupError):
            pass
        time.sleep(1)
        # 优雅关闭可能卡住(pull 循环阻塞)→ 复查强杀
        try:
            os.kill(old_pid, 0)  # 进程还存在?
            os.kill(old_pid, 9)
        except (ProcessLookupError, PermissionError):
            pass

    # 2) pgrep 兜底:列出 aimail-bridge 进程按 PID 杀(防模式漏杀)。
    # ⚠️ 精确匹配:必须匹配 bridge 二进制路径特征(--config 参数或
    # bridge/bin/aimail-bridge),不能裸匹配 "aimail-bridge" 字符串——
    # 否则会误杀命令行含该串的其他进程(如集成脚本自身 shell、测试
    # 包装进程)。2026-08-16 实测事故:裸匹配曾把生产 bridge 与调用
    # shell 一并杀掉。
    def _bridge_pids() -> list:
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", r"aimail-bridge.*--config|aimail-bridge.*\.toml"],
                text=True, timeout=5)
            return [int(l.strip()) for l in out.splitlines() if l.strip().isdigit()]
        except subprocess.CalledProcessError:
            return []

    for pid in _bridge_pids():
        try:
            os.kill(pid, 15)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(1)
    # 复查残留 → 强杀(仅剩匹配 bridge 特征的进程)
    for pid in _bridge_pids():
        try:
            os.kill(pid, 9)
        except (ProcessLookupError, PermissionError):
            pass

    if os.path.exists(pid_path):
        try:
            os.remove(pid_path)
        except OSError:
            pass

    with open(os.devnull, 'w') as lf:
        proc = subprocess.Popen(
            [bin_path, '-c', cfg_path],
            stdout=lf, stderr=lf,
            start_new_session=True  # daemonize
        )

    time.sleep(1.5)
    if proc.poll() is None:
        with open(pid_path, 'w') as f:
            f.write(str(proc.pid))
        return True
    return False

def main():
    # Standalone restart: just kill and restart bridge process
    if "--restart" in sys.argv:
        bin_path = os.path.join(_AM_HOME, "bridge/bin/aimail-bridge")
        cfg_path = os.path.join(_AM_HOME, "bridge/aimail_bridge.toml")
        pid_path = os.path.join(_AM_HOME, "bridge/bridge.pid")
        start_bridge(bin_path, cfg_path, pid_path)
        return 0 if os.path.exists(pid_path) else 1

    # Standalone init: machine-level network setup (aimail init) — runs on
    # a machine with zero systems. Deploys the binary and writes a skeleton
    # config (empty systems). Bridge API key + start happen at the FIRST
    # install (system activation provides the gateway admin key), so
    # activation and bridge remain decoupled (2026-09-02 init/install split).
    if "--init" in sys.argv:
        gw = os.environ.get("GATEWAY_URL", "") or os.environ.get("AIMAIL_URL", "")
        if not gw:
            log_warn("init 需要 GATEWAY_URL/AIMAIL_URL(网关在哪)")
            return 1
        wh_mode = os.environ.get("WEBHOOK_MODE", "bridge")
        bridge_mode = "pull" if wh_mode == "bridge" else "push"
        wh_host = os.environ.get("WEBHOOK_HOST", "") or os.environ.get("AIMAIL_WEBHOOK_HOST", "")
        if bridge_mode == "pull":
            wh_host = ""
        elif not wh_host:
            wh_host = format_webhook_host(detect_ip())
            log_step(f"Auto-detected bridge address: {wh_host}")

        bridge_dir = os.path.join(_AM_HOME, "bridge/bin")
        bridge_bin = os.path.join(bridge_dir, "aimail-bridge")
        if not _ensure_binary(bridge_bin, bridge_dir):
            log_warn("bridge 二进制不可用;请先在仓库 bridge/ 放置 aimail-bridge zip")
            return 1

        cfg_path = os.path.join(_AM_HOME, "bridge/aimail_bridge.toml")
        if os.path.exists(cfg_path):
            log_ok(f"bridge 配置已存在: {cfg_path}(复用,首个 install 将 merge 系统条目)")
        else:
            with open(cfg_path, 'w') as f:
                f.write('\n'.join(_config_lines(
                    wh_host or "127.0.0.1:38081", bridge_mode, [],
                    os.path.join(_AM_HOME, "logs/aimail-bridge.log"))) + '\n')
            log_ok(f"bridge 骨架配置已写入: {cfg_path}(systems 空)")
        print("  init: bridge 二进制+配置就位。首个系统激活(aimail install)时"
              "将创建 bridge key、追加系统条目并启动。")
        return 0

    # Read env vars from integrate.sh
    gw = os.environ.get("GATEWAY_URL", "")
    ak = os.environ.get("ADMIN_KEY", "")
    sid = os.environ.get("SYSTEM_ID", "")
    domain = os.environ.get("AIMAIL_DOMAIN", "")
    wh_mode = os.environ.get("WEBHOOK_MODE", "bridge")
    # webhook_host 来源链(2026-08-18 用户定稿,与 setup_system 同源):
    # env(AIMAIL_WEBHOOK_HOST,兼容旧 WEBHOOK_HOST)→ 已有配置 → 自动探测
    wh_host = os.environ.get("WEBHOOK_HOST", "") or os.environ.get("AIMAIL_WEBHOOK_HOST", "")
    if not wh_host and sid:
        try:
            gw_path = os.path.join(os.path.join(_AM_HOME, "systems"), sid, "aimail_gateway.json")
            if os.path.isfile(gw_path):
                wh_host = json.load(open(gw_path)).get("webhook_host", "")
        except Exception:
            pass

    if not all([gw, ak, sid, domain]):
        log_warn("Required vars missing: GATEWAY_URL, ADMIN_KEY, SYSTEM_ID, AIMAIL_DOMAIN")
        return 1

    # ── Bridge deployment ────────────────────────────────────
    bridge_dir = os.path.join(_AM_HOME, "bridge/bin")
    bridge_bin = os.path.join(bridge_dir, "aimail-bridge")
    os.makedirs(bridge_dir, exist_ok=True)

    if not _ensure_binary(bridge_bin, bridge_dir):
        log_warn("bridge 二进制不可用")
        return 1

    # Write bridge config
    bridge_mode = "pull" if wh_mode == "bridge" else "push"

    # 三态语义(2026-08-18 用户定稿):pull 模式 webhook_host 显式空
    # (有 bridge 拉取,云端不回调);push 模式才保留 env/已有/探测值。
    if bridge_mode == "pull":
        wh_host = ""
    elif not wh_host:
        ip = detect_ip()
        wh_host = format_webhook_host(ip)
        log_step(f"Auto-detected bridge address: {wh_host}")
    if wh_host:
        log_step(f"Using configured bridge address: {wh_host}")

    cfg_dir = os.path.join(_AM_HOME, "bridge")
    os.makedirs(cfg_dir, exist_ok=True)
    bridge_cfg = os.path.join(cfg_dir, "aimail_bridge.toml")

    # Create bridge API key (use system-level key for higher privilege)
    import uuid
    system_key = ""
    system_key_path = os.path.join(
        os.path.join(_AM_HOME, ".system_raw_key"), f"{sid}_admin.key"
    )
    if os.path.exists(system_key_path):
        try:
            with open(system_key_path) as f:
                system_key = f.read().strip()
        except Exception:
            pass
    bridge_ak = system_key or ak  # prefer system key, fallback to agent key

    # Idempotency (2026-09-04): reuse the existing bridge api_key when the
    # current toml already has one for this sid — repeated `aimail install`
    # must not mint a fresh bridge-{uuid} key each run (orphan key sprawl in
    # the gateway DB; old keys stay valid but unused).
    bridge_key = ""
    try:
        import tomllib as _tl
        if os.path.exists(bridge_cfg):
            with open(bridge_cfg, "rb") as _f:
                _old = _tl.load(_f)
            for _s in (_old.get("pull", {}) or {}).get("systems", []):
                if _s.get("system_id") == sid and _s.get("api_key"):
                    bridge_key = _s["api_key"]
                    log_ok("reuse existing bridge api_key (idempotent install)")
                    break
    except Exception:
        bridge_key = ""

    if not bridge_key:
        bridge_domain = f"bridge-{uuid.uuid4().hex[:8]}"
        bridge_result = create_api_key(gw, bridge_ak, sid, bridge_domain, ["bridge"], "bridge")
        bridge_key = bridge_result.get("raw_key", "") if isinstance(bridge_result, dict) else ""
        if not bridge_key:
            log_warn("bridge API key creation failed — aborting deploy (auth would fail)")
            return 1

    # Read webhook secret from agent config (Hermes → env fallback)
    webhook_secret = os.environ.get("AIMAIL_WEBHOOK_SECRET", "")
    if not webhook_secret:
        try:
            import yaml
            hermes_cfg = os.path.expanduser("~/.hermes/config.yaml")
            if os.path.isfile(hermes_cfg):
                with open(hermes_cfg) as f:
                    hc = yaml.safe_load(f)
                webhook_secret = hc.get("platforms", {}).get("webhook", {}).get("extra", {}).get("secret", "")
        except:
            pass

    write_bridge_config(bridge_cfg, bridge_mode, wh_host or "127.0.0.1:38081",
                        gw, ak, sid, api_key=bridge_key,
                        webhook_secret=webhook_secret)

    # Read gateway config to update webhook_host
    gw_cfg = None
    gw_cfg_path = None
    if sid:
        sub = os.path.join(os.path.join(_AM_HOME, "systems"), sid, "aimail_gateway.json")
        if os.path.isfile(sub):
            try:
                with open(sub) as f:
                    gw_cfg = json.load(f)
                gw_cfg_path = sub
            except Exception:
                pass
    if gw_cfg_path and gw_cfg is not None:
        gw_cfg["webhook_host"] = wh_host
        with open(gw_cfg_path, 'w') as f:
            json.dump(gw_cfg, f, indent=2)

    # Start bridge
    pid_path = os.path.join(cfg_dir, "bridge.pid")
    if start_bridge(bridge_bin, bridge_cfg, pid_path):
        log_ok(f"bridge started (mode={bridge_mode}, {wh_host})")
        if bridge_key:
            log_ok("bridge API key created (category=bridge)")
    else:
        log_warn("bridge failed to start — check ~/.aimail/logs/aimail-bridge.log")

    return 0

if __name__ == "__main__":
    sys.exit(main())

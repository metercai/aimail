#!/usr/bin/env python3
"""Deploy aimail-bridge: bridge config, startup."""
import sys, os, json, re, subprocess, socket, time
from urllib.parse import urlparse

# 运行时核心(repo pysdk/ 优先 > pip aimail 兜底);维护脚本从 cli/ 调用
_SCRIPTS_DIR = str(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from runtime_core import load_core  # noqa: E402
load_core()
from gateway_api import create_api_key

# Machine home: canonical rule = pysdk/aimail_base.aimail_home()
# (env AIMAIL_HOME wins, else ~/.aimail) — deploy 是一次性部署脚本,
# 自包含解析,公式同构。
_AM_HOME = os.environ.get("AIMAIL_HOME") or os.path.expanduser("~/.aimail")


from _common import bridge_ctl_supported, bridge_status, bridge_stop


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
            # zip 内单文件带版本号(aimail-bridge-vX.Y.Z-ARCH);解到临时目录后
            # 统一改名为裸名,不依赖 zip 内部命名。
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                r = subprocess.run(
                    ["unzip", "-o", zip_path, "-d", td],
                    capture_output=True, timeout=30)
                if r.returncode != 0:
                    log_warn(f"unzip failed: {(r.stderr or b'').decode()[:200]}")
                    return False
                found = [f for f in os.listdir(td)
                         if f.startswith("aimail-bridge")
                         and os.path.isfile(os.path.join(td, f))]
                if not found:
                    log_warn(f"zip 内无 aimail-bridge 可执行文件: {zip_path}")
                    return False
                os.makedirs(bridge_dir, exist_ok=True)
                os.replace(os.path.join(td, found[0]), bridge_bin)
            os.chmod(bridge_bin, 0o755)
        except Exception as e:
            log_warn(f"Failed to extract bridge: {e}")
            return False
    return os.access(bridge_bin, os.X_OK)

def _config_lines(addr: str, mode: str, merged: list, log_path: str,
                  hostname: str = "") -> list:
    """TOML body for a single bridge serving the given system entries.

    ``hostname`` = the public address the bridge ANNOUNCES (top-level key, must stay
    above the first ``[table]``). Without it the bridge builds the URL it answers
    `POST /api/v1/routes` with out of `bind` — 0.0.0.0 by default, i.e. undeliverable
    (U2, 2026-09-28). It is written for push deployments only (pull: the gateway
    fetches the mail, nothing calls back — and a stray hostname could flip the
    bridge's domain/TLS and dual-port behaviour).
    """
    lines = [
        # schema 版本契约(2026-09-20): 桥只接受 <= 自己支持的最高版本, 更高版本拒绝启动。
        # 当前版本 1(桥侧 SUPPORTED_SCHEMA_VERSION)。
        'schema_version = 1',
        f'bind = "{addr}"',
        f'mode = "{mode}"',
    ]
    if hostname:
        lines.append(f'hostname = "{hostname}"')
    lines += [
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
            f'aimail_url = "{s["aimail_url"]}"',
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
    # Highest semantic version wins (v1.2.3 > v1.2)
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


# ═══════════════════════════════════════════════════════════════
# U2 tail (2026-09-28): the announced address + a LOUD deliverability judge
# ═══════════════════════════════════════════════════════════════
# Read out of the bridge repo (aimail-bridge/src/{config.rs,admin.rs}):
#   · `hostname` is the top-level TOML key holding the public address the bridge
#     *announces*; `POST /api/v1/routes` answers with
#     `http://{hostname or bind}<push path>` (admin.rs:143-148). With no `hostname`
#     the answer is built from `bind` — a LISTEN directive whose default is
#     0.0.0.0:38080, never a destination. That is U2: a URL nobody can POST to got
#     registered and inbound mail had nowhere to go.
#   · an `ip:port` hostname keeps plain HTTP; a *domain* makes the bridge attempt
#     TLS (static certs, else ACME — config.rs `has_tls`/`is_dual_port`).
#   · `AIMAIL_BRIDGE_HOSTNAME` overrides `hostname` at bridge startup (config.rs:394).
# So the address is written here AND judged here: whatever gets registered must be a
# deliverable absolute http(s) URL. Nothing undeliverable is written silently.
#: Env the bridge itself reads for the announced address (config.rs:394).
ANNOUNCE_ENV = "AIMAIL_BRIDGE_HOSTNAME"
#: What the bridge appends to the announced host when it self-reports a push entry
#: (aimail-bridge/src/admin.rs:143-148). contract-allowed: quotes the path hardcoded
#: by the bridge repo — this file mirrors an external artifact, it does not define it.
BRIDGE_ANNOUNCE_PATH = "/webhooks/aimail-inbound"  # contract-allowed: bridge's own hardcoded self-report path (admin.rs:143-148); mirrored, not defined, here.
#: An unspecified address is a bind directive: mail POSTed to it never arrives.
WILDCARD_HOSTS = ("0.0.0.0", "::", "0:0:0:0:0:0:0:0", "[::]")
#: The bridge's own default bind port (aimail_bridge.toml.example).
DEFAULT_BIND_PORT = 38080


def _split_host_port(value: str):
    """(host, port_str) — split 'host:port', '[v6]:port', '[v6]' or 'host'."""
    s = (value or "").strip()
    if not s:
        return "", ""
    if s.startswith("["):                      # [2001:db8::1]:38081 | [2001:db8::1]
        host, _, rest = s.partition("]")
        rest = rest.lstrip(":")
        return host[1:], (rest if rest else "")
    if ":" in s:                               # host:port | 2001:db8::1(bare v6)
        host, _, port = s.rpartition(":")
        if ":" in host:                        # bare IPv6 without brackets
            return s, ""
        return host, port
    return s, ""


def _valid_port(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if 0 < n < 65536 else 0


def normalize_announced(value, default_port: int = 38081):
    """(host:port, reason) — the value for the bridge's `hostname` key.

    Accepts what operators actually announce: a bare `host:port`, a bare `host`
    (the default port is appended), a bracketed/plain IPv6 literal, or a full
    `http(s)://host[:port]` URL (the scheme is dropped — the bridge key is
    scheme-less). Refused, with the reason spelled out: an empty value, a value
    carrying a path/query (that is a URL, not an address), a non-http(s) scheme, a
    malformed or out-of-range port, a wildcard/unspecified host.
    """
    raw = ("" if value is None else str(value)).strip()
    if not raw:
        return "", "no address announced"
    if raw.lower().startswith(("http://", "https://")):
        u = urlparse(raw)
        if u.path not in ("", "/") or u.query or u.fragment:
            return "", (f"'{raw}' carries a path/query — the bridge's `hostname` is an "
                        f"address, not a URL")
        netloc = u.netloc
        if "@" in netloc:
            return "", f"'{raw}' carries userinfo — not an address"
        host, port = _split_host_port(netloc)
        scheme = u.scheme
    else:
        if "/" in raw or "?" in raw or "#" in raw:
            return "", (f"'{raw}' carries a path — the bridge's `hostname` is an "
                        f"address, not a URL")
        host, port = _split_host_port(raw)
        scheme = ""
    if not host:
        return "", f"'{raw}' carries no host"
    if host.lower() in WILDCARD_HOSTS or host == "::":
        return "", (f"'{host}' is an unspecified/bind address — inbound mail POSTed to "
                    f"it never arrives (U2); announce the public address instead")
    if not port:
        port = str(default_port)
    elif not _valid_port(port):
        return "", f"'{raw}' carries an invalid port '{port}'"
    # A bare IPv6 literal needs brackets so `host:port` stays parseable by the bridge.
    shown = f"[{host}]" if ":" in host else host
    note = ""
    if scheme == "https":
        note = (" (announced over https — the bridge still self-reports http://, see "
                "admin.rs:143-148)")
    return f"{shown}:{port}", note


def _arg_value(flag: str) -> str:
    """`--flag value` / `--flag=value` from sys.argv ('' when absent)."""
    for i, a in enumerate(sys.argv):
        if a == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return ""


def _announce_input() -> str:
    """The announced address from the explicit inputs only (arg, then env)."""
    return (_arg_value("--bridge-hostname") or os.environ.get(ANNOUNCE_ENV, "")
            or os.environ.get("AIMAIL_WEBHOOK_HOST", ""))


def announced_url(hostport: str, scheme: str = "http") -> str:
    """The URL this environment announces/registers for the bridge's push entry."""
    return f"{scheme}://{hostport}{BRIDGE_ANNOUNCE_PATH}"


def judge_deliverable(url: str):
    """(ok, reason) — LOUD judge: can the cloud POST to this URL at all?

    Deliverable = the shape the gateway's HTTP client needs (and the shape the SDK's
    `webhook_host` state ③ requires, aimail_base.resolve_register_webhook_url): an
    ABSOLUTE http(s) URL with a real host. Refused with the reason in words — never a
    silent pass:
      · scheme-less (a bare `host` / `host:port` — reqwest dies with `builder error`,
        measured 2026-09-27 L2 J4e);
      · non-http(s) scheme, no host, userinfo, invalid/out-of-range port;
      · a wildcard/unspecified host (0.0.0.0 / ::) — the U2 shape: it is a listen
        address, not a destination.
    """
    raw = ("" if url is None else str(url)).strip()
    if not raw:
        return False, "the URL is empty (pull mode: nothing is registered)"
    try:
        u = urlparse(raw)
        port = u.port
    except ValueError as e:
        return False, f"'{raw}' is not a usable URL ({e})"
    scheme = (u.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, (f"'{raw}' is not an absolute http(s) URL — a bare host or "
                       f"host:port cannot be POSTed to (reqwest: builder error)")
    if "@" in (u.netloc or ""):
        return False, f"'{raw}' carries userinfo"
    host = (u.hostname or "").strip()
    if not host:
        return False, f"'{raw}' carries no host"
    if host.lower() in WILDCARD_HOSTS:
        return False, (f"'{raw}' points at {host} — a bind/unspecified address, not a "
                       f"destination (U2: nothing can deliver there)")
    if port is not None and not _valid_port(port):
        return False, f"'{raw}' carries an invalid port"
    return True, ""


def bind_address(hostport: str) -> tuple:
    """(bind value, reason) — what the bridge may actually `bind`.

    A bind must be an address, so a domain announcement keeps 0.0.0.0 + that port
    (the bridge listens everywhere, the announcement stays the domain). An IP
    announcement binds to exactly that address, as before.
    """
    host, port = _split_host_port(hostport)
    try:
        socket.inet_pton(socket.AF_INET, host)
        return f"{host}:{port}", ""
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, host)
        return f"[{host}]:{port}", ""
    except OSError:
        pass
    return f"0.0.0.0:{port or DEFAULT_BIND_PORT}", (
        f"announced host '{host}' is a domain (not an IP) — the bridge cannot bind to "
        f"it, so it listens on 0.0.0.0:{port or DEFAULT_BIND_PORT} and announces the "
        f"domain")


def write_bridge_config(path: str, mode: str, addr: str, gw: str,
                        ak: str, sid: str, api_key: str = "",
                        webhook_secret: str = "", hostname: str = ""):
    """Write/merge aimail_bridge.toml — SINGLE bridge, MULTI-system.

    2026-08-16 用户定调: 本机只安装一个 bridge,不管几套 agent 系统
    (bridge 已支持多系统透传)。因此本函数**合并**而非覆盖:
      - 已有 [pull].systems 数组 → 追加/更新当前 sid 的条目,保留其他系统
    重启由 start_bridge 幂等处理(先杀旧进程再起新,单实例)。
    """
    log_path = os.path.join(_AM_HOME, "bridge", "aimail-bridge.log")

    def _entry() -> dict:
        e = {
            "aimail_url": gw,
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

    # 读取已有配置(若存在):保留其他系统的 systems 条目(systems 数组
    # 是唯一格式——开发期旧单系统格式无存量)
    if os.path.exists(path):
        try:
            import tomllib
            with open(path, "rb") as f:
                old = tomllib.load(f)
            old_pull = old.get("pull", {})
            old_systems = old_pull.get("systems", [])
            if isinstance(old_systems, list):
                existing_systems = [dict(s) for s in old_systems]
        except Exception:
            pass

    # 更新/追加当前系统条目(按 system_id 去重)
    merged = [s for s in existing_systems if s.get("system_id") != sid]
    merged.append(new_entry)

    with open(path, 'w') as f:
        f.write('\n'.join(_config_lines(addr, mode, merged, log_path, hostname)) + '\n')
    try:
        os.chmod(path, 0o600)  # 凭据文件(admin_key/bridge key/webhook_secret;AUDIT-1 F6)
    except OSError:
        pass

def start_bridge(bin_path: str, cfg_path: str, pid_path: str) -> bool:
    """Start bridge process — SINGLE instance. Returns True if running.

    2026-08-16 双进程教训: pkill 模式匹配可能漏杀(旧进程 cmdline
    是旧路径),残留进程与新进程双拉同一 pending = 重复投递风险。
    因此: ① 按 pid 文件精确杀 ② pgrep -af 兜底列出全部 aimail-bridge
    进程按 PID 逐个 kill(不依赖模式匹配)③ 再启动。
    """
    # 2026-09-20 P2: 不再由 CLI 自己 kill 进程 —— 走桥的生命周期契约(--status/--stop)
    # 契约能力不可用(旧桥 < 0.7.4)时回退旧行为(见下方 legacy 分支)。
    ctl_ok = bridge_ctl_supported(bin_path)
    if ctl_ok:
        st = bridge_status(bin_path, pid_path)
        if st.get("running"):
            log_step(f"检测到已运行桥(pid={st.get('pid')}) → 优雅停止")
            rc = bridge_stop(bin_path, pid_path)
            if rc != 0:
                # 停止失败绝不硬启: 双实例会双拉同一 pending ⇒ 重复投递(AUDIT-1 教训)
                log_warn(f"桥停止失败(rc={rc}) → 放弃本次启动, 请先 `aimail-bridge --stop` 排查")
                return False
    else:
        log_warn("桥二进制不支持生命周期契约(--status/--stop) → 回退旧清理逻辑; 建议升级桥到 >= 0.7.4")
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
            try:
                os.kill(old_pid, 0)
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
        """bridge 进程枚举: 逻辑收口在 _common.pids_by_pattern(pgrep 优先, 无 procps
        时回退扫 /proc —— F12 2026-09-25: deerflow 宿主镜像没有 procps, 原地实现只捕
        CalledProcessError ⇒ FileNotFoundError 冒泡成 traceback)。正则口径见上面注释。"""
        from _common import pids_by_pattern
        return pids_by_pattern(r"aimail-bridge.*--config|aimail-bridge.*\.toml")

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

    if ctl_ok:
        # 桥自守护: --daemon 由**桥自己** fork 脱离并写 pid 文件(跨平台的正确做法,
        # 取代原先 POSIX 专有的 start_new_session + CLI 代写 pid)
        cmd = [bin_path, '-c', cfg_path, '--daemon', '--pid-file', pid_path]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            log_warn("桥启动命令超时(仍继续探测状态)")
        for _ in range(20):
            time.sleep(0.5)
            if bridge_status(bin_path, pid_path).get("running"):
                return True
        log_warn("桥启动后 10s 内未就绪(`aimail-bridge --status` 查)")
        return False

    with open(os.devnull, 'w') as lf:
        proc = subprocess.Popen(
            [bin_path, '-c', cfg_path],
            stdout=lf, stderr=lf,
            start_new_session=True  # legacy: 旧桥没有 --daemon 的 Windows 实现
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

    # Standalone init: machine-level network setup — runs on
    # a machine with zero systems. Deploys the binary and writes a skeleton
    # config (empty systems). Bridge API key + start happen at the FIRST
    # install (system activation provides the gateway admin key), so
    # activation and bridge remain decoupled (2026-09-02 init/install split).
    if "--init" in sys.argv:
        gw = os.environ.get("GATEWAY_URL", "") or os.environ.get("AIMAIL_URL", "")
        if not gw:
            log_warn("init needs GATEWAY_URL/AIMAIL_URL (where is the gateway)")
            return 1
        wh_mode = os.environ.get("WEBHOOK_MODE", "bridge")
        bridge_mode = "pull" if wh_mode == "bridge" else "push"
        wh_host = _announce_input()
        if bridge_mode == "pull":
            wh_host = ""
        else:
            wh_host, why = normalize_announced(wh_host or format_webhook_host(detect_ip()))
            if not wh_host:
                # Provisional skeleton (no systems yet): the announcement is judged
                # here as a WARNING — `aimail install` is where it becomes fatal.
                log_warn(f"init: announced address unusable ({why}) — writing a "
                         f"skeleton without `hostname`; fix it at install time with "
                         f"--bridge-hostname / {ANNOUNCE_ENV}")
            else:
                log_step(f"Announced address: {wh_host}")

        bridge_dir = os.path.join(_AM_HOME, "bridge/bin")
        bridge_bin = os.path.join(bridge_dir, "aimail-bridge")
        if not _ensure_binary(bridge_bin, bridge_dir):
            log_warn("bridge binary unavailable; place an aimail-bridge zip under the repo bridge/ dir")
            return 1

        cfg_path = os.path.join(_AM_HOME, "bridge/aimail_bridge.toml")
        if os.path.exists(cfg_path):
            log_ok(f"bridge config exists: {cfg_path} (reused; first install merges system entries)")
        else:
            with open(cfg_path, 'w') as f:
                f.write('\n'.join(_config_lines(
                    wh_host or "127.0.0.1:38081", bridge_mode, [],
                    os.path.join(_AM_HOME, "bridge", "aimail-bridge.log"),
                    wh_host if bridge_mode == "push" else "")) + '\n')
            log_ok(f"bridge skeleton config written: {cfg_path} (empty systems)")
        print("  init: bridge binary + config in place (empty systems; first install starts it)")
        return 0

    # Read env vars from integrate.sh
    gw = os.environ.get("GATEWAY_URL", "")
    ak = os.environ.get("ADMIN_KEY", "")
    sid = os.environ.get("SYSTEM_ID", "")
    domain = os.environ.get("AIMAIL_DOMAIN", "")
    wh_mode = os.environ.get("WEBHOOK_MODE", "bridge")
    # 已落盘的声明(值可能是绝对 URL 或裸 host:port —— 两种都读得懂,见
    # normalize_announced;只有写了 `hostname` 的桥才是可投递的,那是本文件的职责)。
    cfg_wh_host = ""
    if sid:
        try:
            gw_path = os.path.join(os.path.join(_AM_HOME, "systems"), sid, "aimail_gateway.json")
            if os.path.isfile(gw_path):
                cfg_wh_host = json.load(open(gw_path)).get("webhook_host", "")
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

    bridge_mode = "pull" if wh_mode == "bridge" else "push"

    # ── the announced address + the URL the environment registers (U2 tail) ────
    # 三态语义(2026-08-18 用户定稿):pull 模式显式不声明(云端不回调,桥也不需要
    # hostname);push 模式才解析来源链,并且**在写任何东西之前**判可投递性:
    # 不可投递 = 邮件黑洞(U2),所以 push 场景下大声失败而不是静默按 0.0.0.0 注册。
    announce_src = "pull mode — the gateway fetches the mail, nothing calls back"
    announced = ""
    if bridge_mode == "push":
        for src, val in ((f"--bridge-hostname / {ANNOUNCE_ENV}", _announce_input()),
                         ("systems/<sid>/aimail_gateway.json:webhook_host", cfg_wh_host)):
            if not val:
                continue
            announced, note = normalize_announced(val)
            if not announced:
                print(f"  ✘ announced address unusable ({src}): {note}")
                print(f"    a bridge registered from an address like that can never be "
                      f"POSTed to — inbound mail would vanish. Announce the public "
                      f"address, e.g. `--bridge-hostname 203.0.113.9:38081` or "
                      f"{ANNOUNCE_ENV}=bridge.example.com:38081")
                return 1
            announce_src = src + (note or "")
            break
        else:
            announced, note = normalize_announced(format_webhook_host(detect_ip()))
            announce_src = "auto-detected from this machine's global address"
            if not announced:
                print(f"  ✘ no usable announced address: {note}")
                print(f"    pass --bridge-hostname <host:port> (or {ANNOUNCE_ENV}) — a "
                      f"push deployment must announce where inbound mail can arrive")
                return 1
    if announced:
        registration_url = announced_url(announced)
        ok, why = judge_deliverable(registration_url)
        if not ok:
            print(f"  ✘ the URL this deploy would register is not deliverable: {why}")
            print(f"    (announced {announced} via {announce_src})")
            return 1
        host_only = _split_host_port(announced)[0]
        if host_only in ("127.0.0.1", "localhost", "::1"):
            log_warn(f"announced address {announced} is loopback — only a callback "
                     f"from this very host can reach it")
        bind, bind_note = bind_address(announced)
        if bind_note:
            log_warn(bind_note)
        log_step(f"Announced address: {announced} (from {announce_src})")
        log_step(f"Registration URL: {registration_url} (deliverable: absolute http(s))")
    else:
        registration_url = ""
        bind = "127.0.0.1:38081"

    cfg_dir = os.path.join(_AM_HOME, "bridge")
    os.makedirs(cfg_dir, exist_ok=True)
    bridge_cfg = os.path.join(cfg_dir, "aimail_bridge.toml")

    # Create bridge API key (use system-level key for higher privilege)
    import uuid
    system_key = ""
    system_key_path = os.path.join(
        os.path.join(_AM_HOME, "systems", sid), ".system_raw_key.key"
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

    write_bridge_config(bridge_cfg, bridge_mode, bind,
                        gw, ak, sid, api_key=bridge_key,
                        webhook_secret=webhook_secret,
                        hostname=announced)

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
    # env.webhook_host —— 只由 CLI 写,SDK 只读。值必须是**可投递的绝对 http(s) URL**
    # (SDK 三态 ③;裸 host:port 会被拒并退回本机端点,aimail_base ③' ⇒ 邮件到不了桥)。
    # pull = 显式空串(三态 ②,云端不回调)。值没变就不写(幂等,避免无谓落盘)。
    if gw_cfg_path and gw_cfg is not None:
        if str(gw_cfg.get("webhook_host", "") or "") != registration_url:
            gw_cfg["webhook_host"] = registration_url
            with open(gw_cfg_path, 'w') as f:
                json.dump(gw_cfg, f, indent=2)
            log_ok(f"webhook_host = {registration_url or '(empty = pull)'} "
                   f"({gw_cfg_path})")

    # Start bridge
    pid_path = os.path.join(cfg_dir, "bridge.pid")
    if start_bridge(bridge_bin, bridge_cfg, pid_path):
        log_ok(f"bridge started (mode={bridge_mode}, "
               f"{registration_url or 'no announcement (pull)'})")
        if bridge_key:
            log_ok("bridge API key created (category=bridge)")
    else:
        log_warn("bridge failed to start — check ~/.aimail/bridge/aimail-bridge.log")
        return 1

if __name__ == "__main__":
    sys.exit(main())

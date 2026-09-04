#!/usr/bin/env python3
"""
check_status.py — One-shot amail pipeline runtime status check (generic)

Covers the full chain: aimail-gateway → aimail-bridge → agent config →
agent hook interface → ping-pong. Platform-agnostic: works for any
agent system (Hermes/OpenClaw/DeerFlow/dsh) via a per-platform adapter
(L3/L4 vary by platform; L1/L2/L5 are shared).

Usage:
    python3 scripts/check_status.py [--agent-type hermes|openclaw|auto]
    python3 scripts/check_status.py --json       # JSON output
    python3 scripts/check_status.py --verbose    # with fix suggestions
    python3 scripts/check_status.py --ping       # run ping-pong test only
"""
import sys, os, json, subprocess, time, re, socket
from pathlib import Path
from datetime import datetime, timezone
import urllib.request, urllib.error

# ── ANSI helpers ───────────────────────────────────────────────
GREEN  = '\033[0;32m'
RED    = '\033[0;31m'
YELLOW = '\033[1;33m'
BROWN  = '\033[0;33m'
BOLD   = '\033[1m'
NC     = '\033[0m'
CHECK  = '\u2713'
CROSS  = '\u2717'

# ── Path constants ─────────────────────────────────────────────
AGENT_HOME = Path(os.environ.get("AGENT_HOME", str(Path.home() / ".hermes")))
# --agent-home 直接指定 agent 系统 home(如 Hermes profile 目录),覆盖 env 默认;
# 影响 AGENT_CFG/SUBS_FILE/PROFILES_DIR 全部派生路径
if "--agent-home" in sys.argv:
    try:
        ai = sys.argv.index("--agent-home")
        if ai + 1 < len(sys.argv):
            _ah = Path(sys.argv[ai + 1]).expanduser()
            if _ah.is_dir():
                AGENT_HOME = _ah
    except Exception:
        pass
# 与 aimail_base.aimail_home() 同语义:空 env 回退 ~/.aimail。
# (旧写法 Path("")=PosixPath('.') 恒真,or 回退永不生效——bug。)
_AH_ENV = os.environ.get("AIMAIL_HOME", "")
AIMAIL_HOME = Path(_AH_ENV).expanduser() if _AH_ENV else Path.home() / ".aimail"
SYSTEMS_DIR = AIMAIL_HOME / "systems"
MAIL_DIR    = AIMAIL_HOME / "mail"
BRIDGE_DIR  = AIMAIL_HOME / "bridge"
LOGS_DIR    = AIMAIL_HOME / "logs"

def _clean_agent_dir_name(addr: str) -> str:
    """agent 地址 → 目录名（与 pysdk/aimail_base._clean_agent_dir_name 一致）。"""
    return re.sub(r"[^\w.\-]", "_", addr, flags=re.ASCII)


def _split_host_port(addr: str) -> tuple[str, str]:
    """host:port 拆分(支持 [ipv6]:port)。"""
    addr = addr.strip()
    if addr.startswith("["):
        host, _, rest = addr[1:].partition("]")
        return host, rest.lstrip(":")
    host, _, port = addr.rpartition(":")
    return host, port

def _resolve_system_id(args: list[str] | None = None) -> str:
    """Determine system_id: --system-id arg > AGENT_HOME/.agentmail > env.

    无参调用时也回退扫描 sys.argv 的 --system-id(2026-08-16 修复:
    Hermes adapter 无参调用,而 AGENT_HOME 可能是平台根,指针在
    profile 目录——argv 显式 sid 必须生效)。"""
    if args is None:
        args = sys.argv
    for i, a in enumerate(args):
        if a == "--system-id" and i + 1 < len(args):
            return args[i + 1]
    pointer = AGENT_HOME / ".agentmail"
    if pointer.is_file():
        try:
            return json.loads(pointer.read_text()).get("system_id", "")
        except Exception:
            pass
    return os.environ.get("SYSTEM_ID", "")

def _system_agent_path(sid: str) -> Path:
    p = SYSTEMS_DIR / sid / "aimail_gateway.json"
    legacy = SYSTEMS_DIR / sid / "agentmail_gateway.json"
    if legacy.is_file() and not p.is_file():
        try:
            legacy.rename(p)
            import os as _os
            _os.chmod(p, 0o600)
        except Exception:
            pass
    return p

BRIDGE_CFG  = BRIDGE_DIR / "aimail_bridge.toml"
BRIDGE_PID  = BRIDGE_DIR / "bridge.pid"
BRIDGE_LOG  = LOGS_DIR / "aimail-bridge.log"
AGENT_CFG   = AGENT_HOME / "config.yaml"
# --agent 指定 profile 时,读该 profile 的 config.yaml(端口随 profile)
if "--agent" in sys.argv:
    try:
        ai = sys.argv.index("--agent")
        if ai + 1 < len(sys.argv):
            _prof_cfg = AGENT_HOME / "profiles" / sys.argv[ai + 1].split("@")[0] / "config.yaml"
            if _prof_cfg.exists():
                AGENT_CFG = _prof_cfg
    except Exception:
        pass
SUBS_FILE   = AGENT_HOME / "webhook_subscriptions.json"
PROFILES_DIR = AGENT_HOME / "profiles"
ROUTES_FILE = BRIDGE_DIR / "aimail_routes.toml"

# Agent-scoped paths (require --agent argument for per-agent data)

# ── TOML-like parser (bare keys + sections) ────────────────────
def _parse_toml(text: str) -> dict[str, dict[str, str]]:
    """Parse a minimal TOML subset: bare top-level keys + [section] blocks."""
    data: dict[str, dict[str, str]] = {"__top__": {}}
    cur = "__top__"
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r'^\[(\w+)\]$', s)
        if m:
            cur = m.group(1)
            data.setdefault(cur, {})
            continue
        if "=" in s:
            k, v = s.split("=", 1)
            data.setdefault(cur, {})[k.strip()] = v.strip().strip('"').strip("'")
    return data


# ── Check record ───────────────────────────────────────────────
class Check:
    def __init__(self):
        self.checks: list[dict] = []
        self.verbose = False

    def add(self, level: str, name: str, ok: bool, detail: str, fix: str = ""):
        self.checks.append({
            "level": level, "check": name,
            "pass": ok, "detail": detail, "fix": fix,
        })

    def all_pass(self) -> bool:
        return all(c["pass"] for c in self.checks)

    def print_table(self):
        groups = [
            ("system (check scope)",  [c for c in self.checks if c["level"] == "system"]),
            ("config (config files: gateway/bridge/agent)",  [c for c in self.checks if c["level"] == "config"]),
            ("aimail-gateway (external mail gateway)", [c for c in self.checks if c["level"] == "gateway"]),
            ("aimail-bridge (local NAT traversal bridge)",  [c for c in self.checks if c["level"] == "bridge"]),
            ("runtime (platform resources: patches/skills/plugin)",  [c for c in self.checks if c["level"] == "runtime"]),
            ("agent (platform config + hook interface)",  [c for c in self.checks if c["level"] == "agent"]),
            ("agent-gateway (Hermes gateway)",  [c for c in self.checks if c["level"] == "agent-gw"]),
            ("agent-profile (agent entity)",   [c for c in self.checks if c["level"] == "profile"]),
        ]
        for title, items in groups:
            if not items:
                continue
            print(f"\n  {BROWN}╓─ {title}{NC}")
            for chk in items:
                ik = GREEN + CHECK + NC if chk["pass"] else RED + CROSS + NC
                print(f"  {ik} {chk['check']}: {chk['detail']}")
                if self.verbose and not chk["pass"] and chk.get("fix"):
                    print(f"     {YELLOW}→{NC} {chk['fix']}")

    def print_json(self):
        print(json.dumps({
            "all_pass": self.all_pass(),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "checks": self.checks,
        }, indent=2, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════
#  Platform adapters (generic check tool — L3/L4 vary per platform)
# ═══════════════════════════════════════════════════════════════
# Each adapter provides:
#   name()                    → platform id ("hermes"/"openclaw"/...)
#   detect()                  → bool: is this platform present on this host?
#   list_agents()             → [{name, email, cfg, api_key, ...}] agents to check
#   check_config(c, agent)    → L3: name&apikey/webhook/skill/toolset/register
#   check_hook(c, agent)      → L4: inbound mail hook interface probe
# Shared L1 gateway / L2 bridge / L5 ping-pong are platform-independent.

def _detect_agent_type() -> str:
    """Probe the host for a supported agent platform. Returns id or 'unknown'.

    探测优先级(2026-08-16 双平台共存机器实测):
    ① --agent-home 被显式指定(出现在 argv)→ 视为 Hermes 意图
       (Hermes 是唯一用 agent-home 定位的平台;OpenClaw 固定 ~/.openclaw)。
       注意:即使值恰是默认 ~/.hermes 也算显式(aimail CLI 的 check
       传 --home 平台根时会落到这里)——只看参数是否出现,不看值。
    ② ~/.openclaw/openclaw.json 存在 → openclaw
    ③ ~/.dsh(profiles/) 存在 → dsh
    ④ ~/.pi(agent/) 存在 → pi
    ⑤ AGENT_HOME 下有 hermes-agent 或 profiles/ → hermes
    ⑥ deer-flow 目录特征 → deerflow(仅 detect;L3/L4 adapter 远端适用)
    ⑦ unknown
    """
    if "--agent-home" in sys.argv:
        return "hermes"
    if (Path.home() / ".openclaw" / "openclaw.json").is_file():
        return "openclaw"
    if (Path.home() / ".dsh").is_dir() and (Path.home() / ".dsh" / "profiles").is_dir():
        return "dsh"
    if (Path.home() / ".pi").is_dir() and (Path.home() / ".pi" / "agent").is_dir():
        return "pi"
    if (AGENT_HOME / "hermes-agent").exists() or (AGENT_HOME / "profiles").is_dir():
        return "hermes"
    if (AGENT_HOME / "backend" / "app" / "gateway").is_dir():
        return "deerflow"
    return "unknown"


# ── Hermes adapter ─────────────────────────────────────────────
def _hermes_detect() -> bool:
    return (AGENT_HOME / "hermes-agent").exists() or (AGENT_HOME / "profiles").is_dir()


def _hermes_list_agents() -> list[dict]:
    """Hermes: one agent per profile (+ default root profile).

    两种布局:
    - AGENT_HOME = Hermes 根(~/.hermes):default 根 profile + profiles/*/
    - AGENT_HOME = 某 profile 目录(如 ~/.hermes/profiles/aimail,
      check_status --agent-home 常用):该目录自身即 agent
    """
    agents = []
    is_profile_dir = (AGENT_HOME / ".agentmail").is_file() and not (AGENT_HOME / "profiles").is_dir()

    if is_profile_dir:
        # AGENT_HOME 即 profile:自身是一个 agent
        ptr = AGENT_HOME / ".agentmail"
        email = ""
        try:
            email = json.loads(ptr.read_text()).get("email", "")
        except Exception:
            pass
        agents.append({
            "name": AGENT_HOME.name, "email": email,
            "profile_dir": AGENT_HOME,
            "config": AGENT_HOME / "config.yaml",
        })
        return agents

    # Hermes 根布局:default 根 profile
    ptr = AGENT_HOME / ".agentmail"
    if ptr.is_file():
        try:
            d = json.loads(ptr.read_text())
            agents.append({
                "name": "default", "email": d.get("email", ""),
                "profile_dir": AGENT_HOME,
                "config": AGENT_HOME / "config.yaml",
            })
        except Exception:
            pass
    # Named profiles — 只保留有 amail 标记的(与 ensure_webhook_config 的
    # is_amail_profile 同逻辑):.agentmail 指针或 config 有 aimail 痕迹;
    # 无关 profile(erp/qlbio 等)不报 MISSING 噪音(2026-08-16 实测 22 issue)。
    profiles_dir = AGENT_HOME / "profiles"
    if profiles_dir.is_dir():
        for pdir in sorted(profiles_dir.iterdir()):
            if not pdir.is_dir():
                continue
            ptr = pdir / ".agentmail"
            email = ""
            try:
                email = json.loads(ptr.read_text()).get("email", "")
            except Exception:
                pass
            if not email:
                # 无指针:检查 config 是否有 agentmail 工具标记(内部标识)
                cfg_p = pdir / "config.yaml"
                if cfg_p.exists():
                    try:
                        import yaml
                        pt = (yaml.safe_load(cfg_p.read_text()) or {}).get("platform_toolsets", {})
                        for seg in ("webhook", "cli"):
                            tools = pt.get(seg) or []
                            if "agentmail" in (tools if isinstance(tools, list) else []):
                                email = "?"  # 有 agentmail 工具标记,列入
                                break
                    except Exception:
                        pass
                if not email:
                    continue  # 无关 profile,跳过
            agents.append({
                "name": pdir.name, "email": email,
                "profile_dir": pdir,
                "config": pdir / "config.yaml",
            })
    return agents


def _hermes_check_config(c: Check, agent: dict):
    """L3 Hermes: name&apikey / webhook / skill / toolset / register."""
    name = agent.get("name", "?")
    pd = agent.get("profile_dir")
    cfg = agent.get("config")
    email = agent.get("email", "")

    # 3.1 name & api_key: 该 agent 的 agentmail.json
    sid = _resolve_system_id()
    aj_path = (SYSTEMS_DIR / sid / _clean_agent_dir_name(email) / "agentmail.json") if (sid and email) else None
    api_key = ""
    if aj_path and aj_path.is_file():
        try:
            api_key = json.loads(aj_path.read_text()).get("api_key", "")
        except Exception:
            pass
    ok = bool(email and api_key)
    c.add("agent", "name_apikey", ok,
          f"{name}: {email or 'no email'}" + (", api_key ✓" if api_key else ", api_key MISSING"),
          "Run: python -m aimail.install install --type hermes (SDK register)")

    # 3.2 webhook: profile config platforms.webhook + route secret
    wh_ok = False
    try:
        import yaml
        if cfg and cfg.exists():
            wh = (yaml.safe_load(cfg.read_text()) or {}).get("platforms", {}).get("webhook", {})
            wh_ok = bool(wh.get("enabled") and wh.get("extra", {}).get("secret"))
    except Exception:
        pass
    c.add("agent", "webhook", wh_ok,
          f"{name}: webhook " + ("enabled + secret ✓" if wh_ok else "MISSING (platforms.webhook)"),
          "Run: python -m aimail.install install --type hermes (SDK 补全配置)")

    # 3.3 skill: profile skills/agentmail/ (internal tool name = agentmail)
    skill_dir = pd / "skills" / "agentmail" if pd else None
    skill_ok = bool(skill_dir and skill_dir.is_dir())
    c.add("agent", "skill", skill_ok,
          f"{name}: skills/agentmail " + ("✓" if skill_ok else "MISSING"),
          "Run install-skill or copy skills/SKILL.md")

    # 3.4 toolset: platform_toolsets.webhook/cli 含 agentmail(内部标识,
    # 2026-09-03 d6d035d ruling: 改名只动外部品牌 aimail,此处键不变)
    ts_ok = False
    try:
        import yaml
        if cfg and cfg.exists():
            pt = (yaml.safe_load(cfg.read_text()) or {}).get("platform_toolsets", {})
            for seg in ("webhook", "cli"):
                tools = pt.get(seg) or []
                if "agentmail" in tools:
                    ts_ok = True
                    break
    except Exception:
        pass
    c.add("agent", "toolset", ts_ok,
          f"{name}: platform_toolsets " + ("含 agentmail ✓" if ts_ok else "MISSING"),
          "Run: python -m aimail.install install --type hermes (SDK webhook config)")

    # 3.5 register: 云端注册(api_key 存在 + webhook route 存在)
    route_ok = False
    subs = pd / "webhook_subscriptions.json" if pd else None
    if subs and subs.exists():
        try:
            routes = json.loads(subs.read_text())
            route_ok = any("aimail" in k.lower() for k in (routes or {}))
        except Exception:
            pass
    reg_ok = bool(api_key and route_ok)
    c.add("agent", "register", reg_ok,
          f"{name}: " + ("registered ✓" if reg_ok else "api_key/route 不全"),
          "Run: python -m aimail.install install --type hermes")


def _hermes_check_hook(c: Check, agent: dict):
    """L4 Hermes: POST /webhooks/aimail-inbound — route registered & responsive."""
    port = 8646
    try:
        import yaml
        cfg = agent.get("config")
        if cfg and cfg.exists():
            wh = (yaml.safe_load(cfg.read_text()) or {}).get("platforms", {}).get("webhook", {})
            port = int(wh.get("port") or wh.get("extra", {}).get("port") or 8646)
    except Exception:
        pass
    route_name = "aimail-inbound"
    url = f"http://127.0.0.1:{port}/webhooks/{route_name}"
    payload = json.dumps({
        "message": "status-check",
        "from": "check_status@localhost",
        "subject": "amail connectivity probe",
    }).encode()
    try:
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            c.add("agent", "hook", True, f"POST {route_name} → HTTP {r.status}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):  # route 存在 + 验签保护
            c.add("agent", "hook", True,
                  f"POST {route_name} → {e.code} (route active, HMAC required)")
        elif e.code == 404:
            c.add("agent", "hook", False, f"POST {route_name} → 404 route missing",
                  "Run: aimail install (注册链重跑)或 python -m aimail.install")
        else:
            c.add("agent", "hook", False, f"POST {route_name} → HTTP {e.code}")
    except Exception as e:
        c.add("agent", "hook", False, f"Cannot reach {url}: {e}",
              "Start Hermes gateway with --accept-hooks")


# ── OpenClaw adapter ───────────────────────────────────────────
def _openclaw_detect() -> bool:
    return (Path.home() / ".openclaw" / "openclaw.json").is_file()


def _openclaw_list_agents() -> list[dict]:
    """OpenClaw: one agent per ~/.openclaw/agents/{id}."""
    agents = []
    agents_dir = Path.home() / ".openclaw" / "agents"
    # OpenClaw 系统指针在 ~/.openclaw/.agentmail(不依赖 AGENT_HOME)
    sid = ""
    ptr = Path.home() / ".openclaw" / ".agentmail"
    if ptr.is_file():
        try:
            sid = json.loads(ptr.read_text()).get("system_id", "")
        except Exception:
            pass
    if not sid:
        sid = _resolve_system_id()
    if agents_dir.is_dir():
        for adir in sorted(agents_dir.iterdir()):
            if not adir.is_dir():
                continue
            email = ""
            # amail email 从系统 agentmail.json 反查(agent_id 匹配)
            sysdir = SYSTEMS_DIR / sid
            if sysdir.is_dir():
                for sub in sysdir.iterdir():
                    aj = sub / "agentmail.json"
                    if aj.is_file():
                        try:
                            d = json.loads(aj.read_text())
                            if d.get("agent_id") == adir.name:
                                email = d.get("email", "")
                                break
                        except Exception:
                            pass
            agents.append({
                "name": adir.name, "email": email,
                "agent_dir": adir,
                "config": Path.home() / ".openclaw" / "openclaw.json",
            })
    return agents


def _openclaw_check_config(c: Check, agent: dict):
    """L3 OpenClaw: name&apikey / webhook / skill / toolset / register."""
    name = agent.get("name", "?")
    email = agent.get("email", "")

    # 3.1 name & api_key(sid 解析:argv 显式 > OpenClaw 指针 > 默认)
    sid = _resolve_system_id()
    if not sid or sid == os.environ.get("SYSTEM_ID", ""):
        ptr = Path.home() / ".openclaw" / ".agentmail"
        if ptr.is_file():
            try:
                sid = json.loads(ptr.read_text()).get("system_id", "")
            except Exception:
                pass
    if not sid:
        sid = _resolve_system_id()
    aj_path = (SYSTEMS_DIR / sid / _clean_agent_dir_name(email) / "agentmail.json") if (sid and email) else None
    api_key = ""
    if aj_path and aj_path.is_file():
        try:
            api_key = json.loads(aj_path.read_text()).get("api_key", "")
        except Exception:
            pass
    ok = bool(email and api_key)
    c.add("agent", "name_apikey", ok,
          f"{name}: {email or 'no email'}" + (", api_key ✓" if api_key else ", api_key MISSING"),
          "Run register_agent.py --all")

    # 3.2 webhook: agentmail.json webhook_secret
    wh_ok = False
    if aj_path and aj_path.is_file():
        try:
            wh_ok = bool(json.loads(aj_path.read_text()).get("webhook_secret"))
        except Exception:
            pass
    c.add("agent", "webhook", wh_ok,
          f"{name}: webhook_secret " + ("✓" if wh_ok else "MISSING"),
          "Re-run register_agent.py (persists webhook_secret)")

    # 3.3 skill: ~/.openclaw/skills/agentmail/
    skill_ok = (Path.home() / ".openclaw" / "skills" / "agentmail").is_dir()
    c.add("agent", "skill", skill_ok,
          f"{name}: skills/agentmail " + ("✓" if skill_ok else "MISSING"),
          "Run install-skill.sh")

    # 3.4 toolset: openclaw-aimail TS plugin registered in the gateway config
    # (plugins.entries / plugins.allow). No Python/OpenClaw edition ever
    # shipped, so no MCP coexistence check is needed.
    ts_ok = False
    try:
        oc_path = agent.get("config")
        if oc_path and oc_path.exists():
            oc = json.loads(oc_path.read_text())
            plugins = oc.get("plugins", {})
            entries = plugins.get("entries", {}) if isinstance(plugins, dict) else {}
            allow = plugins.get("allow", []) if isinstance(plugins, dict) else []
            installed_root = Path.home() / ".openclaw" / "npm" / "projects"
            installed = any(installed_root.glob("openclaw-aimail*"))
            ts_ok = installed and ("openclaw-aimail" in entries or "openclaw-aimail" in allow or not entries)
    except Exception:
        pass
    c.add("agent", "toolset", ts_ok,
          f"{name}: openclaw-aimail plugin " + ("✓" if ts_ok else "MISSING"),
          "openclaw plugins install npm-pack:<openclaw-aimail.tgz>; restart gateway")

    # 3.5 register
    reg_ok = bool(api_key)
    c.add("agent", "register", reg_ok,
          f"{name}: " + ("registered ✓" if reg_ok else "api_key MISSING"),
          "Run register_agent.py --all")


def _openclaw_check_hook(c: Check, agent: dict):
    """L4 OpenClaw: plugin gateway endpoint probe (POST /aimail/inbound on
    the OpenClaw gateway, auth:"plugin" — plugin-managed HMAC verification).

    Endpoint path truth: tssdk openclaw-aimail/src/inbound.ts INBOUND_PATH
    = /aimail/inbound (registered on the gateway HTTP server in index.ts).
    Legacy adapter (amail_openclaw_bridge.py :8799/hook) and the old
    /aimail/deliver path are retired.
    """
    # Discover the gateway port from openclaw.json (default 18789).
    port = 18789
    try:
        oc_path = agent.get("config")
        if oc_path and oc_path.exists():
            oc = json.loads(oc_path.read_text())
            port = int((oc.get("gateway") or {}).get("port") or 18789)
    except Exception:
        pass
    url = f"http://127.0.0.1:{port}/aimail/inbound"
    payload = json.dumps({"to": ["probe@invalid"], "body": "status-check"}).encode()
    try:
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read().decode()[:80]
            # 200 with a JSON status = plugin route alive (no_agent/bad_signature
            # are both valid — the endpoint processed the request).
            c.add("agent", "hook", True, f"POST /aimail/inbound → HTTP {r.status} {body}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # 2026-09-04 semantics fix: 404 = route NOT registered (plugin
            # missing/disabled). "probe@invalid" with a body can never 404 on
            # a live route — a live handler answers 400/401/405 at worst.
            c.add("agent", "hook", False,
                  "POST /aimail/inbound → 404 (route not registered)",
                  "Reinstall: openclaw plugins install npm-pack:<openclaw-aimail.tgz>; restart gateway")
        elif e.code in (200, 400, 401, 405):
            # 400 (bad body) / 401 (signature) / 405 prove the route exists
            c.add("agent", "hook", True, f"POST /aimail/inbound → {e.code} (plugin route active)")
        else:
            c.add("agent", "hook", False, f"POST /aimail/inbound → HTTP {e.code}")
    except Exception as e:
        c.add("agent", "hook", False, f"Cannot reach {url}: {e}",
              f"Is the OpenClaw gateway running on :{port} with the openclaw-aimail plugin installed?")


# ── dsh adapter ────────────────────────────────────────────────
def _dsh_detect() -> bool:
    return (Path.home() / ".dsh").is_dir()


def _dsh_list_agents() -> list[dict]:
    """dsh: agents = agentmail.json 含 session_id 的绑定(每 session 一地址)。"""
    agents = []
    sid = _resolve_system_id()
    sysdir = SYSTEMS_DIR / sid if sid else Path()
    if sysdir.is_dir():
        for sub in sorted(sysdir.iterdir()):
            aj = sub / "agentmail.json"
            if not aj.is_file():
                continue
            try:
                d = json.loads(aj.read_text())
            except Exception:
                continue
            if d.get("session_id"):
                agents.append({
                    "name": d["session_id"][:8], "email": d.get("email", ""),
                    "session_id": d.get("session_id", ""), "preset": d.get("preset", ""),
                    "config": aj,
                })
    return agents


def _dsh_check_config(c: Check, agent: dict):
    """L3 dsh: name&apikey / webhook 成对 / session_id / preset / register。"""
    name = agent.get("name", "?")
    email = agent.get("email", "")
    session_id = agent.get("session_id", "")
    aj = agent.get("config")

    api_key = ""
    wh_url = ""
    wh_secret = ""
    preset = ""
    if aj and aj.is_file():
        try:
            d = json.loads(aj.read_text())
            api_key = d.get("api_key", "")
            wh_url = d.get("webhook_url", "")
            wh_secret = d.get("webhook_secret", "")
            preset = d.get("preset", "")
        except Exception:
            pass
    ok = bool(email and api_key)
    c.add("agent", "name_apikey", ok,
          f"{name}: {email or 'no email'}" + (", api_key ✓" if api_key else ", api_key MISSING"),
          "Run scripts/dsh/bind_agent.py")

    wh_ok = bool(wh_url and wh_secret)
    c.add("agent", "webhook", wh_ok,
          f"webhook_url={wh_url or '(缺)'}" + (", secret ✓" if wh_secret else ", secret MISSING"),
          "bind_agent.py 落盘 webhook_url + webhook_secret")

    sess_ok = bool(session_id and preset)
    c.add("agent", "session", sess_ok,
          f"session_id={session_id or '(缺)'}, preset={preset or '(缺)'}",
          "bind_agent.py 落盘 session_id/preset;dsh 侧创建同名 session(加入 mail preset)")


def _pi_detect() -> bool:
    return (Path.home() / ".pi").is_dir() and (Path.home() / ".pi" / "agent").is_dir()


def _pi_list_agents() -> list[dict]:
    """pi: agents = pi 平台 agent 目录 + ~/.pi/.agentmail 指针 email(与 openclaw 同构)。"""
    agents = []
    ptr = Path.home() / ".pi" / ".agentmail"
    email = ""
    if ptr.is_file():
        try:
            email = json.loads(ptr.read_text()).get("email", "")
        except Exception:
            pass
    agents_dir = Path.home() / ".pi" / "agent"
    if agents_dir.is_dir():
        found = False
        for adir in sorted(agents_dir.iterdir()):
            if not adir.is_dir():
                continue
            agents.append({
                "name": adir.name, "email": email,
                "agent_dir": adir,
                "config": Path.home() / ".pi" / "agent" / "config.json",
            })
            found = True
        if not found and email:
            # 指针有 email 但 agent 目录结构未知 → 单 agent 视图(仅指针对齐检查)
            agents.append({"name": "pi", "email": email,
                           "agent_dir": None, "config": Path.home() / ".pi" / "agent" / "config.json"})
    elif email:
        agents.append({"name": "pi", "email": email,
                       "agent_dir": None, "config": None})
    return agents


def _pi_check_config(c: Check, agent: dict):
    """L3 pi: name&apikey(agentmail.json)/pointer 对齐。pi-aimail TS 扩展承载工具+入站。"""
    name = agent.get("name", "?")
    email = agent.get("email", "")
    sid = _resolve_system_id()
    aj_path = (SYSTEMS_DIR / sid / _clean_agent_dir_name(email) / "agentmail.json") if (sid and email) else None
    api_key = ""
    if aj_path and aj_path.is_file():
        try:
            api_key = json.loads(aj_path.read_text()).get("api_key", "")
        except Exception:
            pass
    ok = bool(email and api_key)
    c.add("agent", "name_apikey", ok,
          f"{name}: {email or 'no email'}" + (", api_key ✓" if api_key else ", api_key MISSING"),
          "Re-run: aimail install --home ~/.pi (register pi agent)")
    ptr = Path.home() / ".pi" / ".agentmail"
    try:
        ptr_ok = ptr.is_file() and json.loads(ptr.read_text()).get("system_id") == sid
    except Exception:
        ptr_ok = False
    c.add("agent", "pointer", ptr_ok,
          ".pi pointer matches system" if ptr_ok else ".pi pointer missing/mismatch",
          "Re-run: aimail install --home ~/.pi")


def _pi_check_hook(c: Check, agent: dict):
    """L4 pi: POST {agentmail.json webhook_url} — pi-aimail inbound 端点探测。"""
    email = agent.get("email", "")
    sid = _resolve_system_id()
    wh_url = ""
    aj_path = (SYSTEMS_DIR / sid / _clean_agent_dir_name(email) / "agentmail.json") if (sid and email) else None
    if aj_path and aj_path.is_file():
        try:
            wh_url = json.loads(aj_path.read_text()).get("webhook_url", "")
        except Exception:
            pass
    if not wh_url:
        c.add("agent", "hook", False, "no webhook_url (agentmail.json)",
              "Re-run: aimail install --home ~/.pi")
        return
    from urllib.parse import urlparse as _up
    host = _up(wh_url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        # 远端宿主(pi 平台常见)本机不可探测 → 不算 FAIL
        c.add("agent", "hook", True, f"webhook remote host (not probeable locally): {wh_url}")
        return
    try:
        req = urllib.request.Request(wh_url, data=b"{}", method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            c.add("agent", "hook", True, f"POST {wh_url} → HTTP {r.status}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            c.add("agent", "hook", True, f"POST {wh_url} → {e.code} (receiver active)")
        elif e.code == 404:
            c.add("agent", "hook", False, f"POST {wh_url} → 404", "Start pi-aimail inbound")
        else:
            c.add("agent", "hook", False, f"POST {wh_url} → HTTP {e.code}")
    except Exception as e:
        c.add("agent", "hook", False, f"Cannot reach {wh_url}: {e}")


def _dsh_check_hook(c: Check, agent: dict):
    """L4 dsh: POST webhook_url — 200/401 = 接收端点活跃(mail-inbound)。"""
    aj = agent.get("config")
    wh_url = ""
    if aj and aj.is_file():
        try:
            wh_url = json.loads(aj.read_text()).get("webhook_url", "")
        except Exception:
            pass
    if not wh_url:
        c.add("agent", "hook", False, "webhook_url 缺失",
              "bind_agent.py 落盘 webhook_url(mail-inbound 端点)")
        return
    try:
        req = urllib.request.Request(
            wh_url, data=b"{}", headers={"Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            c.add("agent", "hook", True, f"POST {wh_url} → HTTP {r.status}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            c.add("agent", "hook", True, f"POST {wh_url} → {e.code} (receiver active)")
        elif e.code == 404:
            c.add("agent", "hook", False, f"POST {wh_url} → 404",
                  "Start dsh mail-inbound plugin on the endpoint port")
        else:
            c.add("agent", "hook", False, f"POST {wh_url} → HTTP {e.code}")
    except Exception as e:
        c.add("agent", "hook", False, f"Cannot reach {wh_url}: {e}",
              "Start dsh mail-inbound plugin on the endpoint port")


PLATFORMS = {
    "hermes": {
        "detect": _hermes_detect,
        "list_agents": _hermes_list_agents,
        "check_config": _hermes_check_config,
        "check_hook": _hermes_check_hook,
    },
    "openclaw": {
        "detect": _openclaw_detect,
        "list_agents": _openclaw_list_agents,
        "check_config": _openclaw_check_config,
        "check_hook": _openclaw_check_hook,
    },
    "dsh": {
        "detect": _dsh_detect,
        "list_agents": _dsh_list_agents,
        "check_config": _dsh_check_config,
        "check_hook": _dsh_check_hook,
    },
    "pi": {
        "detect": _pi_detect,
        "list_agents": _pi_list_agents,
        "check_config": _pi_check_config,
        "check_hook": _pi_check_hook,
    },
    "deerflow": None,  # 平台宿主通常远端;L3/L4 由 deer-flow SDK reconcile 自证
}


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════
def _signed_headers(api_key: str, method: str, path: str,
                    data: bytes | None = None,
                    identity: str = "") -> dict:
    """v1 API 签名头（自包含实现，与 pysdk/aimail_base.compute_api_signature
    同一协议：HMAC key = sha256(raw_key)，base = METHOD\\npath\\nts\\nsha256(body)）。
    check_status 离线自包含，不依赖 pysdk/ 核心。"""
    import hashlib, hmac as _hmac, time as _time
    h = {"Content-Type": "application/json"}
    if identity:
        h["X-Api-Identity"] = identity
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    ts = str(int(_time.time() * 1000))
    body_hash = hashlib.sha256(data or b"").hexdigest()
    base = f"{method}\n{path}\n{ts}\n{body_hash}"
    h["X-Api-Timestamp"] = ts
    h["X-Api-Signature"] = _hmac.new(key_hash.encode(), base.encode(),
                                     hashlib.sha256).hexdigest()
    return h


def _signed_get_headers(api_key: str, path: str = "/api/v1/whoami",
                        identity: str = "") -> dict:
    return _signed_headers(api_key, "GET", path, identity=identity)


def _signed_post_headers(api_key: str, path: str, data: bytes,
                         identity: str = "") -> dict:
    return _signed_headers(api_key, "POST", path, data, identity=identity)


def _json_req(url: str, headers: dict | None = None,
              data: bytes | None = None, method: str | None = None,
              timeout: int = 10) -> tuple[int, dict | list]:
    """HTTP request returning (status_code, parsed_json)."""
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def _resolve_platform_sid(agent_type: str) -> str:
    """平台相关 system_id:Hermes = AGENT_HOME 指针;OpenClaw =
    ~/.openclaw/.agentmail 指针。--system-id 显式指定优先。"""
    if "--system-id" in sys.argv:
        try:
            ai = sys.argv.index("--system-id")
            if ai + 1 < len(sys.argv):
                return sys.argv[ai + 1]
        except (ValueError, IndexError):
            pass
    if agent_type == "openclaw":
        ptr = Path.home() / ".openclaw" / ".agentmail"
    else:
        ptr = AGENT_HOME / ".agentmail"
    if ptr.is_file():
        try:
            return json.loads(ptr.read_text()).get("system_id", "")
        except Exception:
            pass
    return os.environ.get("SYSTEM_ID", "")


def _read_gw_cfg(sid: str = "") -> dict | None:
    """Load ~/.aimail/system-{sid}/aimail_gateway.json, return None on failure."""
    if not sid:
        sid = _resolve_system_id(sys.argv)
    p = _system_agent_path(sid) if sid else SYSTEMS_DIR / "aimail_gateway.json"
    if not p.exists() or not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  Level 1: aimail-gateway (external mail gateway)
# ═══════════════════════════════════════════════════════════════
def check_gateway(c: Check, sid: str = ""):
    """aimail-gateway: health + SMTP port + API credentials"""
    cfg = _read_gw_cfg(sid)
    if not cfg:
        c.add("gateway", "config", False,
              "aimail_gateway.json not found",
              "Run: aimail init + aimail install (配置网关与系统)")
        return

    gw_url = cfg.get("gateway_url", "").rstrip("/")
    ak = cfg.get("admin_key", "")
    if not gw_url:
        c.add("gateway", "config", False,
              "gateway_url is empty in config", "Re-run: aimail install")
        return

    # 1.1 Health
    code, body = _json_req(f"{gw_url}/health")
    if code == 200:
        uptime = body.get("uptime_secs", "?") if isinstance(body, dict) else "?"
        c.add("gateway", "health", True, f"HTTP {code}, uptime {uptime}s")
    else:
        err = body.get("error", body) if isinstance(body, dict) else str(body)
        c.add("gateway", "health", False, f"HTTP {code}: {err}",
              "Start aimail-gateway service on the gateway server")
        return

    # 1.2 SMTP port 25
    host = gw_url.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((host, 25))
        banner = s.recv(256).decode(errors="replace").strip()
        s.close()
        c.add("gateway", "smtp_port", True, f"Port 25 open, banner: {banner[:60]}")
    except Exception as e:
        c.add("gateway", "smtp_port", False,
              f"Port 25 unreachable: {e}",
              "Check firewall and aimail-gateway SMTP listener")

    # 1.3 API key scope
    if not ak:
        c.add("gateway", "api_key", False,
              "No admin_key configured",
              "Run: aimail install --with-key (或 AIMAIL_ADMIN_KEY 激活)")
        return
    code, data = _json_req(f"{gw_url}/api/v1/whoami",
                           headers=_signed_get_headers(ak,
                                                       identity=cfg.get("system_id", "")))
    if code == 200:
        scope = data.get("scope", "")
        cat = data.get("category", "")
        sid = data.get("system_id", "")
        # agent_admin 是集成后 config 中的标准 key 类别(9dca44e 架构)
        ok = ("platform" in scope or "system" in scope
              or "agent_admin" in scope or cat == "agent_admin")
        c.add("gateway", "api_key", ok,
              f"scope={scope}, category={cat}, system_id={sid[:16]}..." if ok else
              f"scope={scope} — need platform/system/agent_admin",
              "Use a key with platform, system or agent_admin scope")
    else:
        c.add("gateway", "api_key", False,
              f"whoami HTTP {code}", "Check admin_key is correct")


# ═══════════════════════════════════════════════════════════════
#  Level 2: aimail-bridge (local NAT traversal bridge)
#  Optional component. If config not found, bridge is simply
#  not deployed (gateway → agent-gateway directly).
#  May run on a different machine in the LAN.
# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
# L0: 配置文件完备性与正确性(2026-09-04 全面体检;用户定调:
# check = 全面体检,覆盖 gateway/bridge/agent 三份配置 + 跨文件一致)
# ═══════════════════════════════════════════════════════════════

AGENTMAIL_JSON_REQUIRED = (
    "email", "gateway_url", "domain", "system_id", "system_name",
    "manager_address", "api_key", "webhook_url", "webhook_secret",
)


def _check_l0_configs(c: Check, sid: str):
    """L0 配置层:gateway.json 完备性 + system_home + pointer 归属。"""
    gw = _read_gw_cfg(sid)
    if not gw:
        c.add("config", "gateway_json", False,
              "aimail_gateway.json missing/unreadable",
              "Run: aimail install --home <platform-root> --system-id " + (sid or "<sid>"))
        return

    # 1. 连接字段完备
    missing = [k for k in ("gateway_url", "admin_key") if not gw.get(k)]
    c.add("config", "complete", not missing,
          "gateway_url + admin_key present" if not missing
          else f"missing: {', '.join(missing)}",
          "Run: aimail install --system-id " + sid)

    # 2. system_home 存在且目录有效
    sh = gw.get("system_home", "")
    if not sh:
        c.add("config", "system_home", False,
              "system_home MISSING (stats platform label → [?])",
              "Run: aimail install --home <platform-root> --system-id " + sid)
    elif not Path(sh).is_dir():
        c.add("config", "system_home", False,
              f"system_home dir does not exist: {sh}",
              "Fix path or re-run: aimail install --home <platform-root> --system-id " + sid)
    else:
        c.add("config", "system_home", True, sh)

    # 3. pointer 归属(任一平台 .agentmail 引用该 sid 即可)
    ptr_hit = _pointer_platforms_for_sid(sid)
    c.add("config", "pointer", bool(ptr_hit),
          "pointer: " + ",".join(ptr_hit) if ptr_hit
          else "no platform .agentmail pointer references this system",
          "Re-run platform install or aimail repair --system-id " + sid)


def _pointer_platforms_for_sid(sid: str) -> list:
    """Which platform pointers reference sid (hermes/openclaw/deerflow/pi/dsh)."""
    hits = []
    home = Path.home()
    cand = [
        ("openclaw", home / ".openclaw" / ".agentmail"),
        ("deerflow", home / ".deer-flow" / ".agentmail"),
        ("pi",       home / ".pi" / ".agentmail"),
        ("dsh",      home / ".dsh" / ".agentmail"),
    ]
    hermes_root = home / ".hermes" / ".agentmail"
    if hermes_root.is_file():
        cand.append(("hermes", hermes_root))
    profiles = home / ".hermes" / "profiles"
    if profiles.is_dir():
        for pp in sorted(profiles.glob("*/.agentmail")):
            cand.append(("hermes", pp))
    for plat, ptr in cand:
        if ptr.is_file():
            try:
                if json.loads(ptr.read_text()).get("system_id") == sid:
                    hits.append(plat)
            except Exception:
                pass
    return hits


def _check_agentmail_json(c: Check, sid: str):
    """L0 扩展:逐 agent agentmail.json 完备性 + 内部一致性。"""
    sysdir = SYSTEMS_DIR / sid
    if not sysdir.is_dir():
        return
    for sub in sorted(sysdir.iterdir()):
        aj = sub / "agentmail.json"
        if not aj.is_file():
            continue
        try:
            d = json.loads(aj.read_text())
        except Exception as e:
            c.add("agent", "config-json", False, f"{sub.name}: parse error: {e}",
                  "Re-run register_agent.py --all")
            continue
        name = d.get("agent_id") or sub.name
        missing = [k for k in AGENTMAIL_JSON_REQUIRED if k not in d or d.get(k) in ("", None)]
        c.add("agent", "config-complete", not missing,
              f"{name}: all {len(AGENTMAIL_JSON_REQUIRED)} fields present" if not missing
              else f"{name}: missing fields: {', '.join(missing)}",
              "Re-run register_agent.py --all (api_key/webhook_secret cannot be rebuilt locally)")

        # 内部一致性:system_id / gateway_url / domain vs email
        issues = []
        if d.get("system_id") and d.get("system_id") != sid:
            issues.append(f"system_id {d['system_id'][:16]}… != {sid[:16]}…")
        gwc = _read_gw_cfg(sid) or {}
        if d.get("gateway_url") and gwc.get("gateway_url") \
                and d["gateway_url"].rstrip("/") != gwc["gateway_url"].rstrip("/"):
            issues.append("gateway_url differs from aimail_gateway.json")
        email = d.get("email", "")
        if email and d.get("domain") and not email.endswith("@" + d["domain"]):
            issues.append(f"email domain != domain field ({d['domain']})")
        c.add("agent", "config-consistency", not issues,
              f"{name}: consistent" if not issues else f"{name}: " + "; ".join(issues),
              "Re-run register_agent.py --all")


def _check_bridge_completeness(c: Check, sid: str):
    """L0 扩展:aimail_bridge.toml 结构完备性 + routes 覆盖 + admin_key 一致。"""
    if not BRIDGE_CFG.exists():
        return  # direct-connect 合法,check_bridge 已报
    try:
        import tomllib
        with open(BRIDGE_CFG, "rb") as f:
            td = tomllib.load(f)
    except Exception as e:
        c.add("bridge", "config-complete", False, f"toml parse error: {e}",
              "Check aimail_bridge.toml syntax")
        return
    mode = td.get("mode", "")
    c.add("bridge", "config-mode", mode in ("push", "pull"),
          f"mode={mode}" if mode in ("push", "pull") else f"invalid mode: '{mode}'",
          "mode must be push or pull")

    if mode != "pull":
        return
    systems = (td.get("pull", {}) or {}).get("systems") or []
    gw = _read_gw_cfg(sid) or {}
    entry = next((x for x in systems if x.get("system_id") == sid), None) if sid else None
    if sid and gw:
        if entry is None:
            c.add("bridge", "pull-entry", False,
                  f"no pull.systems entry for {sid}",
                  "Run: aimail bridge --system-id " + sid)
        else:
            key_match = (entry.get("admin_key") or "") == (gw.get("admin_key") or "")
            c.add("bridge", "pull-entry", key_match,
                  f"pull entry present, admin_key {'match' if key_match else 'MISMATCH'}",
                  "Re-run: aimail bridge --system-id " + sid)

    # routes 覆盖:每个本系统 agent 的 email 在 ROUTES_FILE 有条目(pull 模式投递路径表)
    if not ROUTES_FILE.exists():
        return
    try:
        routes = {}
        for line in ROUTES_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                routes[k.strip().strip('"')] = v.strip().strip('"').strip(",")
    except Exception:
        return
    sysdir = SYSTEMS_DIR / sid
    if not sysdir.is_dir():
        return
    for sub in sorted(sysdir.iterdir()):
        aj = sub / "agentmail.json"
        if not aj.is_file():
            continue
        try:
            d = json.loads(aj.read_text())
        except Exception:
            continue
        email = d.get("email", "")
        if not email:
            continue
        name = d.get("agent_id") or sub.name
        target = routes.get(email)
        declared = d.get("webhook_url", "")
        if target is None:
            c.add("bridge", "routes-entry", False,
                  f"{name}: no route for {email} (pull delivery dead)",
                  "Run: aimail bridge --system-id " + sid)
            continue
        # 路由目标 vs 声明 webhook_url:探测判定(死端口 FAIL,双活不同路径 WARN)
        from urllib.parse import urlparse as _up
        _h = _up(target).hostname if target else ""
        _local = _h in ("127.0.0.1", "localhost", "::1")
        t_ok, _ = _probe_endpoint(target)
        if not t_ok and not _local:
            # 远端目标本机不可探测(pi 等远端宿主)→ WARN 不算 FAIL
            c.add("bridge", "routes-target", True,
                  f"{name}: route target remote (not probeable locally): {target}")
            continue
        d_ok, _ = _probe_endpoint(declared) if declared else (False, "empty")
        if not t_ok:
            c.add("bridge", "routes-target", False,
                  f"{name}: route target dead: {target}",
                  "Check the agent platform is running; do NOT auto-edit routes")
        elif declared and not d_ok:
            c.add("bridge", "routes-target", False,
                  f"{name}: declared webhook_url dead: {declared} (route target alive: {target})",
                  "Run: aimail repair --system-id " + sid)
        elif declared and target.rstrip("/") != declared.rstrip("/"):
            c.add("bridge", "routes-target", True,
                  f"{name}: paths differ but both alive (route={target} vs declared={declared}) — repair will align",
                  "Run: aimail repair --system-id " + sid)
        else:
            c.add("bridge", "routes-target", True, f"{name}: route target alive & consistent")


def _probe_endpoint(url: str, timeout: float = 3.0) -> tuple:
    """POST probe: (alive, detail). alive = TCP connect + HTTP response (any code != conn-refused).
    404 counts as NOT alive (route missing) — 2026-09-04 semantics fix."""
    if not url or not url.startswith("http"):
        return False, "no url"
    try:
        import urllib.request
        req = urllib.request.Request(url, data=b"", method="POST")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "HTTP 404 (route not found)"
        return True, f"HTTP {e.code}"  # 400/401/405 = endpoint alive (auth/body required)
    except Exception as e:
        return False, str(e)[:60]


def check_bridge(c: Check, sid: str = ""):
    """aimail-bridge: config + process + log + pull path (P0)

    sid 指定时:bridge 配置里的 pull.systems 按 sid 匹配该系统的条目
    (单 bridge 多系统,2026-08-16)。未指定则用 systems[0] 或扁平字段。
    """

    # 2.1 Config — this is the single source of truth for bridge existence
    if not BRIDGE_CFG.exists():
        # Bridge not deployed — this is valid for direct-connect setups
        c.add("bridge", "config", True, "not deployed (gateway → agent-gateway direct)")
        return

    try:
        td = _parse_toml(BRIDGE_CFG.read_text())
        mode   = td.get("__top__", {}).get("mode", "") or td.get("bridge", {}).get("mode", "")
        addr   = td.get("__top__", {}).get("addr", "") or td.get("bridge", {}).get("addr", "")
        amail_url = td.get("pull", {}).get("amail_url", "")
        poll_int  = td.get("pull", {}).get("poll_interval_sec", "")
        parts = [f"mode={mode}"]
        if addr:    parts.append(f"addr={addr}")
        if amail_url: parts.append(f"amail_url={amail_url}")
        if poll_int:  parts.append(f"poll={poll_int}s")
        c.add("bridge", "config", True, ", ".join(parts))
    except Exception as e:
        c.add("bridge", "config", False,
              f"Parse error: {e}", "Check aimail_bridge.toml syntax")
        return

    # Determine if bridge is running on this machine
    local_pid = _detect_local_bridge_pid()

    # 2.2 Process — only meaningful when bridge is local
    if local_pid:
        c.add("bridge", "process", True, f"Local PID={local_pid}")
    else:
        c.add("bridge", "process", True,
              "not on this machine (check addr or PID file for local)")

    # 2.3 Activity — only local
    if local_pid and BRIDGE_LOG.exists():
        _check_bridge_activity(c)
    elif local_pid:
        c.add("bridge", "activity", True, "running, no log yet (no emails processed)")
    else:
        c.add("bridge", "activity", True, "N/A — bridge is remote")

    # 2.4 [P0] Pull path: bridge → aimail-gateway (works remotely too)
    _check_bridge_pull_path(c, td, sid)

    # 2.5 [P1] Bridge self health (remote HTTP to bridge addr)
    if addr:
        _check_bridge_health(c, addr)

    # 2.6 Cross-config: bridge ↔ gateway consistency
    _check_bridge_gateway_consistency(c, td, sid)


def _check_bridge_gateway_consistency(c: Check, td: dict, sid: str = ""):
    """Cross-check bridge config fields against aimail_gateway.json.
    All config files are local (copied by deploy_bridge.py even when
    bridge runs remotely), so these checks always run when bridge is deployed.
    """
    gw = _read_gw_cfg(sid)
    if not gw:
        return  # gateway level will report its own error

    mismatches = []

    # 用标准 tomllib 重读 bridge 配置(自定义 _parse_toml 不支持数组)
    try:
        import tomllib
        with open(BRIDGE_CFG, "rb") as _f:
            _td = tomllib.load(_f)
    except Exception:
        _td = td

    # (A) pull.amail_url vs gateway_url(按 sid 匹配该系统的条目)
    pull_cfg = _td.get("pull", {})
    systems = pull_cfg.get("systems") or []
    if systems:
        entry = next((s for s in systems if s.get("system_id") == sid), None) if sid else None
        if entry is None:
            entry = systems[0]
        bridge_amail = entry.get("amail_url", "")
        bridge_sid = entry.get("system_id", "")
    else:
        bridge_amail = pull_cfg.get("amail_url", "")
        bridge_sid = pull_cfg.get("system_id", "")
    gw_url = gw.get("gateway_url", "").rstrip("/")
    if bridge_amail and gw_url:
        b_host = bridge_amail.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
        g_host = gw_url.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
        if b_host != g_host:
            mismatches.append(f"bridge pulls from '{b_host}' but gateway is '{g_host}'")

    # (B) pull.system_id vs gateway system_id
    gw_sid = gw.get("system_id", "")
    if bridge_sid and gw_sid and bridge_sid != gw_sid:
        mismatches.append(f"bridge system_id differs: '{bridge_sid[:16]}...' vs '{gw_sid[:16]}...'")

    # (C) bridge addr vs gateway webhook_host
    # NAT 部署: bridge 绑定本地地址(127.0.0.1/0.0.0.0/::)而 webhook_host 是
    # 公网映射地址,两者必然不同——此时只比较端口;两边都是具体公网地址才
    # 要求完全一致。
    bridge_addr = _td.get("__top__", {}).get("addr", "") or _td.get("bridge", {}).get("addr", "")
    gw_wh = gw.get("webhook_host", "")
    if bridge_addr and gw_wh and bridge_addr != gw_wh:
        _bhost, _bport = _split_host_port(bridge_addr)
        _ghost, _gport = _split_host_port(gw_wh)
        _local_hosts = ("0.0.0.0", "::", "", "127.0.0.1", "localhost", "[::1]")
        if _bhost in _local_hosts and _bport and _bport == _gport:
            pass  # 本地绑定 + 端口一致 = 配置一致(NAT 映射到公网同端口)
        else:
            mismatches.append(f"bridge addr '{bridge_addr}' ≠ gateway webhook_host '{gw_wh}'")

    if mismatches:
        detail = "; ".join(mismatches)
        c.add("bridge", "config_consistency", False, detail,
              "Re-run: aimail install (同步配置)")
    else:
        c.add("bridge", "config_consistency", True, "bridge ↔ gateway configs match")


def _detect_local_bridge_pid() -> str:
    """Check if a bridge process is running on this machine. Returns PID string or ''."""
    if BRIDGE_PID.exists():
        try:
            pid = int(BRIDGE_PID.read_text().strip())
            os.kill(pid, 0)
            return str(pid)
        except Exception:
            pass
    try:
        out = subprocess.run(["pgrep", "-f", "aimail-bridge"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().replace("\n", ", ")
    except Exception:
        pass
    return ""


def _check_bridge_activity(c: Check):
    """Check log freshness. Bridge may be idle with no emails to process."""
    try:
        age = time.time() - BRIDGE_LOG.stat().st_mtime
        if age < 30:
            c.add("bridge", "activity", True, f"log {int(age)}s ago — actively running")
        elif age < 120:
            c.add("bridge", "activity", True, f"log {int(age)}s ago — may be idle")
        else:
            hrs = int(age / 3600)
            c.add("bridge", "activity", True,
                  f"log {int(age)}s ago — idle ({hrs}h)")
    except Exception as e:
        c.add("bridge", "activity", True, f"Cannot read: {e}")


def _check_bridge_pull_path(c: Check, td: dict, sid: str = "") -> bool:
    """P0: Verify bridge credentials can reach aimail-gateway pull API. Returns True if pass."""
    # 多系统支持:pull.systems 数组按 sid 匹配,空则回退单系统扁平字段。
    # 用标准 tomllib 重读(自定义 _parse_toml 不支持数组)。
    try:
        import tomllib
        with open(BRIDGE_CFG, "rb") as _f:
            _td = tomllib.load(_f)
        pull_cfg = _td.get("pull", {})
    except Exception:
        pull_cfg = td.get("pull", {})
    systems = pull_cfg.get("systems") or []
    if systems:
        # 按 sid 匹配该系统的条目(单 bridge 多系统);无匹配回退 systems[0]
        entry = next((s for s in systems if s.get("system_id") == sid), None) if sid else None
        if entry is None:
            entry = systems[0]
        amail_url = entry.get("amail_url", "")
        pull_key = entry.get("api_key", "") or entry.get("admin_key", "")
    else:
        amail_url = pull_cfg.get("amail_url", "")
        pull_key = pull_cfg.get("admin_key", "")
        pull_key = pull_key or pull_cfg.get("api_key", "")
    if not amail_url or not pull_key:
        c.add("bridge", "pull_path", False,
              "amail_url or admin_key missing in bridge config",
              "Check [pull] section in aimail_bridge.toml")
        return False

    body = json.dumps({"limit": 1}).encode()
    code, resp = _json_req(
        f"{amail_url.rstrip('/')}/api/v1/admin/pending",
        headers=_signed_post_headers(pull_key, "/api/v1/admin/pending", body,
                                     identity=pull_cfg.get("system_id", "")),
        data=body, method="POST")

    if code == 200:
        batches = resp.get("batches", []) if isinstance(resp, dict) else []
        detail = f"API 200, {len(batches)} pending batch(es)"
        c.add("bridge", "pull_path", True, detail)
    elif code == 400:
        # 400 can mean no routes configured on bridge side — the server
        # is reachable and auth works, just no emails to pull
        c.add("bridge", "pull_path", False,
              "HTTP 400 — route table may be empty on bridge",
              "Ensure bridge has registered routes via admin API")
    else:
        c.add("bridge", "pull_path", False,
              f"HTTP {code} — bridge cannot reach gateway's pending API",
              "Check amail_url and admin_key in aimail_bridge.toml")
    return code == 200


def _check_bridge_health(c: Check, addr: str):
    """P1: Bridge self health endpoint."""
    url = f"http://{addr}/health" if "://" not in addr else f"{addr}/health"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read()) if r.status == 200 else {}
            status = body.get("status", "ok") if isinstance(body, dict) else "ok"
            c.add("bridge", "self_health", True,
                  f"HTTP {r.status}, status={status}")
    except Exception as e:
        c.add("bridge", "self_health", False,
              f"Unreachable at {url}: {e}",
              "Bridge binary may not be running or addr is wrong")


# ═══════════════════════════════════════════════════════════════
#  Ping-Pong End-to-End Test (delegated to shared scripts/ping_test.py)
# ═══════════════════════════════════════════════════════════════
def _run_ping_test() -> int:
    """Delegate to the shared, agent-agnostic ping test script.

    The implementation moved to scripts/ping_test.py (SMTP auth inbound +
    aimail.log three-stage assertion) so every agent system
    (Hermes/OpenClaw/DeerFlow/dsh) uses the SAME ping/pong verification.
    """
    import subprocess
    script = Path(__file__).resolve().parent / "ping_test.py"
    cmd = [sys.executable, str(script)]
    if "--system-id" in sys.argv:
        try:
            i = sys.argv.index("--system-id")
            cmd += ["--system-id", sys.argv[i + 1]]
        except (ValueError, IndexError):
            pass
    if "--agent" in sys.argv:
        try:
            i = sys.argv.index("--agent")
            cmd += ["--agent", sys.argv[i + 1]]
        except (ValueError, IndexError):
            pass
    cmd += ["--agent-home", str(AGENT_HOME)]
    return subprocess.call(cmd)


# ═══════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════
def _detect_default_sid() -> str:
    """默认检测范围:本机实际安装且已集成 aimail 的第一个平台。

    用户定调 2026-08-16:默认值必须以实际为准——本机装了哪些
    agent 系统且有 aimail 指针的第一个,不能扫 ~/.aimail/systems/
    目录(里面有历史遗留的过期旧系统,歧义混淆)。
    按平台注册表顺序探测:平台存在 + 该平台指针(.agentmail)存在且
    有 system_id → 返回该 sid。全部无 → 空。
    """
    for pid, adapter in PLATFORMS.items():
        try:
            if not adapter["detect"]():
                continue
            sid = _resolve_platform_sid(pid)
            if sid:
                return sid
        except Exception:
            continue
    return ""


def main():
    if "--ping" in sys.argv:
        return _run_ping_test()

    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    json_out = "--json" in sys.argv

    # 平台识别:--agent-type 显式指定,否则自动探测
    agent_type = "auto"
    if "--agent-type" in sys.argv:
        try:
            ai = sys.argv.index("--agent-type")
            agent_type = sys.argv[ai + 1]
        except (ValueError, IndexError):
            pass
    if agent_type == "auto":
        agent_type = _detect_agent_type()
    adapter = PLATFORMS.get(agent_type)

    # ══ sid 归属平台反选(2026-08-16 双平台共存实测)══════════════
    # 探测顺序在双平台机器上可能选错平台(② ~/.openclaw 存在 → openclaw,
    # 优先于 ③ Hermes 特征)。显式 --system-id 时,按 sid 在哪个平台的
    # 指针下归属来覆盖探测结果——与 CLI 层"事实推断"定调一致
    # (不引入 --agent-type 参数)。
    if "--system-id" in sys.argv:
        _sid = _resolve_platform_sid(agent_type)
        if _sid:
            sid_platform = None
            # Hermes 指针:AGENT_HOME/.agentmail + profiles/*/.aimail
            ptr_cands = [AGENT_HOME / ".agentmail"]
            profiles_dir = AGENT_HOME / "profiles"
            if profiles_dir.is_dir():
                ptr_cands += [p / ".agentmail" for p in sorted(profiles_dir.iterdir()) if p.is_dir()]
            for ptr in ptr_cands:
                if ptr.is_file():
                    try:
                        if json.loads(ptr.read_text()).get("system_id") == _sid:
                            sid_platform = "hermes"
                            break
                    except Exception:
                        pass
            if not sid_platform:
                home = Path.home()
                for plat, cand in (("openclaw", home / ".openclaw" / ".agentmail"),
                                   ("pi",       home / ".pi" / ".agentmail"),
                                   ("dsh",      home / ".dsh" / ".agentmail"),
                                   ("deerflow", home / ".deer-flow" / ".agentmail")):
                    if cand.is_file():
                        try:
                            if json.loads(cand.read_text()).get("system_id") == _sid:
                                sid_platform = plat
                                break
                        except Exception:
                            pass
            if sid_platform and sid_platform != agent_type:
                _msg = f"  platform: {agent_type} → {sid_platform} (by system_id {_sid[:8]}…)"
                if "--json" in sys.argv:
                    print(_msg, file=sys.stderr)
                else:
                    print(_msg)
                agent_type = sid_platform
                adapter = PLATFORMS.get(agent_type)

    # ══ system_id 锚点(用户定调 2026-08-16)══════════════════════
    # 入参优先(--system-id / 上位传递);无则默认 = 本机实际安装且
    # 已集成 aimail 的第一平台指针(绝不扫 systems/ 目录——历史
    # 遗留旧系统会造成歧义)。
    platform_sid = _resolve_platform_sid(agent_type)
    if not platform_sid:
        platform_sid = _detect_default_sid()
        if platform_sid:
            print(f"  default system_id: {platform_sid} (from {_detect_agent_type()} pointer)")
    if not platform_sid:
        print(f"{YELLOW}⚠ No system_id resolved — 请用 --system-id 指定"
              f"(或确认本机已安装 agent 平台且有 aimail 指针){NC}")

    c = Check()
    c.verbose = verbose
    c.add("system", "id", bool(platform_sid),
          platform_sid or "none found", "Run: aimail init + aimail install (激活系统)")

    # ══ L0 配置文件完备性与正确性(2026-09-04 全面体检)════════
    # 顺序定调:L0/L1 配置 → L2 平台运行时资源 → L3 agent 配置 → L4 链路。
    _check_l0_configs(c, platform_sid)

    # L1 gateway(通用,按 sid)
    check_gateway(c, platform_sid)
    # L2 bridge(通用,按 sid 匹配 pull.systems 条目)
    check_bridge(c, platform_sid)
    # L0 扩展:bridge 配置完备性 + agentmail.json 完备性/一致性 + routes 覆盖
    _check_bridge_completeness(c, platform_sid)
    _check_agentmail_json(c, platform_sid)
    # L2 平台运行时资源就绪(补丁标记/skills/board 资源/插件)
    _check_l2_runtime(c, platform_sid)

    # L3/L4 agent 配置完整性 + hook 接口(平台专属适配器)
    if adapter:
        agents = adapter["list_agents"]()
        if not agents:
            c.add("agent", "discovery", False,
                  f"no agents found for platform '{agent_type}' (system {platform_sid})",
                  "Check the platform home dir / agents registry")
        for a in agents:
            try:
                adapter["check_config"](c, a)
            except Exception as e:
                c.add("agent", "config", False, f"{a.get('name')}: {e}")
            try:
                adapter["check_hook"](c, a)
            except Exception as e:
                c.add("agent", "hook", False, f"{a.get('name')}: {e}")
    elif agent_type == "deerflow":
        # deerflow 宿主通常远端;L3/L4 由 deer-flow SDK reconcile 自证,
        # 本机只报 config/runtime 层结果。
        print(f"{YELLOW}⚠ deerflow platform: L3/L4 agent checks run via deer-flow SDK reconcile on its host{NC}")
    else:
        # 未知平台:跳过 agent 检查(L0-L2 已跑),不回退旧检查
        print(f"{YELLOW}⚠ Unknown agent platform: {agent_type} — skipping agent checks{NC}")

    if json_out:
        c.print_json()
    else:
        c.print_table()
        print()
        if c.all_pass():
            print(f"  {GREEN}{BOLD}✓ All clear — aimail-gateway → agent-platform ready{NC}")
        else:
            fail = sum(1 for ch in c.checks if not ch["pass"])
            print(f"  {YELLOW}{BOLD}⚠ {fail}  issue(s) — check items marked  {CROSS} {NC}")
            if not verbose:
                print("    Use --verbose for fix suggestions")

    return 0 if c.all_pass() else 1




# ═══════════════════════════════════════════════════════════════
# L2: 平台运行时资源就绪(2026-09-04 全面体检第二维度)
# 顺序:L0/L1 配置 → L2 运行时资源 → L3 agent 配置 → L4 链路
# ═══════════════════════════════════════════════════════════════

def _check_l2_runtime(c: Check, sid: str):
    """检查安装时部署的运行时资源是否就绪(幂等重装可修复)。"""
    gw = _read_gw_cfg(sid) or {}
    sh = gw.get("system_home", "")
    platform = ""
    if sh and Path(sh).is_dir():
        # 复用 CLI 的特征判定(不经 import;特征判定与 aimail.detect 一致)
        p = Path(sh)
        if p.name == ".pi" and (p / "agent").is_dir():
            platform = "pi"
        elif p.name == ".dsh" and (p / "profiles").is_dir():
            platform = "dsh"
        elif (p / "hermes-agent").exists() or (p / "profiles").is_dir():
            platform = "hermes"
        elif (p / "openclaw.json").is_file():
            platform = "openclaw"
        elif (p / "backend" / "app" / "gateway").is_dir():
            platform = "deerflow"

    if not platform:
        # system_home 缺失/无效已在 L0 报;此处仅提示平台不可定位
        c.add("runtime", "platform-locatable", False,
              "platform root not locatable (system_home missing/invalid)",
              "Run: aimail install --home <platform-root> --system-id " + (sid or "<sid>"))
        return
    c.add("runtime", "platform-locatable", True, f"{platform} @ {sh}")

    home = Path.home()
    fix_install = f"python -m aimail.install --type {platform} --home {sh}"

    if platform == "hermes":
        ha = Path(sh) / "hermes-agent"
        wh = ha / "gateway" / "platforms" / "webhook.py"
        ok = wh.is_file() and "PREPROCESS_REGISTRY" in (wh.read_text(errors="replace") if wh.is_file() else "")
        c.add("runtime", "patch-webhook", ok,
              f"webhook.py {'patched (PREPROCESS_REGISTRY present)' if ok else 'NOT patched or missing: ' + str(wh)}",
              fix_install)
        prof = ha / "hermes_cli" / "profiles.py"
        if not prof.is_file():
            prof = ha / "cli" / "profiles.py"
        ok2 = prof.is_file() and "AmailGateway" in (prof.read_text(errors="replace") if prof.is_file() else "")
        c.add("runtime", "patch-profiles", ok2,
              f"profiles.py {'patched (AmailGateway hook present)' if ok2 else 'NOT patched or missing: ' + str(prof)}",
              fix_install)
        # skills: profiles/*/skills/agentmail/SKILL.md(至少一个)
        skills_hits = 0
        for cand in [Path(sh)] + sorted((Path(sh) / "profiles").glob("*")) if (Path(sh) / "profiles").is_dir() else [Path(sh)]:
            if (cand / "skills" / "agentmail" / "SKILL.md").is_file():
                skills_hits += 1
        c.add("runtime", "skills", skills_hits > 0,
              f"{skills_hits} profile(s) have skills/agentmail/SKILL.md" if skills_hits
              else "no profile has skills/agentmail/SKILL.md", fix_install)
        # toolsets: hermes config platform_toolsets 含 agentmail
        ts_ok = False
        for cfgp in [Path(sh) / "config.yaml"] + sorted((Path(sh) / "profiles").glob("*/config.yaml")):
            if cfgp.is_file():
                try:
                    import yaml as _y
                    pt = (_y.safe_load(cfgp.read_text()) or {}).get("platform_toolsets", {})
                    if any("agentmail" in (v or []) for v in pt.values() if isinstance(v, dict) or isinstance(v, list)):
                        ts_ok = True
                        break
                except Exception:
                    pass
        c.add("runtime", "toolsets", ts_ok,
              "platform_toolsets 含 agentmail" if ts_ok else "platform_toolsets lacks agentmail",
              fix_install)

    elif platform == "openclaw":
        installed = any((home / ".openclaw" / "npm" / "projects").glob("openclaw-aimail*")) \
            if (home / ".openclaw" / "npm" / "projects").is_dir() else False
        c.add("runtime", "plugin-installed", installed,
              "openclaw-aimail npm package present" if installed else "openclaw-aimail not installed",
              "openclaw plugins install npm-pack:<openclaw-aimail.tgz>; restart gateway")
        sk = (home / ".openclaw" / "skills" / "agentmail" / "SKILL.md").is_file()
        c.add("runtime", "skills", sk,
              "skills/agentmail/SKILL.md present" if sk else "skills/agentmail/SKILL.md missing",
              "Run: bash <core>/cli/scripts/openclaw/install-skill.sh")

    elif platform == "deerflow":
        app_py = Path(sh) / "backend" / "app" / "gateway" / "app.py"
        if not app_py.is_file():
            app_py = Path(sh) / "app" / "gateway" / "app.py"
        ok = app_py.is_file() and "aimail_inbound" in (app_py.read_text(errors="replace") if app_py.is_file() else "")
        c.add("runtime", "patch-app", ok,
              f"app.py {'patched (aimail_inbound router present)' if ok else 'NOT patched or missing: ' + str(app_py)}",
              fix_install)

    elif platform == "pi":
        ptr = home / ".pi" / ".agentmail"
        try:
            match = ptr.is_file() and json.loads(ptr.read_text()).get("system_id") == sid
        except Exception:
            match = False
        c.add("runtime", "pointer", match,
              ".pi pointer matches system" if match else ".pi pointer missing/mismatch",
              "Run: aimail install --home ~/.pi --system-id " + sid)

    # 通用:board 资源 + role_prompt 兜底(a2a_board 角色查找链)
    board_dir = SYSTEMS_DIR / sid / "board"
    rp = board_dir / "role_prompt" / "common.md"
    c.add("runtime", "board-resources", rp.is_file(),
          "board/role_prompt/common.md present" if rp.is_file()
          else "board/role_prompt/common.md missing (a2a role rendering falls back empty)",
          fix_install)


if __name__ == "__main__":
    sys.exit(main())

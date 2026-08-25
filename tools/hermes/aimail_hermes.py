"""aimail_hermes.py — Hermes 适配层（公共核心的平台接线）

公共核心（tools/aimail_base.py / aimail_tools.py / aimail_board.py）
保持平台无关，通过注入点提供业务逻辑；本模块：
  1. 注入 Hermes 平台实现（config 加载 / personas / profile 目录 / SOUL / skills /
     board 登记 / persona 上下文）
  2. 提供 Hermes 专属功能（profile 生命周期钩子 / webhook 路由 / 端口管理）
  3. 在 Hermes 运行时注册（webhook preprocessor / tools registry / 生命周期钩子）

OpenClaw 对应适配层：tools/openclaw/amail_base.py
"""

import json
import logging
import os
import secrets
import socket
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

# ── Hermes 运行时定位:本模块随捆绑落到 {install-root}/tools/hermes/,
 #    共享核心(aimail_base/tools/board/gateway_api)在父目录。Hermes 以包形式
 #    加载工具(tools.*),顶层 import 需要 tools/ 目录在 sys.path 上——父目录
 #    探测位置无关(捆绑 / pip site-packages / 仓库 dev 三态通用)。直接 import
 #    本模块(独立脚本 / register_profiles.py)同样依赖此引导。
_HERMES_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERMES_TOOLS_DIR not in sys.path:
    sys.path.insert(0, _HERMES_TOOLS_DIR)

import aimail_base as core
import aimail_tools as tools
import aimail_board as board

# API 客户端（公共 aimail_tools._GatewayClient 全方法）
_GatewayClient = tools._GatewayClient

# ── 公共函数转发（搬移函数/_handle_* 内部裸调用——同源公共核心）──
_agentmail_system_dir = core._agentmail_system_dir
_load_gateway_config = core._load_gateway_config
list_personas = core.list_personas
# 6 工具函数体（注册表 handler 包装器裸调用）
send_mail = tools.send_mail
manage_contacts = tools.manage_contacts
contact_profile = tools.contact_profile
set_contact_profile = tools.set_contact_profile
email_summary = tools.email_summary
set_email_summary = tools.set_email_summary
# A2A Board 工具函数体（registry 注册 handler 裸调用）
board_task_show = board.board_task_show
board_task_list = board.board_task_list
board_members = board.board_members
board_roles = board.board_roles
board_status = board.board_status
board_heartbeat = board.board_heartbeat
_TOOLSET = "agentmail"

logger = logging.getLogger(__name__)

# profile 生命周期钩子注册表（Hermes 平台专属）
_profile_hooks: Dict[str, List[Callable]] = {
    "profile_created": [],
    "profile_deleted": [],
}
# ═══════════════════════════════════════════════════════════════
# 1. Hermes 平台实现（自公共核心移出的 Hermes 专属函数）
# ═══════════════════════════════════════════════════════════════
def _read_soul_md() -> str:
    """Read SOUL.md from current Hermes profile."""
    profile_dir = os.environ.get("HERMES_PROFILE_DIR", "")
    if not profile_dir:
        profile_dir = str(Path.home() / ".hermes")
    soul = Path(profile_dir) / "SOUL.md"
    if soul.exists():
        return soul.read_text(encoding="utf-8")
    return ""


def _read_skills() -> list[str]:
    """Read loaded skills list from profile config."""
    profile_dir = os.environ.get("HERMES_PROFILE_DIR", "")
    if not profile_dir:
        profile_dir = str(Path.home() / ".hermes")
    cfg = Path(profile_dir) / "config.yaml"
    if cfg.exists():
        import yaml
        try:
            data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
            return data.get("skills", []) or []
        except Exception:
            pass
    return []


def _resolve_agent_email() -> str:
    """Resolve current agent's email from profile config."""
    from tools.aimail_tools import _load_profile_config as _lpc
    cfg = _lpc()
    if cfg:
        return cfg.get("email", "") or cfg.get("domain", "")
    return ""


def _resolve_profile_dir() -> Optional[str]:
    """Resolve Hermes profile directory via fallback chain.

    1. HERMES_PROFILE_DIR env var (explicit override, highest priority)
    2. Hermes runtime get_hermes_home() (contextvar-aware, multi-profile)
    3. ~/.hermes/ (default profile, ultimate fallback)
    """
    pdir = os.environ.get("HERMES_PROFILE_DIR", "")
    if pdir:
        return pdir
    try:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
        if home:
            return str(home)
    except Exception:
        pass
    default = Path.home() / ".hermes"
    if default.is_dir():
        return str(default)
    return None


_profile_config_path = core._agent_config_path  # 地址键 per-agent 配置路径（共享）


def _load_profile_config() -> Optional[dict]:
    """Load per-agent config from the address-keyed directory.

    Uses {profile_dir}/.agentmail pointer → {system_id} + {email} → path:
      ~/.agentmail/systems/{system_id}/{cleaned_addr}/agentmail.json
    """
    profile_dir = _resolve_profile_dir() or ""
    
    search_paths = []

    if profile_dir:
        # .agentmail pointer → address-keyed path
        pointer_data = core._read_pointer(Path(profile_dir) / ".agentmail")
        sid = pointer_data.get("system_id", "")
        email = pointer_data.get("email", "")
        if sid and email:
            search_paths.append(_profile_config_path(sid, email))

    for config_path in search_paths:
        if config_path.is_file():
            try:
                cfg = json.loads(config_path.read_text())
                # Internal key: location of the agent config file — board
                # credentials live next to it (per-agent layout).
                cfg["_config_path"] = str(config_path)
                return cfg
            except Exception:
                pass
    
    return None


def _inject_profile_config(profile_dir: str, config: dict) -> None:
    """Write per-agent agentmail config.

    Address-keyed: ~/.agentmail/systems/{system_id}/{cleaned_addr}/agentmail.json
    Pointer file:  {profile_dir}/.agentmail  (contains system_id + email for discovery)

    Merges with existing config — preserves fields not in the new config
    (e.g. api_key from previous activation).
    """
    system_id = config.get("system_id", "")
    email = config.get("email", "")

    # Write primary config to the address-keyed directory
    if system_id and email:
        primary = _profile_config_path(system_id, email)
        primary.parent.mkdir(parents=True, exist_ok=True)
        # Merge with existing — preserve fields like api_key
        existing = {}
        if primary.exists():
            try:
                existing = json.loads(primary.read_text())
            except Exception:
                pass
        merged = {**existing, **config}
        # Prevent activation_code + api_key coexistence
        if merged.get("api_key") and merged.get("activation_code"):
            merged.pop("activation_code", None)
        primary.write_text(json.dumps(merged, indent=2))

    # Write .agentmail pointer for discovery
    core._write_pointer(Path(profile_dir) / ".agentmail", system_id, email)


def _port_is_available(port: int, host: str = "0.0.0.0") -> bool:
    """Check if a TCP port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.bind((host, port))
            return True
    except OSError:
        return False


def _read_webhook_port(cfg_path: Path) -> int:
    """Read webhook port from a profile config file. Returns 0 if not found."""
    if not cfg_path.exists():
        return 0
    try:
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        wh = cfg.get("platforms", {}).get("webhook", {})
        if wh.get("enabled"):
            return int(wh.get("extra", {}).get("port", 0))
    except Exception:
        pass
    return 0


def _next_available_webhook_port(base_port: int = 8644) -> int:
    """Find the next available webhook port.
    
    Scans all existing profile configs for the max port, then probes
    actual port availability. Increments until an unused port is found.
    """
    # Scan existing profiles for max configured port
    max_port = base_port - 1
    default_cfg = Path.home() / ".hermes" / "config.yaml"
    max_port = max(max_port, _read_webhook_port(default_cfg))
    profiles_dir = Path.home() / ".hermes" / "profiles"
    if profiles_dir.is_dir():
        for d in profiles_dir.iterdir():
            if d.is_dir():
                max_port = max(max_port, _read_webhook_port(d / "config.yaml"))
    
    # Start after max configured port, probe actual availability
    candidate = max(max_port + 1, base_port)
    for _ in range(100):  # safety limit
        if _port_is_available(candidate):
            return candidate
        candidate += 1
    return candidate


def _ensure_profile_webhook(profile_dir: str) -> Optional[dict]:
    """Ensure the profile has webhook configured. Auto-generates if missing.
    
    Returns {enabled, host, port, secret} or None on fatal error.
    """
    cfg_path = Path(profile_dir) / "config.yaml"
    
    # Already configured? Return existing
    if cfg_path.exists():
        try:
            import yaml
            cfg = yaml.safe_load(cfg_path.read_text()) or {}
            wh = cfg.get("platforms", {}).get("webhook", {})
            if wh.get("enabled"):
                extra = wh.get("extra", {})
                return {
                    "enabled": True,
                    "host": extra.get("host", "0.0.0.0"),
                    "port": int(extra.get("port", 8644)),
                    "secret": extra.get("secret", ""),
                }
        except Exception as e:
            logger.warning("[agentmail_gateway] Failed to read webhook config: %s", e)
    
    # Auto-generate: persist the webhook config, then return it
    port = _next_available_webhook_port()
    secret = secrets.token_hex(32)

    try:
        import yaml
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if cfg_path.exists():
            existing = yaml.safe_load(cfg_path.read_text()) or {}

        # Deep-merge with existing config
        platforms = dict(existing.get("platforms", {}))
        platforms["webhook"] = {
            "enabled": True,
            "host": "0.0.0.0",
            "port": port,
            "extra": {"port": port, "secret": secret},
        }
        existing["platforms"] = platforms
        tmp = cfg_path.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(existing, allow_unicode=True, sort_keys=False))
        tmp.replace(cfg_path)
        return {
            "enabled": True,
            "host": "0.0.0.0",
            "port": port,
            "secret": secret,
        }
    except Exception as e:
        logger.warning("[agentmail_gateway] Failed to write webhook config: %s", e)
        return None


def _list_personas() -> dict:
    """List configured personas from profile config."""
    profile_dir = _resolve_profile_dir()
    if profile_dir:
        try:
            import yaml
            profile_cfg_path = Path(profile_dir) / "config.yaml"
            if profile_cfg_path.exists():
                with open(profile_cfg_path) as f:
                    cfg = yaml.safe_load(f) or {}
                return cfg.get("agent", {}).get("personalities", {}) or {}
        except Exception:
            pass
    return {}


def _ensure_webhook_route(
    route_name: str,
    secret: str,
    profile_dir: str = "",
    skills: Optional[List[str]] = None,
    deliver: str = "log",
    persona: str = "",
) -> bool:
    """Idempotently create/update a webhook route in webhook_subscriptions.json.
    
    Writes to ``{profile_dir}/webhook_subscriptions.json`` so each profile's
    gateway sees its own routes. Falls back to ``HERMES_HOME`` (or ~/.hermes)
    when ``profile_dir`` is empty.

    Returns True if the route was newly created, False if it already existed.
    """
    # Validate persona if specified
    if persona:
        personas = list_personas()
        if persona not in personas:
            logger.warning(
                "[agentmail_gateway] Persona '%s' not found in agent.personalities. "
                "Available: %s. Route will be created without persona.",
                persona, ", ".join(personas.keys()) or "(none)"
            )
            persona = ""  # clear invalid persona

    if profile_dir:
        hermhome = profile_dir
    else:
        hermhome = os.environ.get("HERMES_HOME",
            str(Path.home() / ".hermes"))
    subs_path = Path(hermhome) / "webhook_subscriptions.json"
    subs = {}
    if subs_path.exists():
        try:
            subs = json.loads(subs_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass

    route_entry = {
        "description": f"agentmail inbound email route ({route_name})",
        "events": [],
        "secret": secret,
        "preprocess": "agentmail_gateway",    # triggers preprocess_mail_payload
        "prompt": "",
        "skills": skills or [],
        "deliver": deliver,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if persona:
        route_entry["persona"] = persona
    subs[route_name] = route_entry

    # Persist the route (atomic replace). Previously missing — the route
    # was never written, so inbound webhook delivery 404'd.
    subs_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = subs_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(subs, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(subs_path)
    return True


def register_profile_hook(event: str, callback: Callable) -> None:
    """Register a callback for profile lifecycle events."""
    if event not in _profile_hooks:
        _profile_hooks[event] = []
    _profile_hooks[event].append(callback)


def trigger_profile_hooks(event: str, profile_name: str, profile_dir: str) -> None:
    """Called by profiles.py to fire all registered hooks for an event.

    Gracefully handles missing config -- if no gateway is configured, hooks are
    simply skipped.
    """
    try:
        config = _load_gateway_config()
    except RuntimeError:
        logger.debug("[agentmail_gateway] No gateway config -- skipping hooks for %s", event)
        return


    if not config:
        logger.debug("[agentmail_gateway] No gateway config -- skipping hooks for %s", event)
        return

    for cb in _profile_hooks.get(event, []):
        try:
            cb(profile_name, profile_dir, config)
        except Exception as e:
            logger.warning("[agentmail_gateway] hook %s for '%s' failed: %s", event, profile_name, e)


def _auto_register_email(name: str, profile_dir: str, config: dict) -> None:
    """When a new Profile is created, register its email with agentmail:
    1. Create domain entry for {name}@{domain}
    2. Create activation code for the agent
    3. Ensure agentmail-inbound webhook route on the gateway
    4. Inject config into profile directory
    
    The registered address is the agent's identity. Persona switching is
    handled at inbound time by parse_amail_persona() — the agentmail skill
    extracts persona from the 'to' address (persona.profile@domain format).
    """
    gateway_url = config.get("gateway_url", "")
    admin_key = config.get("admin_key", "")
    domain = config.get("domain", "")
    system_id = config.get("system_id", "")

    if not gateway_url or not admin_key:
        logger.warning("[agentmail_gateway] Cannot auto-register: gateway_url or admin_key not configured")
        return
    if not domain:
        logger.warning("[agentmail_gateway] Cannot auto-register: domain not configured")
        return
    if not system_id:
        logger.warning("[agentmail_gateway] Cannot auto-register: system_id not configured")
        return

    client = _GatewayClient(gateway_url, admin_key)
    system_name = config.get("system_name", "") or ""
    # 地址派生统一走公共 email_for_agent（Hermes 默认名 default → agent；其余保持原名；
    # 非法字符清洗 '.' → '_'；合规标准由 gateway register_address 校验——http.rs）
    email = core.email_for_agent(name, domain, system_name)
    manager_address = config.get("manager_address", "")

    # 事实源优先:agentmail.json 已有 webhook_url/secret 则复用(agentmail 唯一
    # 信任源;profile config 是 Hermes 运行时副本,值同源——重注册不漂移)
    existing_cfg = _load_profile_config()
    if (existing_cfg and existing_cfg.get("webhook_url")
            and existing_cfg.get("webhook_secret")):
        local_webhook_url = existing_cfg["webhook_url"]
        webhook_secret = existing_cfg["webhook_secret"]
        try:
            wh_port = int(local_webhook_url.rsplit(":", 1)[1].split("/", 1)[0])
        except Exception:
            wh_port = 8644
        wh_config = {"secret": webhook_secret, "port": wh_port}
    else:
        # Auto-configure or read profile webhook config
        wh_config = _ensure_profile_webhook(profile_dir)
        if not wh_config:
            logger.warning("[agentmail_gateway] Failed to configure webhook for %s — inbound mail disabled", profile_dir)
            local_webhook_url = ""
            webhook_secret = ""
        else:
            webhook_secret = wh_config["secret"]
            wh_port = wh_config["port"]
            # 本地接收端点(进程内 preprocess 的 webhook 路由)
            local_webhook_url = f"http://127.0.0.1:{wh_port}/webhooks/agentmail-inbound"
            # Ensure agentmail-inbound route exists (idempotent)
            # skills=["agentmail"] so webhook sessions get the agentmail skill
            # (send_mail protocol); without it the agent cannot reply by email.
            _ensure_webhook_route(
                "agentmail-inbound", webhook_secret, profile_dir=profile_dir,
                skills=["agentmail"],
            )

    # 注册参数三态(webhook_host):push=bridge 公网入口 / pull=空 / 无 bridge=本地端点
    reg_url = core.resolve_register_webhook_url(config, local_webhook_url)

    # Register the email + generate activation code（公共注册链，幂等）
    reg = core.register_agent_email(
        client, system_id, email, reg_url, webhook_secret,
        manager_address,
    )
    logger.info("[agentmail_gateway] Registered email %s (api_key=%s)",
                email, "ok" if reg.get("api_key") else "pending")
    activation_code = reg.get("activation_code", "")
    api_key = reg.get("api_key", "")

    if not activation_code:
        # Already registered: profile should have existing api_key or activation_code
        logger.info("[agentmail_gateway] Email %s already registered — using existing credentials", email)
        # Don't pass activation_code to inject; merge preserves existing value
        # Check if existing config has a pending activation_code to activate
        try:
            if system_id and email:
                existing_cfg_path = _profile_config_path(system_id, email)
                if existing_cfg_path.is_file():
                    existing_cfg = json.loads(existing_cfg_path.read_text())
                    if existing_cfg.get("activation_code") and not existing_cfg.get("api_key"):
                        activation_code = existing_cfg["activation_code"]
                        logger.info("[agentmail_gateway] Found pending activation_code for %s — will activate", email)
        except Exception:
            pass

    inject_cfg = {
        "email": email,
        "gateway_url": gateway_url,
        "domain": config["domain"],
        "system_id": system_id,
        "manager_address": manager_address,
        "save_raw_snapshots": config.get("save_raw_snapshots", False),
        "webhook_host": config.get("webhook_host", ""),
        # agentmail 唯一信任源:webhook_url + webhook_secret 成对落盘
        # (注册链/验签/路由统一从 agentmail.json 读;profile config 的
        # platforms.webhook 只是 Hermes 运行时副本,值同源)
        "webhook_url": local_webhook_url,
        "webhook_secret": webhook_secret,
    }
    if api_key:
        inject_cfg["api_key"] = api_key
    if activation_code:
        inject_cfg["activation_code"] = activation_code
    _inject_profile_config(profile_dir, inject_cfg)

    # 铁律(2026-08-18):有 bridge 时,agent 注册地址后必须向 bridge 注册
    # 入站 hook 路由(email → 本地接收端点);否则 bridge 拉取后不知转发到哪。
    if local_webhook_url:
        try:
            core.register_bridge_route(system_id, email, config, local_webhook_url)
        except Exception as e:
            logger.warning("[agentmail_gateway] bridge route registration failed for %s: %s", email, e)

    # Activate the profile immediately after registration.
    # register_agent_email already activated when it returned an api_key —
    # only re-activate (via activation_code) when no api_key came back.
    if activation_code and not api_key:
        try:
            _auto_activate_profile(profile_dir, config)
        except Exception as e:
            logger.error("[agentmail_gateway] Activation failed for %s: %s — activation_code retained", email, e)


def _auto_activate_profile(profile_dir: str, config: dict) -> None:
    """Activate a pending profile (has activation_code, no api_key yet).

    Called by the agent startup process when it detects an activation code
    in the profile config. This ensures the raw_key is only visible to
    the agent process itself.

    Reads from the address-keyed ~/.agentmail/systems/{system_id}/{addr}/ path only.
    """
    # Read system_id + email from pointer file to find address-keyed config
    sid = ""
    email = ""
    pointer_path = Path(profile_dir) / ".agentmail"
    if pointer_path.is_file():
        try:
            pd_data = json.loads(pointer_path.read_text())
            sid = pd_data.get("system_id", "")
            email = pd_data.get("email", "")
        except Exception:
            pass

    if not sid:
        logger.warning(
            "[agentmail_gateway] No system_id in .agentmail pointer for %s — cannot activate",
            profile_dir,
        )
        return

    config_path = _profile_config_path(sid, email) if email else None

    if not config_path or not config_path.is_file():
        return

    try:
        with open(config_path) as f:
            prof = json.load(f)
    except (json.JSONDecodeError, IOError):
        return

    activation_code = prof.get("activation_code", "")
    if not activation_code:
        return  # Already activated or no code

    if prof.get("api_key"):
        # Already has a key -- clean up stale activation_code
        prof.pop("activation_code", None)
        with open(config_path, "w") as f:
            json.dump(prof, f, indent=2)
        return

    client = _GatewayClient(config.get("gateway_url", prof.get("gateway_url", "")),
                          "", timeout=5)
    result = client.activate_address(activation_code, email_address=prof.get("email", ""))
    if result.get("success") and result.get("raw_key"):
        prof["api_key"] = result["raw_key"]
        prof.pop("activation_code", None)
        prof.pop("last_activation_attempt", None)
        with open(config_path, "w") as f:
            json.dump(prof, f, indent=2)
        logger.info("[agentmail_gateway] Activated profile, api_key saved to %s", config_path)

        # ── Sync api_key to address-keyed config ─────────────────────
        try:
            pointer_path = Path(profile_dir) / ".agentmail"
            if pointer_path.is_file():
                pd = json.loads(pointer_path.read_text())
                sid = pd.get("system_id", "")
                email = pd.get("email", "")
                if sid and email:
                    sync_path = _profile_config_path(sid, email)
                    if sync_path.is_file():
                        cfg = json.loads(sync_path.read_text())
                        cfg["api_key"] = result["raw_key"]
                        cfg.pop("activation_code", None)
                        sync_path.write_text(json.dumps(cfg, indent=2))
                        logger.info("[agentmail_gateway] api_key synced to %s", sync_path)
        except Exception as sync_err:
            logger.warning("[agentmail_gateway] Failed to sync api_key: %s", sync_err)

        # 注:旧 "Port refresh"(激活时刷 bridge 路由)已删除(2026-08-18)——
        # bridge 路由表目标 = agentmail.json 的 webhook_url(唯一信任源),
        # 由注册链/CLI bridge 维护,激活流程不再触碰。
    else:
        # Rate-limit retries: skip if recently attempted (avoids spamming gateway
        # with a permanently invalid activation code)
        import time as _time
        now_ts = _time.time()
        last = prof.get("last_activation_attempt", 0)
        if last and (now_ts - last) < 300:
            logger.debug("[agentmail_gateway] Skipping activation retry for %s (last attempt %ds ago)",
                         config_path, int(now_ts - last))
        else:
            prof["last_activation_attempt"] = now_ts
            with open(config_path, "w") as f:
                json.dump(prof, f, indent=2)
            logger.warning("[agentmail_gateway] Failed to activate profile %s: %s",
                           config_path, result.get("error", result))


def _auto_deregister_email(name: str, profile_dir: str, config: dict) -> None:
    """When a Profile is deleted, clean up its amail registration
    （API 注销链走公共 deregister_agent_email——幂等，补全原缺口）。"""
    gateway_url = config.get("gateway_url", "")
    admin_key = config.get("admin_key", "")
    domain = config.get("domain", "")
    if not gateway_url or not admin_key or not domain:
        return

    system_id = config.get("system_id", "")
    system_name = config.get("system_name", "")
    email = core.email_for_agent(name, domain, system_name)

    try:
        client = _GatewayClient(gateway_url, admin_key)
        status = core.deregister_agent_email(
            client, system_id, email,
            manager_address=config.get("manager_address", ""),
        )
        logger.info("[agentmail_gateway] Deregistered %s: %s", email, status)
    except Exception as e:
        logger.warning("[agentmail_gateway] Deregister failed for %s: %s", email, e)


def _get_board_gateway_url(board_id: str, profile_cfg: dict) -> str:
    with core._board_gateways_lock:
        return core._board_gateways.get(board_id, profile_cfg.get("gateway_url", ""))


def _register_board_gateway(board_id: str, gateway_url: str):
    with core._board_gateways_lock:
        core._board_gateways[board_id] = gateway_url


def _handle_send_mail(args, **_kw):
    return tool_result(send_mail(
        to=args.get("to", ""),
        subject=args.get("subject", ""),
        body=args.get("body", ""),
        cc=args.get("cc"),
        attachments=args.get("attachments"),
        message_id=args.get("message_id"),
    ))

def _handle_manage_contacts(args, **_kw):
    return tool_result(manage_contacts(
        action=args.get("action", "check"),
        address=args.get("address"),
        direction=args.get("direction", "all"),
    ))

def _handle_contact_profile(args, **_kw):
    return tool_result(contact_profile(
        address=args.get("address", ""),
        name=args.get("name", ""),
    ))

def _handle_set_contact_profile(args, **_kw):
    return tool_result(set_contact_profile(


        address=args.get("address", ""),
        profile=args.get("profile", ""),
    ))

def _current_persona_name() -> Optional[str]:
    """当前 persona 名（注入点）。Hermes 适配层注入（profile 目录派生）；
    OpenClaw 无 persona 概念 → None（发件用基础地址）。"""
    return _PERSONA_NAME_PROVIDER() if _PERSONA_NAME_PROVIDER is not None else None


def _hermes_persona_name() -> Optional[str]:
    """Hermes 版 persona 名：从 profile 目录派生（原 aimail_tools 实现）。
    default（~/.hermes）→ None；profiles/<name> → name。"""
    profile_dir = _resolve_profile_dir() or ""
    if not profile_dir:
        return None
    p = Path(profile_dir).resolve()
    home_hermes = (Path.home() / ".hermes").resolve()
    if p == home_hermes:
        return None
    profiles_dir = home_hermes / "profiles"
    try:
        p.relative_to(profiles_dir)
    except ValueError:
        return None
    name = p.name
    return name if name else None

def _handle_email_summary(args, **_kw):
    return tool_result(email_summary(
        message_id=args.get("message_id", ""),
    ))

def _handle_set_email_summary(args, **_kw):
    return tool_result(set_email_summary(
        message_id=args.get("message_id", ""),
        summary=args.get("summary", ""),
    ))

# ═══════════════════════════════════════════════════════════════
# 2. 注入公共核心的注入点
# ═══════════════════════════════════════════════════════════════
core._PROFILE_DIR_RESOLVER = _resolve_profile_dir
core._CONFIG_LOADER = _load_profile_config
core._PERSONAS_PROVIDER = _list_personas
core._SOUL_PROVIDER = _read_soul_md
core._SKILLS_PROVIDER = _read_skills
core._BOARD_GATEWAY_SINK = _register_board_gateway
# board 凭据存储（_store_board_credential）已提升到公共核心默认实现；
# 跨模块名（store_inbound_message/_log_amail/_GatewayClient）已由公共核心
# 函数级 import 自解析——均无需适配层注入。
tools._PERSONA_NAME_PROVIDER = _hermes_persona_name
_PERSONA_NAME_PROVIDER = _hermes_persona_name          # 适配层命名空间（_current_persona_name 注入点）
core.PERSONA_SUPPORTED = True  # Hermes 全能力（默认值，显式声明）

# ═══════════════════════════════════════════════════════════════
# 3. Hermes 平台注册（gateway 进程内执行）
# ═══════════════════════════════════════════════════════════════
# 3a. webhook preprocessor（入站富化）
try:
    from gateway.platforms.webhook import register_preprocessor

    register_preprocessor("agentmail_gateway", core.process_inbound_mail)
    logger.info("agentmail preprocessor registered with webhook gateway")
except ImportError:
    pass

# 3b. tools registry（6 邮件工具 + board 工具）
try:
    from tools.registry import registry, tool_result
except ImportError:
    registry = None
    tool_result = lambda x: x  # noqa: E731

if registry is not None:
    try:
        from tools.registry import registry, tool_result  # noqa: E402
        _HERMES_REGISTRY_AVAILABLE = True
    except ImportError:
        _HERMES_REGISTRY_AVAILABLE = False
        class _DummyRegistry:
            def register(self, **kw): pass
        registry = _DummyRegistry()  # type: ignore
        def tool_result(x): return x

    try:
        registry.register(
            name="board_task_show",
            toolset=_TOOLSET,
            schema={
                "name": "a2a_show",
                "description": "Show one task's full details, including parent-task context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task ID (t_<board>_<id>)"
                        }
                    },
                    "required": ["task_id"]
                }
            },
            handler=board_task_show,
            emoji="📋",
        )

        registry.register(
            name="board_task_list",
            toolset=_TOOLSET,
            schema={
                "name": "a2a_list",
                "description": "List a board's tasks, optionally filtered by status or assignee.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "board": {"type": "string", "description": "Board ID (b_ prefix)"},
                        "status": {"type": "string", "description": "Filter by task status"},
                        "assignee": {"type": "string", "description": "Filter by assignee email"}
                    },
                    "required": ["board"]
                }
            },
            handler=board_task_list,
            emoji="📋",
        )

        registry.register(
            name="board_members",
            toolset=_TOOLSET,
            schema={
                "name": "a2a_members",
                "description": "List a board's members and their roles.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "board": {"type": "string", "description": "Board ID (b_ prefix)"}
                    },
                    "required": ["board"]
                }
            },
            handler=board_members,
            emoji="👥",
        )

        registry.register(
            name="board_heartbeat",
            toolset=_TOOLSET,
            schema={
                "name": "a2a_heartbeat",
                "description": "Signal your task is still in progress (a ready task advances to running).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task ID (t_<board>_<id>)"},
                        "note": {"type": "string", "description": "Progress note (optional)"}
                    },
                    "required": ["task_id"]
                }
            },
            handler=board_heartbeat,
            emoji="💓",
        )
    except Exception as _e:
        logger.warning("[a2a_board] tool registration failed: %s", _e)

# 3b.2 邮件工具注册（6 个: send_mail / 联系人 / summary）
# 与 board 工具同机制: registry.register 在 import 时执行,
# 名字需与 toolsets.py _HERMES_CORE_TOOLS 中的条目一致。
try:
    registry.register(
        name="send_mail",
        toolset=_TOOLSET,
        schema={
            "name": "send_mail",
            "description": "Send an email via your agentmail address. For replies, pass the original email's message_id to thread automatically (In-Reply-To/References/persona).",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient address(es); comma-separated for multiple"},
                    "subject": {"type": "string", "description": "Email subject"},
                    "body": {"type": "string", "description": "Email body (plain text)"},
                    "cc": {"type": "string", "description": "CC address(es); comma-separated"},
                    "attachments": {"type": "array", "items": {"type": "string"}, "description": "Local file paths to attach"},
                    "message_id": {"type": "string", "description": "Original message_id when replying — threads the reply"},
                },
                "required": ["to", "subject", "body"],
            },
        },
        handler=_handle_send_mail,
        emoji="📧",
    )
except Exception as _e:
    logger.warning("[agentmail] send_mail registration failed: %s", _e)

try:
    registry.register(
        name="manage_contacts",
        toolset=_TOOLSET,
        schema={
            "name": "manage_contacts",
            "description": "Manage your address book (whitelist): check/add/remove contacts with direction control.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["check", "add", "remove"], "description": "check/add/remove"},
                    "address": {"type": "string", "description": "Email address (required for add/remove)"},
                    "direction": {"type": "string", "enum": ["all", "from", "to"], "description": "Direction filter (default all)"},
                },
                "required": ["action"],
            },
        },
        handler=_handle_manage_contacts,
        emoji="📇",
    )
except Exception as _e:
    logger.warning("[agentmail] manage_contacts registration failed: %s", _e)

try:
    registry.register(
        name="contact_profile",
        toolset=_TOOLSET,
        schema={
            "name": "contact_profile",
            "description": "Look up a contact profile by address or name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "Exact email address lookup"},
                    "name": {"type": "string", "description": "Server-side search by name"},
                },
                "required": [],
            },
        },
        handler=_handle_contact_profile,
        emoji="👤",
    )
except Exception as _e:
    logger.warning("[agentmail] contact_profile registration failed: %s", _e)

try:
    registry.register(
        name="set_contact_profile",
        toolset=_TOOLSET,
        schema={
            "name": "set_contact_profile",
            "description": "Store or update a contact profile (JSON string; gateway merges and maintains the name index).",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "Contact email address"},
                    "profile": {"type": "string", "description": "JSON-formatted profile fields (name/title/location/relationship/focus/close_contacts/style)"},
                },
                "required": ["address", "profile"],
            },
        },
        handler=_handle_set_contact_profile,
        emoji="✍️",
    )
except Exception as _e:
    logger.warning("[agentmail] set_contact_profile registration failed: %s", _e)

try:
    registry.register(
        name="email_summary",
        toolset=_TOOLSET,
        schema={
            "name": "email_summary",
            "description": "Retrieve the stored thread summary for the email thread containing this message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "Message id of any email in the thread"},
                },
                "required": ["message_id"],
            },
        },
        handler=_handle_email_summary,
        emoji="📝",
    )
except Exception as _e:
    logger.warning("[agentmail] email_summary registration failed: %s", _e)

try:
    registry.register(
        name="set_email_summary",
        toolset=_TOOLSET,
        schema={
            "name": "set_email_summary",
            "description": "Save the updated thread summary after replying (status/next steps per active topic).",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "Message id of the email just replied to or sent"},
                    "summary": {"type": "string", "description": "Fresh snapshot of active topics/decisions/pending actions"},
                },
                "required": ["message_id", "summary"],
            },
        },
        handler=_handle_set_email_summary,
        emoji="🗂️",
    )
except Exception as _e:
    logger.warning("[agentmail] set_email_summary registration failed: %s", _e)

# 3c. profile 生命周期钩子（地址自动注册/注销）
register_profile_hook("profile_created", _auto_register_email)
register_profile_hook("profile_deleted", _auto_deregister_email)

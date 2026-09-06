"""aimail_base — Runtime: preprocessor, hooks, profile, templates."""
from __future__ import annotations
import json
import logging
import os
import re
import time
import hmac
import hashlib
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, Dict, List


logger = logging.getLogger(__name__)
_TOOLSET = "agentmail"


# ═══════════════════════════════════════════════════════════════
# v1 API signature — canonical helper (single source of truth)
# Contract: aimail-gateway docs/API-SIGNATURE-PROTOCOL.md
# ═══════════════════════════════════════════════════════════════

def compute_api_signature(api_key: str, method: str, path: str,
                          body: bytes = b"",
                          timestamp_ms: Optional[int] = None) -> Optional[Dict[str, str]]:
    """Compute the v1 API signature headers.

    Returns ``{"X-Api-Timestamp": <ms>, "X-Api-Signature": <hex>}``, or ``None``
    when ``api_key`` is empty (caller then sends no signature headers).

    The raw API key never crosses the wire: the HMAC key is
    ``sha256(raw_key)`` (= the DB ``api_keys.key_hash``), which the client
    derives offline. The caller separately adds ``X-Api-Identity`` (the key's
    email for address-scoped keys, or its ``system_id`` for system-level keys).

    base string = ``METHOD\\n path_and_query \\n timestamp \\n sha256_hex(body)``
    sig = ``hex(HMAC-SHA256(key=sha256(raw_key) bytes, msg=base bytes))``

    ``path`` MUST be the exact request target (path + query, URL-encoded) that
    is sent on the wire, so the server's ``path_and_query()`` re-computes the
    identical base string. ``timestamp_ms`` is for tests (fixed vector);
    defaults to now.
    """
    if not api_key:
        return None
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    timestamp = str(timestamp_ms if timestamp_ms is not None
                    else int(time.time() * 1000))
    body_hash = hashlib.sha256(body).hexdigest()
    base = f"{method.upper()}\n{path}\n{timestamp}\n{body_hash}"
    sig = hmac.new(key_hash.encode("utf-8"), base.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return {"X-Api-Timestamp": timestamp, "X-Api-Signature": sig}


# ── persona 能力开关（跨 agent 系统共享，接入新系统时按能力设置）────
# True  = 支持 persona：派生地址 {role}.{profile}@{domain} 保留 + 配置校验 +
#         LLM session 前 persona 切换（Hermes 全能力，默认值）
# False = 不支持 persona：角色 = 独立 agent，收件地址归一为基础地址
#         （OpenClaw 等：aimail_base 被 import 后由系统层设 False）
# 处理逻辑框架一致，差异仅由本开关驱动——preprocess 内部读取。
PERSONA_SUPPORTED = True



# ═══════════════════════════════════════════════════════════════
# a2a_board helpers — template filling, role/context utilities
# ═══════════════════════════════════════════════════════════════


def fill_template(text: str, ctx: dict) -> str:
    """Replace {{KEY}} placeholders with values from ctx (keys uppercase)."""
    for key, val in ctx.items():
        text = text.replace("{{" + key + "}}", str(val))
    return text


def _read_role_file(name: str) -> str:
    """Read a2a_board role file — address-level first, then system-level.

    Role names are case-insensitive: the filename is always lowercased
    before lookup, so callers may pass any casing.

    Priority:
    1. ~/.aimail/systems/{sid}/{addr}/role_prompt/{name}.md  (address override)
    2. ~/.aimail/systems/{sid}/board/role_prompt/{name}.md   (system-level)
    3. common.md fallback (system-level dir)
    """
    name = name.lower()
    cfg = _load_profile_config()
    sid = cfg.get("system_id", "default") if cfg else "default"
    addr = _clean_agent_dir_name(cfg.get("email", "")) if cfg and cfg.get("email") else ""
    sys_role_dir = _aimail_system_dir(sid) / "board" / "role_prompt"
    # 1) address-level override
    if addr:
        addr_role_dir = _aimail_system_dir(sid) / addr / "role_prompt"
        p = addr_role_dir / f"{name}.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    # 2) system-level exact match
    p = sys_role_dir / f"{name}.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    # 3) common.md fallback
    common = sys_role_dir / "common.md"
    if common.exists():
        logger.info("[a2a_board] role '%s' not found, using common.md", name)
        return common.read_text(encoding="utf-8")
    logger.warning("[a2a_board] role file not found: %s (common.md also missing)", name)
    return ""


def build_ctx(payload: dict, headers: dict) -> dict:
    """Build template context dict from available data."""
    return {
        "AGENTMAIL_ADDRESS": payload.get("my_amail_addr", ""),
        "BOARD_ID": payload.get("board_id", ""),
        "BOARD_ROLE": payload.get("board_role", ""),
        "FROM_ROLE": payload.get("from_role", ""),
        "INQUIRY_SENDER": payload.get("from", ""),
        "INQUIRY_SUBJECT": payload.get("subject", ""),
        "SOUL_MD_CONTENT": _read_soul_md(),
        "SKILLS_LIST": ", ".join(_read_skills()),
    }


# ── Config helpers ──

def aimail_home() -> Path:
    """Canonical aimail home root (single source of truth).

    Resolves env AIMAIL_HOME to a home-root dir,
    falling back to ~/.aimail. All path constructors (mail/{clean},
    systems/, logs/) derive from this so the env var relocates the whole
    tree consistently on Python and TS sides (mirrors TS config.ts
    AIMAIL_HOME()).
    """
    env = os.environ.get("AIMAIL_HOME", "")
    return Path(env).expanduser() if env else Path.home() / ".aimail"


def _aimail_system_dir(system_id: str = "") -> Path:
    """Return ~/.aimail/systems/{system_id}/ for config storage.
    
    When system_id is empty, returns ~/.aimail/systems/ itself."""
    base = aimail_home() / "systems"
    return base / system_id if system_id else base


def _gateway_config_path(system_id: str = "") -> Path:
    """Return path to the gateway config file.
    
    When system_id is provided, returns system-specific path.
    When empty, returns the base ~/.aimail/systems/ level (caller should resolve system_id).
    
    Canonical name: aimail_gateway.json (2026-09-04, aligned with the gateway
    binary name). Legacy agentmail_gateway.json is auto-migrated on first
    access."""
    base_path = _aimail_system_dir(system_id)
    p = base_path / "aimail_gateway.json"
    legacy = base_path / "agentmail_gateway.json"
    if legacy.is_file() and not p.is_file():
        try:
            legacy.rename(p)
            os.chmod(p, 0o600)
        except Exception:
            pass
    return p


def _load_gateway_config(system_id: str = "") -> Optional[dict]:
    """load gateway connection config

    Reads from (in priority order):
    1. Environment variables (AIMAIL_GATEWAY_URL + AIMAIL_ADMIN_KEY/AIMAIL_PRODUCT_CODE)
    2. ~/.aimail/systems/{system_id}/aimail_gateway.json (direct, or via the platform adapter's profile-dir resolver -> .agentmail pointer)
    """
    # Try environment variables first
    gateway_url = os.environ.get("AIMAIL_GATEWAY_URL", "")
    admin_key = os.environ.get("AIMAIL_ADMIN_KEY", "")
    product_code = os.environ.get("AIMAIL_PRODUCT_CODE", "")
    domain = os.environ.get("AIMAIL_DOMAIN", "")
    # Fallback: map AIMAIL_BRIDGE_URL → webhook_host
    raw_webhook = os.environ.get("AIMAIL_WEBHOOK_HOST", "") or os.environ.get("AIMAIL_BRIDGE_URL", "")
    if raw_webhook:
        # Strip protocol and /path to get host:port
        raw_webhook = raw_webhook.replace("http://", "").replace("https://", "").split("/")[0]
    if gateway_url and (admin_key or product_code):
        return {
            "gateway_url": gateway_url,
            "admin_key": admin_key,
            "product_code": product_code,
            "system_id": system_id,
            "domain": domain,
            "manager_address": os.environ.get("AIMAIL_MANAGER_ADDRESS", ""),
            "webhook_host": raw_webhook,
        }

    # Try ~/.aimail/systems/{system_id}/aimail_gateway.json
    resolved_sid = system_id
    if not resolved_sid:
        # Resolve from HERMES_PROFILE_DIR/.agentmail pointer
        profile_dir = _PROFILE_DIR_RESOLVER() if _PROFILE_DIR_RESOLVER else None
        if profile_dir:
            pointer = Path(profile_dir) / ".agentmail"
            if pointer.is_file():
                try:
                    pointer_data = json.loads(pointer.read_text())
                    resolved_sid = pointer_data.get("system_id", "")
                except Exception:
                    pass
        if not resolved_sid:
            raise RuntimeError(
                "system_id not provided and no platform pointer (.agentmail) found "
                "-- cannot locate gateway config (set system_id or run aimail install)"
            )

    gw_path = _gateway_config_path(resolved_sid)
    if gw_path.is_file():
        try:
            cfg = json.loads(gw_path.read_text())
            if cfg.get("gateway_url") and (cfg.get("admin_key") or cfg.get("product_code")):
                return cfg
        except Exception:
            pass

    return None


# ── 注入点（适配层设置；Hermes → tools/hermes/aimail_hermes.py，
#             OpenClaw → tools/openclaw/amail_base.py）────────────────
# 平台差异（config 来源/personas/profile 目录/board 登记）由适配层注入，
# 公共核心保持平台无关。未注入时使用安全默认（None/空/no-op）。
_CONFIG_LOADER = None          # () -> Optional[dict]      agent 配置加载
_PERSONAS_PROVIDER = None      # () -> dict                personas 配置
_PROFILE_DIR_RESOLVER = None   # () -> Optional[str]       profile 目录（gateway config 定位）
_SOUL_PROVIDER = None          # () -> str                 SOUL 内容（board ctx）
_SKILLS_PROVIDER = None        # () -> list[str]           skills 列表（board ctx）
_BOARD_GATEWAY_SINK = None     # (board_id, gateway_url) -> None
# ping/pong 拦截的 pong 回发函数。Hermes 与 OpenClaw 共享同一实现
# (send_pong)——无平台差异("结尾如何调 agent 可不同"不适用于 pong,
# 它始终是 http 出站 send_mail)。默认即共享实现,无需平台注入。
_PONG_SENDER = None            # (body, pong_id) -> bool (保留兼容,恒等于 send_pong)


def _read_soul_md() -> str:
    """SOUL 内容（注入点）。Hermes 适配层注入；默认空。"""
    return _SOUL_PROVIDER() if _SOUL_PROVIDER is not None else ""


def _read_skills() -> list:
    """skills 列表（注入点）。Hermes 适配层注入；默认空。"""
    return _SKILLS_PROVIDER() if _SKILLS_PROVIDER is not None else []


def _load_profile_config() -> Optional[dict]:
    """agent 配置加载（注入点）。适配层注入平台实现；未注入返回 None
    （preprocess 走 'not configured' 分支）。"""
    if _CONFIG_LOADER is not None:
        return _CONFIG_LOADER()
    return None


def list_personas() -> dict:
    """personas 配置（注入点）。默认空（无 persona 配置）。"""
    if _PERSONAS_PROVIDER is not None:
        return _PERSONAS_PROVIDER()
    return {}


def _register_board_gateway(board_id: str, gateway_url: str) -> None:
    """board 网关注册（注入点）。Hermes 适配层注入写 profile_cfg；默认 no-op。"""
    if _BOARD_GATEWAY_SINK is not None:
        _BOARD_GATEWAY_SINK(board_id, gateway_url)


# ── 平台无关 agent 上下文（兜底 MCP/CLI 共用,2026-08-18 提升）─────
# 原实现位于 tools/openclaw/amail_base.py(set_agent_context/load_agent_config),
# 仅 OpenClaw 可用;MCP server 提升为共享服务后,任何 agent 系统只需按共享
# 布局落 agentmail.json 即可复用。OpenClaw 适配层转发此实现(单一权威)。
_ACTIVE_AGENT_CONFIG: Optional[dict] = None  # 最近一次 set_agent_context 的配置


def _scan_systems_for_agent(agent_id: str, system_id: str = "") -> Optional[dict]:
    """按 agentId 遍历 systems/{sid}/*/agentmail.json 找匹配配置(平台无关)。

    system_id 缺省时扫描全部 systems/ 目录;命中返回 agentmail.json 内容。
    """
    base = aimail_home() / "systems"
    candidates = [base / system_id] if system_id else (
        sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else [])
    for sys_dir in candidates:
        if not sys_dir.is_dir():
            continue
        for addr_dir in sorted(sys_dir.iterdir()):
            aj = addr_dir / "agentmail.json"
            if not aj.is_file():
                continue
            try:
                cfg = json.loads(aj.read_text())
                if cfg.get("agent_id") == agent_id:
                    cfg.setdefault("system_id", sys_dir.name)
                    return cfg
            except Exception:
                continue
    return None


def load_agent_config(agent_id: str, system_id: str = "") -> Optional[dict]:
    """按 agentId 找地址键 config(共享布局 agentmail.json,平台无关)。"""
    return _scan_systems_for_agent(agent_id, system_id)


def set_agent_context(agent_id: str, system_id: str = "") -> None:
    """把当前 agent 的 config 挂到公共核心注入点(平台无关,兜底 MCP 服务用)。

    原 OpenClaw 版(读 ~/.openclaw/.agentmail 指针 + AIMAIL_AGENT_EMAIL)提升为
    共享实现:遍历 systems/{sid}/*/agentmail.json 匹配 agent_id,命中后设置
    _CONFIG_LOADER 与 AIMAIL_AGENT_EMAIL(日志落位),供 preprocess 与 6 工具共用。
    未注册 → RuntimeError(与 OpenClaw 原语义一致)。
    """
    global _ACTIVE_AGENT_CONFIG, _CONFIG_LOADER
    cfg = _scan_systems_for_agent(agent_id, system_id)
    if cfg is None:
        raise RuntimeError(f"agent '{agent_id}' not registered — run register_agent.py first")
    _ACTIVE_AGENT_CONFIG = cfg
    _CONFIG_LOADER = lambda: cfg  # noqa: E731
    if cfg.get("email"):
        os.environ["AIMAIL_AGENT_EMAIL"] = cfg["email"]
    os.environ.setdefault("AIMAIL_AGENT_ID", agent_id)
    os.environ.setdefault("AIMAIL_SYSTEM_ID", cfg.get("system_id", system_id))


def _clean_agent_dir_name(addr: str) -> str:
    """agent 地址 → 目录名：非 [A-Za-z0-9_.-] 字符替换为 _。

    邮件地址是 7-bit ASCII(RFC 5321),re.ASCII 让 \\w 退化为 [a-zA-Z0-9_],
    任何非 ASCII 字符(理论上不出现)也归一为 _,与 TS cleanAddr(/[^\\w.-]/g)
    1:1 对齐,且文件系统路径始终 ASCII 安全。
    与 bridge 顶层 agent 目录命名同规则（mike_aimail.token.tm）。
    """
    return re.sub(r"[^\w.\-]", "_", addr, flags=re.ASCII)


def _agent_config_path(system_id: str, email: str) -> Path:
    """地址键 per-agent 配置路径：systems/{sid}/{cleaned_addr}/agentmail.json。"""
    return _aimail_system_dir(system_id) / _clean_agent_dir_name(email) / "agentmail.json"


def route_agent_for_email(registry: dict, email: str) -> str:
    """收件地址 → agent_id（精确匹配 + persona 前缀剥离：support.alice@… → alice@…）。

    多收件人入站路由共享（OpenClaw/DeerFlow 等单入多出平台）。
    """
    if email in registry:
        return registry[email]
    local = email.split("@")[0]
    for addr, agent_id in registry.items():
        base_local = addr.split("@")[0]
        if local and local.endswith("." + base_local):
            return agent_id
    return ""


def render_message(payload: dict) -> str:
    """把富化后的 amail payload 组装成 agent 输入 message。

    对齐 Hermes webhook.py 空模板 fallback 渲染语义
    （json.dumps(payload, indent=2)[:4000]），各平台保持一致。
    """
    return json.dumps(payload, indent=2)[:4000]


def _read_pointer(pointer: Path) -> dict:
    """读取 {dir}/.aimail 指针（{system_id, email}）。缺失/损坏返回 {}。"""
    if pointer.is_file():
        try:
            return json.loads(pointer.read_text())
        except Exception:
            pass
    return {}


def _write_pointer(pointer: Path, system_id: str, email: str) -> None:
    """写 {dir}/.aimail 指针（系统身份唯一来源）。"""
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps({"system_id": system_id, "email": email}, indent=2, ensure_ascii=False) + "\n"
    )


def _board_creds_path() -> Optional[Path]:
    """Universal per-agent board credential path.

    ~/.aimail/systems/{system_id}/{agent_addr_cleaned}/board_creds.json

    The directory key is the agent's FINAL address (path-unsafe chars
    replaced) — every agent system (Hermes, OpenClaw, ...) follows this
    one convention, so a system's agents never share a creds file.
    """
    try:
        cfg = _load_profile_config()
        if not cfg:
            return None
        sid = cfg.get("system_id", "")
        addr = cfg.get("email", "")
        if not sid or not addr:
            return None
        cleaned = _clean_agent_dir_name(addr)
        return _aimail_system_dir(sid) / cleaned / "board_creds.json"
    except Exception:
        return None


def _store_board_credential(board_id: str, gateway_url: str, token: str) -> None:
    """board 凭据存储（共享默认实现）：写入 per-agent board_creds.json。

    所有平台共用。此前是 Hermes 适配层注入的 sink（OpenClaw 未注入 → 凭据
    静默不落盘），现提升到共享核心作默认实现，各平台无需注入即得持久化。
    """
    try:
        creds_path = _board_creds_path()
        if creds_path is None:
            return
        creds = {}
        if creds_path.exists():
            try:
                creds = json.loads(creds_path.read_text())
            except Exception:
                pass
        creds[board_id] = {"gateway_url": gateway_url, "token": token}
        creds_path.parent.mkdir(parents=True, exist_ok=True)
        creds_path.write_text(json.dumps(creds, indent=2))
    except Exception:
        pass


def _put_contact_profile(address: str, profile: str) -> dict:
    from aimail_tools import _GatewayClient
    config = _load_profile_config()
    if not config:
        return {"success": False, "error": "aimail not configured for this profile"}
    client = _GatewayClient(config["gateway_url"], config["api_key"],
                            identity=config.get("email", ""))

    result = client.put_contact(address, profile)
    if result.get("status") == 200:
        return {"success": True}
    error = result.get("error", f"HTTP {result.get('status')}")
    return {"success": False, "error": f"Failed to store profile: {error}"}




# ═══════════════════════════════════════════════════════════════
# Gateway Preprocessor — inbound mail payload transformation
# ═══════════════════════════════════════════════════════════════

# ── Ping/pong interception (shared by Hermes + OpenClaw) ────────────
# Single implementation: Hermes webhook preprocess, OpenClaw poll and
# OpenClaw bridge all call handle_ping_pong() instead of each writing
# their own copy — trigger conditions stay identical everywhere.
#
# PREFIX CONTRACT: gateway send.rs P0 interception matches
# "__amail_pong__:" (redirects pong to inbound instead of outbound SMTP).
# Agent-side PONG_PREFIX MUST equal that exact string — otherwise the
# pong goes out as a normal outbound email and never loops back to the
# agent preprocess chain. PING_PREFIX is agent-side only (ping enters
# via SMTP as a normal inbound mail; no gateway-side ping matching).
PING_PREFIX = "__aimail_ping__:"
PONG_PREFIX = "__amail_pong__:"


def is_ping(subject: str) -> bool:
    return isinstance(subject, str) and subject.startswith(PING_PREFIX)


def is_pong(subject: str) -> bool:
    return isinstance(subject, str) and subject.startswith(PONG_PREFIX)


def ping_id(subject: str) -> str:
    return subject.split(":", 1)[1].strip() if is_ping(subject) else ""


def handle_ping_pong(
    body: dict,
    send_pong_fn=None,
) -> Optional[str]:
    """Unified ping/pong interception for all agent platforms.

    Returns "ping" (pong sent back via send_pong_fn), "pong"
    (acknowledged — caller swallows it), or None (not a ping/pong).
    """
    subject = body.get("subject", "")
    if is_ping(subject):
        pid = ping_id(subject)
        if send_pong_fn is not None:
            try:
                send_pong_fn(body, pid)
            except Exception:
                pass
        return "ping"
    if is_pong(subject):
        return "pong"
    return None


def send_pong(body: dict, pong_id_value: str) -> bool:
    """SHARED pong sender — one implementation for every agent platform.

    Sends the pong via the gateway HTTP send API (outbound path), so the
    gateway's P0 interception (send.rs matches __amail_pong__:) redirects
    it back as inbound — closing the ping→pong→agent loop. Platform-agnostic:
    - Hermes:   _CONFIG_LOADER injected → profile config → aimail_tools
    - OpenClaw: adapter injects a loader that resolves the agent config
      (CLI/subprocess path handled by the injected loader, not here)
    No platform-specific code lives in this function.
    """
    try:
        from aimail_tools import send_mail
        to = body.get("from", "")
        if not to:
            return False
        res = send_mail(
            to=to,
            subject=f"{PONG_PREFIX}{pong_id_value}",
            body='{"ping_id": "%s", "event": {"mail_id": "%s"}}'
            % (pong_id_value, body.get("mail_id", "")),
            message_id=str(body.get("mail_id", "")) or None,
        )
        _log_ping_event("pong_sent", pong_id_value, body,
                        "ok" if res.get("success") else str(res.get("error", "?")))
        return bool(res.get("success"))
    except Exception as e:
        _log_ping_event("pong_sent", pong_id_value, body, str(e))
        return False


def aimail_log_path(email: str = "") -> Path:
    """Canonical per-agent processing log path (user-mandated 2026-08-16).

    All agent logs live under {AIMAIL_HOME|~/.aimail}/logs/, one file
    per agent: aimail.{cleaned_addr}.log — NOT inside mail/{addr}/.
    """
    cleaned = _clean_agent_dir_name(email) if email else "default"
    base = aimail_home()
    return base / "logs" / f"aimail.{cleaned}.log"


def _log_ping_event(dir_: str, ping_id: str, payload: dict, pong_status: str = ""):
    """Append a JSON line to aimail.log for ping-pong loop tracking.

    Shared by all platforms — same file layout, same three dir values:
    ping_intercepted / pong_sent / pong_returned. Written at the gateway
    (webhook.py Hermes) or poll/bridge (OpenClaw) intercept point.
    """
    try:
        _entry = {
            "ts": datetime.now().astimezone().isoformat(),
            "dir": dir_, "ping_id": ping_id,
            "from": payload.get("from", ""),
            "to": payload.get("to", ""),
        }
        if pong_status:
            _entry["pong_status"] = pong_status
        # Resolve agent email: recipient (payload.to, the agent's own address
        # — platform-independent) > sender > agent pointer
        _email = ""
        _to = payload.get("to") or payload.get("recipients") or []
        if isinstance(_to, str):
            _to = [_to]
        if _to:
            _first = str(_to[0]).strip()
            if "@" in _first:
                _email = _first
        if not _email:
            _from = payload.get("from", "")
            if isinstance(_from, str) and "@" in _from:
                _email = _from
        if not _email:
            # Try common agent pointers (Hermes profile, OpenClaw, AGENT_HOME)
            _candidates = [
                os.environ.get("AGENT_MAIL_POINTER", ""),
                os.environ.get("HERMES_PROFILE_DIR", ""),
            ]
            if not any(_candidates):
                _home = os.environ.get("AGENT_HOME", "")
                if _home:
                    _candidates.append(os.path.join(_home, ".agentmail"))
            for _ptr in _candidates:
                if not _ptr:
                    continue
                _p = Path(_ptr)
                if _p.is_dir():
                    _p = _p / ".agentmail"
                if _p.is_file():
                    try:
                        _email = json.loads(_p.read_text()).get("email", "")
                    except Exception:
                        pass
                    if _email:
                        break
        # Canonical per-agent log: {logs}/aimail.{cleaned_addr}.log
        _log_path = aimail_log_path(_email)
        _log_dir = _log_path.parent
        os.makedirs(_log_dir, exist_ok=True)
        with open(_log_path, "a") as _f:
            _f.write(json.dumps(_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def process_inbound_mail(payload: dict, headers: dict) -> Optional[dict]:
    """THE shared inbound middle pipeline (single call, every platform).

    Runs the full preprocessing (identity → persona → enrichment →
    whoami → store) FIRST, then intercepts ping/pong at the very LAST
    step — right before the agent is invoked. A ping therefore
    exercises the entire inbound chain; the pong is only replied when
    every step worked — maximizing E2E verification of the pipeline
    (if any middle step breaks, no pong comes back).
    """
    pong_sender = _PONG_SENDER if _PONG_SENDER is not None else send_pong
    enriched = preprocess_mail_payload(payload, headers)
    # ── LAST: ping/pong interception ──
    # Detection is subject-based (no enriched fields needed), so it runs
    # even when preprocess returned None (e.g. pull-mode batch without an
    # agent context): a ping must still be swallowed + logged + ponged.
    # Use the RAW payload for detection AND logging — preprocess's enriched
    # copy drops to/cc, which would break aimail.log dir resolution
    # (falls back to sender → wrong mail dir).
    subject = payload.get("subject", "")
    intercept = handle_ping_pong(payload, pong_sender)
    if intercept is not None:
        _log_ping_event(
            "ping_intercepted" if intercept == "ping" else "pong_returned",
            subject.split(":", 1)[1].strip() if ":" in subject else "",
            payload,
        )
        logger.info("[aimail_gateway] ping/pong intercepted — swallowed at the last step")
        return None
    return enriched


def preprocess_mail_payload(payload: dict, headers: dict) -> Optional[dict]:
    """Preprocess aimail webhook payload before prompt rendering.

    Returns None when the event must be swallowed (ping/pong
    interception — the webhook adapter responds "ignored" and no agent
    run happens), otherwise the (possibly enriched) payload dict.

    Rust backend already handles text cleaning. Python side handles:

    _extract_board_gateway(payload)  # board gateway URL registry
    - Persona extraction from 'to' address (persona.profile@domain format)
    - Persona validation against configured personalities
    - direct_message / mentioned (persona-aware matching)
    - attachment download
    """
    result = dict(payload)
    body = result.get("body", "")

    if not body:
        logger.warning("[aimail_gateway] body is empty in raw payload — keys=%s", list(payload.keys())[:12])

    # Agent identity (for direct_message / mentioned)
    config = _load_profile_config()
    agent_email = config.get("email", "") if config else ""
    system_name = config.get("system_name", "") if config else ""

    if not agent_email:
        logger.warning("[aimail_gateway] No email configured for this profile — inbound preprocessing skipped")
        # Still return a recognizable payload so the gateway continues
        result["_preprocess_error"] = "aimail email not configured"
        return result

    # ── Extract display names from headers before stripping ──
    import re as _re
    _name_re = _re.compile(r'^(.+?)\s*<')
    _email_re = _re.compile(r'<([^>]+)>')

    def _parse_header_addrs(header_val: str):
        results = []
        for part in header_val.split(','):
            part = part.strip()
            if not part:
                continue
            m = _email_re.search(part)
            if m:
                email = m.group(1).strip().lower()
                nm = _name_re.match(part)
                name = nm.group(1).strip() if nm else email.split('@')[0]
            elif '@' in part:
                email = part.strip().lower()
                name = email.split('@')[0]
            else:
                continue
            results.append((name, email))
        return results

    def _to_list(v):
        if isinstance(v, list):
            return [s.strip() for s in v if s and s.strip()]
        if isinstance(v, str):
            return [s.strip() for s in v.split(',') if s.strip()]
        return []

    def _base_email(email: str) -> str:
        """Strip persona prefix: support.alice@agent.com -> alice@agent.com"""
        persona, profile, sys_name = parse_amail_persona(email, system_name)
        domain = email.split('@', 1)[1] if '@' in email else ''
        if sys_name:
            return f"{profile}.{sys_name}@{domain}"
        return f"{profile}@{domain}"

    to_raw = _to_list(result.get("to", []))
    cc_raw = _to_list(result.get("cc", []))

    # Extract display names from MIME headers
    raw_headers = result.get("headers", {}) or {}
    to_named = _parse_header_addrs(raw_headers.get("to", ""))
    cc_named = _parse_header_addrs(raw_headers.get("cc", ""))

    def _fmt(n, e): return f"{n} <{e}>" if n else e

    if to_named:
        to_display = [_fmt(n, e) for n, e in to_named]
    else:
        to_display = to_raw
    if cc_named:
        cc_display = [_fmt(n, e) for n, e in cc_named]
    else:
        cc_display = cc_raw
    result["recipients"] = {"to": to_display, "cc": cc_display}

    # Bare emails for matching
    to_bare = [e for _, e in to_named] if to_named else [a.lower() for a in to_raw]
    cc_bare = [e for _, e in cc_named] if cc_named else [a.lower() for a in cc_raw]

    # Set sender field with display name (SKILL.md defines "sender", not "from")
    from_named = _parse_header_addrs(raw_headers.get("from", ""))
    if from_named:
        result["sender"] = _fmt(from_named[0][0], from_named[0][1])

    # ── Persona extraction from 'to' address ──
    # Find the recipient that belongs to our agent domain
    agent_domain = agent_email.split('@', 1)[1] if agent_email and '@' in agent_email else ''
    my_to_addr = ''
    for addr in to_bare:
        if agent_domain and addr.endswith('@' + agent_domain):
            my_to_addr = addr
            break

    persona, profile, _sys_name = parse_amail_persona(my_to_addr, system_name) if my_to_addr else ('', '', '')
    if persona:
        if not PERSONA_SUPPORTED:
            # 系统不支持 persona：收件地址归一为基础地址（剥离 persona 前缀），
            # 不做配置校验与派生地址保留——agent 身份即注册的基础地址。
            result["my_amail_addr"] = agent_email
        else:
            # Validate persona against configured personalities
            configured = list_personas()
            if persona in configured:
                result["my_amail_addr"] = my_to_addr
            else:
                logger.warning("[aimail_gateway] Persona '%s' not found in agent.personalities — falling back to base address", persona)
                # 未配置 persona：剥离 persona 前缀，回退注册基础地址（与创建端幂等）
                result["my_amail_addr"] = agent_email
    if not result.get("my_amail_addr"):
        result["my_amail_addr"] = my_to_addr or agent_email

    # ── Persona-aware direct_message / mentioned ──
    if agent_email:
        agent_email_lower = agent_email.lower()
        agent_base = _base_email(agent_email_lower)
        all_bare = to_bare + cc_bare
        all_base = [_base_email(a) for a in all_bare]

        # DM: only one to-recipient, and it's us (persona-aware)
        result["direct_message"] = (
            len(to_bare) == 1
            and not cc_bare
            and all_base[0] == agent_base
        )

        # mentioned: match profile name and display name
        agent_local = agent_email.split('@')[0]
        agent_display = ''
        for n, e in to_named + cc_named:
            if _base_email(e) == agent_base and n:
                agent_display = n
                break
        match_targets = [agent_local, profile] if profile else [agent_local]
        if agent_display:
            match_targets.append(agent_display)
        body_lower = (body or "").lower()
        result["mentioned"] = any(
            f'@{t.lower()}' in body_lower or t.lower() in body_lower.split()
            for t in match_targets if t
        ) if agent_email else False
    else:
        result["direct_message"] = False
        result["mentioned"] = False

    # ── B1: batch profile injection (one gateway round-trip) ──
    # my_profile / sender_profile / recipients_profile come from a single
    # GET /api/v1/contacts?addresses=... call. Sender goes FIRST (the
    # endpoint treats the first address as the inbound sender); the rest
    # are recipients. my_profile is the calling agent's approved persona
    # (domain_addr_meta) — the single source of truth for who the agent is.
    from aimail_tools import _GatewayClient as _GC
    from aimail_base import _load_gateway_config as _load_gw_cfg
    sender_bare = payload.get("from", "")
    if isinstance(sender_bare, str) and sender_bare:
        sender_bare = sender_bare.strip().lower()
    batch_addrs = [sender_bare] + to_bare + cc_bare if sender_bare else to_bare + cc_bare
    _seen = set()
    batch_addrs = [a for a in batch_addrs if a and not (a in _seen or _seen.add(a))]
    if batch_addrs:
        _gw = _load_gw_cfg()
        _ak = (config or {}).get("api_key", "")
        if _gw and _ak:
            profiles = _GC(_gw["gateway_url"], _ak).get_contact_profiles(batch_addrs)
            if profiles:
                my_profile = profiles.get("my_profile")
                if my_profile and isinstance(my_profile, dict):
                    result["my_profile"] = my_profile.get("profile")
                if profiles.get("sender_profile"):
                    result["sender_profile"] = profiles["sender_profile"]
                if profiles.get("recipients_profile"):
                    result["recipients_profile"] = profiles["recipients_profile"]
        else:
            logger.warning("[aimail_gateway] batch profiles skipped: no gateway config or api_key")

    # ── B2: thread_summary preload (pure local, no gateway round-trip) ──
    # thread_id = first References entry (thread root), else the message_id
    # itself — identical to store_inbound_message's write-time derivation.
    # Only pre-existing threads are injected; a first mail in a thread has
    # no file yet and gets nothing.
    _mid = (result.get("message_id") or "").strip()
    _refs = result.get("references") or []
    if isinstance(_refs, str):
        _refs = [r.strip() for r in _refs.split() if r.strip()]
    _tid = (_refs[0] if _refs else _mid)
    if _tid:
        try:
            from aimail_tools import _thread_path
            _tp = _thread_path(_tid)
            if _tp.exists():
                _td = json.loads(_tp.read_text(encoding="utf-8"))
                _summary = (_td.get("summary") or "").strip()
                if _summary:
                    result["thread_summary"] = _summary
        except Exception as _e:
            logger.warning("[aimail_gateway] thread_summary preload failed: %s", _e)

    attachments = result.get("attachments")

    if attachments and isinstance(attachments, list) and len(attachments) > 0:
        # Use profile api_key (agent scope) instead of admin_key for
        # download_attachment — the admin_key may have agent_admin scope
        # which does not include agent-level attachment access.
        profile = _load_profile_config()
        agent_key = (profile or {}).get("api_key", "")
        if not agent_key:
            logger.warning("[aimail_gateway] Cannot download attachments: no agent api_key in profile")
            return result

        config = _load_gateway_config()
        if not config:
            logger.warning("[aimail_gateway] Cannot download attachments: no gateway config")
            return result

        from aimail_tools import _GatewayClient
        client = _GatewayClient(config["gateway_url"], agent_key,
                                identity=(profile or {}).get("email", ""))
        local_paths = []

        # Attachments land beside the email JSON snapshot (sibling dir keyed by
        # message). The agent reads these files directly from here — this is the
        # primary landing, not a cache; a per-message dir removes cross-message
        # filename collisions. Function-level import breaks the base<->tools
        # import cycle (resolved at call time, after both modules load).
        from aimail_tools import _aimail_dir, _sanitize_message_id
        attch_dir = (
            _aimail_dir()
            / datetime.now().strftime("%Y%m")
            / "attch"
            / _sanitize_message_id(result.get("message_id", "") or "unknown")
        )
        attch_dir.mkdir(parents=True, exist_ok=True)

        for att in attachments:
            if not isinstance(att, dict):
                continue
            att_id = att.get("attachment_id", att.get("id", ""))
            fname = att.get("filename", att.get("name", "unnamed_attachment"))
            if not att_id:
                continue

            content = client.download_attachment(att_id)
            if content is None:
                continue

            # Save beside the email JSON snapshot (see attch_dir above).
            safe_name = Path(fname).name or "unnamed_attachment"
            local_path = attch_dir / safe_name
            local_path.write_bytes(content)
            local_paths.append(str(local_path))

            # Convert binary documents to markdown (DOCX, XLSX, PDF, HTML)
            ext = Path(fname).suffix.lower()
            if ext in (".docx", ".xlsx", ".html", ".htm"):
                try:
                    from markitdown import MarkItDown
                    md_text = MarkItDown().convert(str(local_path)).text_content
                    if md_text.strip():
                        md_path = attch_dir / f"{Path(fname).stem}.md"
                        md_path.write_text(md_text)
                        local_paths.append(str(md_path))
                except Exception:
                    pass  # keep original, agent falls through to PDF skill

        result["attachments"] = local_paths

    # ── Strip backend-only fields not in SKILL.md to avoid LLM confusion ──
    for field in ("mail_id", "to", "cc", "headers", "created_at", "forwarder", "forward_at"):
        result.pop(field, None)

    # ── Store message metadata + optional raw snapshot ──────────
    mid = result.get("message_id", "")
    refs = result.get("references", [])
    my_addr = result.get("my_amail_addr", "")
    if mid and my_addr:
        from aimail_tools import store_inbound_message, _log_amail
        store_inbound_message(mid, refs, my_addr, preprocessed_payload=result)
        # Lightweight log entry
        _from = raw_headers.get("from", payload.get("from", ""))
        _subj = (raw_headers.get("subject") or raw_headers.get("Subject")
                 or payload.get("subject") or payload.get("Subject") or "")
        _log_amail("inbound", str(_from), my_addr, str(_subj))

    # ── a2a_board: [WhoAmI]问询检测 ──
    subject = (payload.get("subject") or "").strip()
    if subject.upper().startswith("[WHOAMI]"):
        ctx = build_ctx(result, dict(headers))
        whoami_raw = _read_role_file("whoami")
        if whoami_raw:
            result["_whoami_prompt"] = fill_template(whoami_raw, ctx)
        return result

    # ── B3: Role_Calibrator (persona update request) ──
    # A manager email whose subject contains "update persona" asks the agent
    # to draft a new persona + signature. The gateway does NOT intercept it
    # (no manager trigger word), so it reaches the agent; we inject the
    # Role_Calibrator role prompt (SOUL + skills auto-filled by build_ctx)
    # and let the LLM draft and reply within the session. Early return so a
    # board role prompt cannot clobber _role_prompt.
    if "update persona" in subject.lower():
        ctx = build_ctx(result, dict(headers))
        calib_raw = _read_role_file("role_calibrator")
        if calib_raw:
            result["_role_prompt"] = fill_template(calib_raw, ctx)
        else:
            logger.warning("[aimail_gateway] Role_Calibrator role file missing — persona update will proceed without a role prompt")
        return result

    # ── a2a_board: Board上下文检测（由Rust A2aInterceptor注入 board_id / board_role）──
    board_id = result.get("board_id")
    board_role = result.get("board_role")
    if board_id and board_role:
        ctx = build_ctx(result, dict(headers))
        role_raw = _read_role_file(board_role)
        if role_raw:
            result["_role_prompt"] = fill_template(role_raw, ctx)
        sender = result.get("from", "")
        result["_a2a_session_key"] = f"a2a:{board_id}:{sender}"

    return result


# ═══════════════════════════════════════════════════════════════
# Profile Hook System
# ═══════════════════════════════════════════════════════════════

_profile_hooks: Dict[str, List[Callable]] = {
    "profile_created": [],
    "profile_deleted": [],
}


# ── Hook: auto-register email on profile creation ──────────────

def parse_amail_persona(email: str, system_name: str = "") -> tuple:
    """Parse persona, profile, and system_name from an aimail address.
    
    Returns (persona, profile_name, sys_name).
    
    Shared domain (three-part: persona.profile.sys_name@domain):
      'support.ql-biopharm.myco@aimail.token.tm'  → ('support', 'ql-biopharm', 'myco')
      'ql-biopharm.myco@aimail.token.tm'           → ('', 'ql-biopharm', 'myco')
      'myco@aimail.token.tm'                       → ('', 'default', 'myco')  ← short form
    
    Non-shared domain (two-part: persona.profile@domain):
      'support.alice@agent.com'  → ('support', 'alice', '')
      'alice@agent.com'          → ('', 'alice', '')
    """
    local = email.split('@')[0] if '@' in email else email
    parts = local.split('.')
    
    # If system_name is known and local part matches → short form (default agent)
    if system_name and len(parts) == 1 and parts[0] == system_name:
        return ('', 'default', system_name)
    
    # Three-part: persona.profile.sys_name@domain
    if system_name and len(parts) >= 2 and parts[-1] == system_name:
        sys_name = parts[-1]
        profile_parts = parts[:-1]
        if len(profile_parts) >= 2:
            return ('.'.join(profile_parts[:-1]), profile_parts[-1], sys_name)
        return ('', profile_parts[0], sys_name)
    
    # Traditional: persona.profile@domain
    if len(parts) >= 2:
        return ('.'.join(parts[:-1]), parts[-1], '')
    return ('', parts[0], '')


# ── Board gateway URL registry ──
_board_gateways: dict = {}
_board_gateways_lock = threading.Lock()

def _extract_board_gateway(payload: dict):
    """Extract board_id and gateway_url from board notification emails."""
    subject = payload.get("subject", "")
    body = payload.get("body", "")
    from_addr = payload.get("from", "")
    if ".a2a@" not in from_addr and not subject.startswith("[A2A]"):
        return
    token_match = re.search(r'Token:\s*(bdt_\S+)', body)
    gw_match = re.search(r'API:\s*(https?://\S+)', body)
    if not gw_match:
        return
    gateway_url = gw_match.group(1).rstrip()
    from_match = re.search(r'(\S+)\.a2a@', from_addr)
    if not from_match:
        return
    board_short_id = from_match.group(1)
    gw_domain = re.search(r'://([^/]+)', gateway_url)
    domain = gw_domain.group(1) if gw_domain else ""
    # board_id must match the gateway's derive_board_id: hash of the FULL
    # board address ({short}.a2a@{domain}) — embeds system name on shared
    # domains, no cross-system collision.
    board_email = f"{board_short_id}.a2a@{domain}"
    board_id = hashlib.sha256(board_email.encode()).hexdigest()[:20]
    _register_board_gateway(board_id, gateway_url)
    if token_match:
        token = token_match.group(1).rstrip()
        _store_board_credential(board_id, gateway_url, token)

# ═══════════════════════════════════════════════════════════════
# 生命周期公共链（注册/注销 agent 地址——跨 agent 系统统一，
# Hermes 适配层与 OpenClaw 注册脚本共用，修订只改此处）
# ═══════════════════════════════════════════════════════════════

def email_for_agent(agent_id: str, domain: str, system_name: str = "",
                    default_aliases: tuple = ("default",)) -> str:
    """agent 地址派生（跨系统统一规则 + 注册前合规清洗）。

    1. 默认名归一：**各系统自己的默认 agent 名** → "agent"
       （Hermes 传 ("default",)，OpenClaw 传 ("main",)；互不替换——Hermes 的
       "main" profile 保持 "main"，OpenClaw 的 "default" agent 保持 "default"）。
    2. 非法字符清洗（作用于**原始地址名** base 段，不含共享域 system_name 标识名）：
       - '.' → '_' **全系统严格统一**（点是 persona 分隔符保留位 + gateway 点规则：
         shared 恰 1 点 / non-shared 0 点，base 含点必拒；与 persona 支持无关）
       - 其他非 atext-no-dot 字符 → '_'（字符集 = gateway is_atext_no_dot）
       - 清洗后为空 → 回退 "agent"
    """
    base = "agent" if agent_id in default_aliases else agent_id
    # 严格清洗：非 atext-no-dot 字符（含 '.'）→ '_'；空结果回退 "agent"
    cleaned = re.sub(r"[^A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]", "_", base)
    base = cleaned or "agent"
    if system_name:
        return f"{base}.{system_name}@{domain}"
    return f"{base}@{domain}"


def trigger_profile_hooks(event: str, profile_name: str, profile_dir: str) -> None:
    """Lazy forward to the hermes adapter (patch entry point in host
    profiles.py keeps importing aimail_base; AUDIT-1 P1-1 — the real
    implementation lives in aimail_hermes, module-level import here would
    create a cycle)."""
    try:
        from aimail.hermes import aimail_hermes as _ah  # pip form
    except ImportError:
        import importlib
        _ah = importlib.import_module("aimail_hermes")  # repo form
    return _ah.trigger_profile_hooks(event, profile_name, profile_dir)


def register_bridge_route(system_id: str, email: str, gw: dict,
                          local_webhook_url: str) -> dict:
    """注册后向本机 bridge POST 入站 hook 路由(email → 本地接收端点全 URL)。

    铁律(2026-08-18 用户强调):有 bridge 时,每个 agent 创建注册地址后
    必须注册路由——否则 bridge 拉取到邮件后不知转发到哪,入站断链。
    幂等(bridge 路由表 upsert)。bridge admin API: POST /api/v1/routes
    {email, host, port} —— host 传完整 URL(含路径)。admin 端口取
    aimail_gateway.json 的 bridge_admin_port(默认 38081)。
    """
    import urllib.request
    admin_port = int(gw.get("bridge_admin_port", 38081)) if isinstance(gw, dict) else 38081
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{admin_port}/api/v1/routes",
            # port 占位(host 为全 URL 时被 bridge 忽略;0 会被参数校验拒绝,用 80 对齐 CLI)
            data=json.dumps({"email": email, "host": local_webhook_url, "port": 80}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode() or "{}")
    except Exception as e:
        return {"error": str(e)}


def resolve_register_webhook_url(gw: dict, local_webhook_url: str) -> str:
    """webhook_host 三态 → 地址注册参数 webhook_url(2026-08-18 用户定稿语义):

    - webhook_host 有合法 IP:port → 有 bridge,push 模式 → 注册参数 =
      webhook_host(bridge 公网入口,云端直推)
    - webhook_host 显式空值("") → 有 bridge,pull 模式 → 注册参数 = 空
      (云端不回调;bridge 按空值走 pull 拉取,与 amail_bridge toml 语义一致)
    - webhook_host 配置项不存在 → 无 bridge → 注册参数 = local_webhook_url
      (agentmail.json 的本地接收端点,云端直推本地)

    注意:注册参数与 agentmail.json 的 webhook_url 是两个值——agentmail.json
    webhook_url 始终 = 本地接收端点(bridge 路由目标,唯一信任源)。
    """
    whh = gw.get("webhook_host") if isinstance(gw, dict) else None
    if whh is not None and str(whh).strip():
        return str(whh)          # push:bridge 公网入口
    if whh is not None:
        return ""                # pull:显式空值
    return local_webhook_url     # 无 bridge:本地端点


def register_agent_email(client, system_id: str, email: str,
                         webhook_url: str = "", webhook_secret: str = "",
                         manager_address: str = "") -> dict:
    """注册链（幂等，4 步）：register_email(generate_code) → 已存在更新 webhook →
    manager 白名单 → activate_address。返回 {"api_key", "activation_code"}
    （api_key 为空 = 激活 pending/已存在；activation_code 供延迟激活语义）。
    域由 gateway 从 email 地址自行提取（2026-08-18 起不再传 mx_domain/domain 参数）。

    client 须提供：register_email / list_system_domains / update_system_domain /
    activate_address（aimail_tools._GatewayClient 全具备；白名单由网关注册接口自动创建）。
    """
    result = client.register_email(
        system_id=system_id, email=email,
        webhook_url=webhook_url, webhook_secret=webhook_secret,
        manager_address=manager_address, generate_code=True,
    )
    activation_code = ""
    if isinstance(result, dict):
        activation_code = result.get("activation_code", "") or ""
        status = result.get("status", "")
        if status and str(status) not in ("created", "200", "201", 200, 201):
            msg = str(result.get("error", "")) + str(result.get("detail", ""))
            if "already exists" in msg.lower() or "exists" in msg.lower():
                activation_code = ""
                # 已存在 → 更新 webhook 配置（幂等）
                try:
                    domains = client.list_system_domains(system_id)
                    for d in (domains if isinstance(domains, list) else []):
                        if isinstance(d, dict) and d.get("domain") == email:
                            client.update_system_domain(str(d.get("id", "")),
                                                        webhook_url, webhook_secret)
                            break
                except Exception:
                    pass
            else:
                raise RuntimeError(f"register failed: {result}")

    api_key = ""
    if activation_code:
        act = client.activate_address(activation_code, email_address=email)
        if act.get("success") and act.get("raw_key"):
            api_key = act["raw_key"]

    # ── 5. Bridge route pairing ──
    # Callers push the local bridge route table explicitly via
    # register_bridge_route(system_id, email, gw, local_webhook_url) —
    # consolidated single implementation (AUDIT-1 P2-5; the inline
    # hardcoded-38081 duplicate used to double-write the route).
    return {"api_key": api_key, "activation_code": activation_code}


def deregister_agent_email(client, system_id: str, email: str,
                           manager_address: str = "") -> dict:
    """注销链（API 部分，幂等）：api-key → domain → whitelist。
    返回各步状态 {api_key, domain, whitelist}。

    client 须提供：get_api_key_by_email / delete_api_key / list_system_domains /
    delete_whitelist_by_value。
    """
    out: dict = {}
    # 1. 删 API key（按 email 查 id）
    try:
        k = client.get_api_key_by_email(email)
        if isinstance(k, dict) and k.get("id"):
            r = client.delete_api_key(k["id"])
            out["api_key"] = str(r.get("status", r))
        else:
            out["api_key"] = "not_found"
    except Exception as e:
        out["api_key"] = f"err:{e}"

    # 2. 删 domain entry（按 id，回退按名）
    try:
        domains = client.list_system_domains(system_id)
        addr_id = ""
        for d in (domains if isinstance(domains, list) else []):
            if isinstance(d, dict) and d.get("domain") == email:
                addr_id = str(d.get("id", ""))
                break
        if addr_id:
            r = client._request("DELETE", f"/api/v1/admin/system-domains/{addr_id}")
            out["domain"] = str(r.get("status", r))
        else:
            r = client._request("DELETE", f"/api/v1/admin/system-domains/{email}")
            out["domain"] = str(r.get("status", r))
    except Exception as e:
        out["domain"] = f"err:{e}"

    # 3. 白名单清理（按值删）
    try:
        if manager_address and hasattr(client, "delete_whitelist_by_value"):
            client.delete_whitelist_by_value(email, manager_address)
        out["whitelist"] = "attempted"
    except Exception as e:
        out["whitelist"] = f"err:{e}"

    return out



# ═══════════════════════════════════════════════════════════════
# System ensure (CLI reverse-call ABI) — pysdk parity with tssdk
# ensureSystem (tssdk/packages/mail-core/src/ensure-system.ts)
# ═══════════════════════════════════════════════════════════════

def _system_home_owned(system_home: str) -> str:
    """Local ownership probe (pure file reads — NOT the activation protocol,
    which lives once in the CLI): system_home -> owning sid (UNIQUE match
    only; ambiguous -> '' so the CLI's authoritative decision is consulted)."""
    if not system_home:
        return ""
    target = str(Path(system_home).expanduser()).rstrip("/")
    base = aimail_home() / "systems"
    found = ""
    if not base.is_dir():
        return ""
    for d in sorted(base.iterdir()):
        if not (d.is_dir() and (d / "aimail_gateway.json").is_file()):
            continue
        try:
            cfg = json.loads((d / "aimail_gateway.json").read_text())
        except Exception:
            continue
        sh = str(cfg.get("system_home", "") or "").rstrip("/")
        if sh and sh == target:
            if found:
                return ""  # second owner -> ambiguous, don't guess
            found = d.name
    return found


def ensure_system(system_home: str = "", cli: str = "aimail",
                  timeout: int = 60) -> dict:
    """Ensure a system exists for this host — REVERSE-CALL to the CLI's
    L1-only `aimail ensure-system` (the single activation implementation;
    SDKs never carry the protocol). Parity with tssdk ensureSystem.

    Ownership short-circuit: with a system_home, only an OWNING system
    (cfg.system_home matches) short-circuits — on multi-platform machines
    another platform's system must NOT block this one's activation. Without
    a home, any local system short-circuits.

    Returns {ok, system_id?, activated?, error?, hint?}. Never raises.
    """
    import subprocess as _sp
    # 1) ownership short-circuit (pure local reads)
    owned = _system_home_owned(system_home) if system_home else ""
    if owned:
        return {"ok": True, "system_id": owned, "activated": False}
    if not system_home:
        base = aimail_home() / "systems"
        if base.is_dir():
            sids = [d.name for d in sorted(base.iterdir())
                    if d.is_dir() and (d / "aimail_gateway.json").is_file()]
            if sids:
                sid = sids[0] if len(sids) == 1 else ""
                return {"ok": True, "system_id": sid, "activated": False}

    # 2) reverse-call the CLI L1 ABI (single activation implementation)
    argv = [cli, "ensure-system"]
    if system_home:
        argv += ["-H", system_home]
    try:
        out = _sp.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False,
                "error": f"aimail CLI not found ('{cli}') — run bootstrap first",
                "hint": "run the aimail bootstrap first, then retry"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    try:
        parsed = json.loads(out.stdout.strip() or "{}")
    except Exception:
        return {"ok": False,
                "error": f"aimail ensure-system returned unparsable output (exit {out.returncode})",
                "hint": "run `aimail install --home <root>` manually to see the error"}
    if parsed.get("success") is not True or out.returncode != 0:
        r = {"ok": False, "error": str(parsed.get("error") or f"ensure-system failed (exit {out.returncode})")}
        if parsed.get("hint"):
            r["hint"] = str(parsed["hint"])
        return r
    return {"ok": True,
            "system_id": str(parsed.get("system_id", "")),
            "activated": parsed.get("path") == "activation"}

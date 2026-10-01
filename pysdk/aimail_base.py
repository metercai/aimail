"""aimail_base — Runtime: preprocessor, hooks, profile, templates."""
from __future__ import annotations
import json
import logging
import os
import re
import secrets
import time
import hmac
import hashlib
import threading
import urllib.error
import urllib.request
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


# ═══════════════════════════════════════════════════════════
# prompt_rules: 识别条件 ↔ 角色文件的可定制关系(裁决 2026-09-23)
#   - 字段: subject/body/sender/recipient;每字段"包含"匹配(大小写不敏感),
#     字段内多关键字=或, 字段间=且(裁决①)
#   - name = {序号}_{filename}, 序号 10..99(裁决②);实际加载看 rule["file"]
#     (CLI 落盘时显式写出;读侧不做字符串推导)
#   - 存储: agentmail.json 的 prompt_rules 键(仅该 agent;裁决④键名)
# 单一真源: CLI `aimail prompt test` 干跑也调这里的函数。
# ═══════════════════════════════════════════════════════════
PROMPT_RULE_NAME_RE = re.compile(r"^[1-9][0-9]_[a-z0-9_-]{1,64}$")


def prompt_rule_name_ok(name: str) -> bool:
    """name = {10..99 序号}_{filename};filename 段 1..64 位(裁决②+CLI 校验)。"""
    return bool(PROMPT_RULE_NAME_RE.fullmatch(name or ""))


def read_prompt_rules() -> list:
    """读 agent 级 prompt_rules(agentmail.json 经适配层配置加载器注入)。

    读到的每一项都过合法性筛(坏项丢弃 + WARN —— 送达链路绝不因规则坏而失败),
    返回按 name 字母序(= 序号数值序, 前缀等宽)排列的规则表。
    """
    cfg = _load_profile_config()
    if not cfg:
        return []
    raw = cfg.get("prompt_rules")
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("[aimail_gateway] prompt_rules is not a list — ignored")
        return []
    kept = []
    for r in raw:
        if not isinstance(r, dict):
            logger.warning("[aimail_gateway] prompt rule skipped (not an object): %r", r)
            continue
        name = str(r.get("name", ""))
        if not prompt_rule_name_ok(name):
            logger.warning("[aimail_gateway] prompt rule skipped (bad name: %r)", r.get("name"))
            continue
        if r.get("file") is None or not str(r.get("file")).strip():
            logger.warning("[aimail_gateway] prompt rule %s skipped (missing 'file')", name)
            continue
        if r.get("enabled") is False:
            continue
        if not _prompt_rule_has_any_field(r):
            logger.warning("[aimail_gateway] prompt rule %s skipped (no non-empty field)", name)
            continue
        kept.append(r)
    return sorted(kept, key=lambda r: str(r.get("name", "")))


def _prompt_rule_has_any_field(rule: dict) -> bool:
    for key in ("subject", "body", "sender", "recipient"):
        kws = rule.get(key)
        if kws is None:
            continue
        if isinstance(kws, str):
            kws = [kws]
        if isinstance(kws, list) and any(str(k).strip() for k in kws):
            return True
    return False


def prompt_rule_matches(rule: dict, subject: str, body: str,
                        sender: str, recipient: str) -> bool:
    """字段内或 / 字段间且(裁决①)。缺席字段不参与;空列表=未给;坏类型=不命中。"""
    hay = {
        "subject": (subject or "").lower(),
        "body": (body or "").lower(),
        "sender": (sender or "").lower(),
        "recipient": (recipient or "").lower(),
    }
    for key in ("subject", "body", "sender", "recipient"):
        if key not in rule:
            continue
        kws = rule[key]
        if isinstance(kws, str):
            kws = [kws]
        if not isinstance(kws, list):
            return False
        items = [str(k).strip().lower() for k in kws if str(k).strip()]
        if not items:
            continue
        if not any(kw in hay[key] for kw in items):
            return False
    return True


def _recipients_text(result: dict) -> str:
    rec = result.get("recipients") or {}
    if isinstance(rec, dict):
        parts = list(rec.get("to") or []) + list(rec.get("cc") or [])
        return " ".join(str(p) for p in parts)
    return str(rec)


def build_ctx(payload: dict, headers: dict) -> dict:
    """Build template context dict from available data."""
    return {
        "AGENTMAIL_ADDRESS": payload.get("my_aimail_addr", ""),
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
    falling back to ~/.aimail. All path constructors (三层收口 2026-09-23:
    systems/{sid}/{addr}/agentmail.log、systems/{sid}/{addr}/mail、
    systems/{sid}/.system_raw_key.key、bridge/) derive from this so the env var relocates the whole
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
    binary name)."""
    return _aimail_system_dir(system_id) / "aimail_gateway.json"


def _load_gateway_config(system_id: str = "") -> Optional[dict]:
    """load gateway connection config

    Reads from (in priority order):
    1. Environment variables (AIMAIL_GATEWAY_URL + AIMAIL_ADMIN_KEY/AIMAIL_PRODUCT_CODE)
    2. ~/.aimail/systems/{system_id}/aimail_gateway.json (direct, or via the platform adapter's profile-dir resolver -> .agentmail pointer)

    Returns None when nothing is resolvable (unknown system_id and no pointer, or
    an unusable config file) — callers degrade instead of aborting: inbound mail
    is still delivered, only gateway-dependent enrichment (contact profiles,
    attachment download) is skipped. Misconfiguration surfaces at adapter
    registration through `check_profile_pointer()`.
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
            # 不是"错误"而是"本机还没装/适配层未注入指针": 调用方(入站预处理、
            # 平台钩子)一律按"无网关"降级 —— B1 联系画像与附件下载跳过,邮件本身
            # 照常投递给 agent。这里只留一条可定位的 warn;把配置问题顶到启动时的
            # 自检见 check_profile_pointer()。
            logger.warning(
                "[aimail_gateway] gateway config unresolvable: no system_id and no "
                "platform .agentmail pointer (profile dir %r) — run `aimail install` "
                "or inject _PROFILE_DIR_RESOLVER; gateway-dependent enrichment is skipped",
                _PROFILE_DIR_RESOLVER() if _PROFILE_DIR_RESOLVER else None,
            )
            return None

    gw_path = _gateway_config_path(resolved_sid)
    if gw_path.is_file():
        try:
            cfg = json.loads(gw_path.read_text())
            # 审计 D3: agent 自助激活写出的网关文件是 agent 作用域(scope=agent,
            # 不含管理 key) —— 它同样"可用", 判定不能只看 admin_key/product_code。
            if cfg.get("gateway_url") and (
                cfg.get("admin_key") or cfg.get("product_code") or cfg.get("scope") == "agent"
            ):
                return cfg
        except Exception:
            pass

    return None


# ── 注入点（适配层设置；Hermes → pysdk/hermes/aimail_hermes.py，
#             DeerFlow → pysdk/deer-flow/aimail_deerflow.py）────────────
# 平台差异（config 来源/personas/profile 目录/board 登记）由适配层注入，
# 公共核心保持平台无关。未注入时使用安全默认（None/空/no-op）。
_CONFIG_LOADER = None          # () -> Optional[dict]      agent 配置加载
_PERSONAS_PROVIDER = None      # () -> dict                personas 配置
_PROFILE_DIR_RESOLVER = None   # () -> Optional[str]       profile 目录（gateway config 定位）
_SOUL_PROVIDER = None          # () -> str                 SOUL 内容（board ctx）
_SKILLS_PROVIDER = None        # () -> list[str]           skills 列表（board ctx）
_BOARD_GATEWAY_SINK = None     # (board_id, gateway_url) -> None


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


def check_profile_pointer() -> bool:
    """适配层注册注入点后自检：profile 目录里的 .agentmail 指针能否解析出 system_id。

    指针是平台契约的一部分（preprocess 的 B1 步骤无参调用 gateway 配置解析，
    只能靠它定位 system_id）。返回 False 不代表故障，而是"本机尚未安装/未注入"：
    此时入站预处理降级（B1 联系画像与附件下载跳过，**邮件照常投递给 agent**，
    不会再整条失败），本函数同时留一条 warn 让降级可见，并把配置问题顶到
    适配层启动而不是每封邮件。
    """
    resolver = _PROFILE_DIR_RESOLVER
    profile_dir = None
    if resolver is not None:
        try:
            profile_dir = resolver()
        except Exception:  # noqa: BLE001 — 自检本身绝不抛
            profile_dir = None
    ok = False
    if profile_dir:
        pointer = Path(profile_dir) / ".agentmail"
        if pointer.is_file():
            try:
                ok = bool(json.loads(pointer.read_text()).get("system_id"))
            except Exception:  # noqa: BLE001 — 指针损坏等同未安装
                ok = False
    if not ok:
        logger.warning(
            "[aimail_gateway] no resolvable .agentmail pointer (profile dir %r) — "
            "aimail is not installed for this profile; inbound preprocessing degrades "
            "(no contact profiles / attachment download) until `aimail install` runs",
            profile_dir,
        )
    return ok


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
# 兜底 MCP/CLI 与各平台适配层共用同一实现(单一权威):任何 agent 系统
# 只需按共享布局落 agentmail.json 即可复用。
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


def save_agent_config(agent_id: str, cfg: dict, system_id: str) -> Path:
    """原子写地址键 agentmail.json(共享布局,平台无关)——Python 侧唯一
    共享落盘实现,对齐 TS mail-core config.ts saveBinding(tmp+rename+0600)。

    hermes/deer-flow 注册链统一经此落盘;agent_id 非空时写入 cfg
    (load_agent_config/set_agent_context 按 agent_id 匹配)。
    """
    cfg = dict(cfg)
    if agent_id:
        cfg["agent_id"] = agent_id
    cleaned = _clean_agent_dir_name(cfg.get("email", ""))
    p = aimail_home() / "systems" / str(system_id) / cleaned / "agentmail.json"
    # 地址目录 0o700:凭证(api_key)所在目录,组/其他不可进
    p.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    # tmp 以 0600 创建:先写后 chmod 的写法存在短暂全局可读窗口(含 api_key)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(p)
    return p


# ── CLI 侧的语义化薄入口(owner 裁决 A, 2026-09-28)────────────────────────────
# 分层裁决: 系统级环境文件由 **CLI** 写, per-agent 绑定文件由 **SDK** 写。
# CLI 需要改绑定内容时不再自持写调用, 只调下面这几个语义化薄函数; 三者内部一律走
# 上面既有的 `save_agent_config`(原子 tmp+rename + 0600 语义逐字不变, 未复制实现)。
# 写作约定: 这几个函数的文档串里**不出现**绑定文件名字面量 —— 契约字面量棘轮按
# 「文件 × 键」只许减不许增, 本模块已是该键的既有持有者, 不能因新代码再加。

def update_binding(system_id: str, cfg: dict, updates: dict) -> Path:
    """字段级更新绑定内容并原子落盘(语义化薄函数: updates 并入 → `save_agent_config`)。

    CLI 侧只做触发与取值(`aimail prompt add|rm` 改 prompt_rules、`aimail address
    set-manager` 改 manager_address); 判定与落盘在 SDK 侧。
    """
    merged = dict(cfg)
    merged.update(updates or {})
    return save_agent_config(merged.get("agent_id", ""), merged, system_id)


def backfill_binding(cfg: dict, system_id: str) -> Path:
    """把**已经算好的整份**绑定内容落盘(`aimail repair` 的回填/对齐入口)。

    repair 仍是判定者(决定哪些字段要补、webhook 目标是否要对齐), 这里只负责经
    `save_agent_config` 写回 —— 与 CLI 自持写调用的旧形态相对。
    """
    return save_agent_config(cfg.get("agent_id", ""), cfg, system_id)


def rename_binding(old_dir, new_email: str, cfg: dict, system_id: str) -> dict:
    """地址改名时的本地绑定迁移 —— **目录 + 内容 = 同一件事**。

    绑定按地址目录存放, 所以「搬目录」与「写内容」拆开必留中间态(目录已改名、
    内容未落盘); 两者都在这里完成, 内容仍走 `save_agent_config`(原子+0600)。
    `old_dir` 必须是调用方已定位到的**实际**绑定目录(容忍遗留命名), 不做二次推导。

    返回 ``{"path": Path, "dir": Path, "moved": bool, "merged": bool}`` ——
    ``merged=True`` 表示目标目录已存在(只把内容并过去、不动目录), 由调用方告警。
    """
    root = aimail_home() / "systems" / str(system_id)
    src = Path(old_dir)
    dst = root / _clean_agent_dir_name(new_email or "")
    moved = False
    merged = False
    if dst != src:
        if dst.exists():
            merged = True
        elif src.is_dir():
            src.rename(dst)
            moved = True
    path = save_agent_config(cfg.get("agent_id", ""), cfg, system_id)
    return {"path": path, "dir": dst, "moved": moved, "merged": merged}


def _ensure_private_dir(d: Path, mode: Optional[int]) -> None:
    """私有目录: 不存在则按 mode 建; 已存在但过宽则收紧(仅本 SDK 管理的目录)。"""
    d.mkdir(parents=True, mode=mode or 0o700, exist_ok=True)
    if mode is None:
        return
    try:
        if d.stat().st_mode & 0o077:
            os.chmod(d, mode)
    except OSError:
        pass


def atomic_write_private(path: Path, text: str, ensure_dir_mode: Optional[int] = 0o700) -> None:
    """私有内容原子落盘 —— 单一入口(审计 2026-09-21: 多处默认 umask 写私有数据)。

    约定: 父目录 0700(除宿主自有目录可传 None) + tmp 以 0600 创建(无"先写后
    chmod"的全局可读窗口) + 原子 replace。凭证/指针/邮件正文/快照/线程摘要
    统一走这里, 不再各自 `tmp.write_text(...)`(默认 umask 会产出 0644)。
    """
    _ensure_private_dir(path.parent, ensure_dir_mode)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    tmp.replace(path)


def append_private(path: Path, text: str, ensure_dir_mode: Optional[int] = 0o700) -> None:
    """私有日志追加(日志不能原子替换): 目录收紧 + 文件按 0600 创建。"""
    _ensure_private_dir(path.parent, ensure_dir_mode)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(text)


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
    """把富化后的 AIMail payload 组装成 agent 输入 message。

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
    """写 {dir}/.aimail 指针（系统身份唯一来源）。

    审计 D2: 原实现用默认 umask(实测 0664 文件 / 0775 目录)+ 非原子写 ⇒
    统一走 atomic_write_private(目录 0700、tmp 0600、原子替换)。
    """
    atomic_write_private(
        pointer,
        json.dumps({"system_id": system_id, "email": email}, indent=2, ensure_ascii=False) + "\n",
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
        # token 与 api_key 同级敏感: 目录 0700 + tmp 0600 + 原子替换(审计 D2:
        # 原 mkdir 无 mode, 目录一旦本函数先建就比 0700 宽)。
        atomic_write_private(creds_path, json.dumps(creds, indent=2))
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
# "__aimail_pong__:" (redirects pong to inbound instead of outbound SMTP).
# Agent-side PONG_PREFIX MUST equal that exact string — otherwise the
# pong goes out as a normal outbound email and never loops back to the
# agent preprocess chain. PING_PREFIX is agent-side only (ping enters
# via SMTP as a normal inbound mail; no gateway-side ping matching).
PING_PREFIX = "__aimail_ping__:"
PONG_PREFIX = "__aimail_pong__:"


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
    gateway's P0 interception (send.rs matches __aimail_pong__:) redirects
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


def resolve_system_id_for_email(email: str = "") -> str:
    """email → system_id(三层收口布局 systems/{sid}/{addr}/… 的归属解析, 2026-09-23)。

    顺序: 1) 活动 profile 配置(email 匹配时, 零扫描)
          2) 扫 {home}/systems/*/{cleaned}/agentmail.json 落点(权威映射,
             覆盖任意地址, 如 ping 收件人)
          3) 平台指针 .agentmail 的 system_id 兜底
    解析失败返回 "" —— 调用方收口到 systems/_unassigned/, 保持三层布局纯净
    (绝不回落旧的顶层 logs/ 或 mail/)。
    """
    if email:
        try:
            cfg = _load_profile_config()
            if (cfg and cfg.get("system_id") and
                    str(cfg.get("email", "")).strip().lower() == email.strip().lower()):
                return str(cfg["system_id"])
        except Exception:
            pass
        cleaned = _clean_agent_dir_name(email)
        try:
            root = aimail_home() / "systems"
            if root.is_dir():
                for d in sorted(root.iterdir()):
                    if d.is_dir() and (d / cleaned / "agentmail.json").is_file():
                        return d.name
        except Exception:
            pass
    try:
        resolver = _PROFILE_DIR_RESOLVER
        pdir = resolver() if resolver else ""
        if pdir:
            ptr = Path(pdir) / ".agentmail"
            if ptr.is_file():
                return str(json.loads(ptr.read_text()).get("system_id", ""))
    except Exception:
        pass
    return ""


def aimail_log_path(email: str = "") -> Path:
    """Canonical per-agent processing log path (user-mandated 2026-08-16).

    三层收口(2026-09-23 裁决): 每 agent 日志落 agent 层, 与 agentmail.json
    同目录 —— {home}/systems/{system_id}/{cleaned_addr}/agentmail.log;
    顶层不再有 logs/(原 logs/aimail.{cleaned_addr}.log 已废弃)。
    system_id 解析失败收口到 systems/_unassigned/(不落旧路径)。
    """
    cleaned = _clean_agent_dir_name(email) if email else "default"
    sid = resolve_system_id_for_email(email) or "_unassigned"
    return aimail_home() / "systems" / sid / cleaned / "agentmail.log"


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
    pong_sender = send_pong
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
        persona, profile, sys_name = parse_aimail_persona(email, system_name)
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

    persona, profile, _sys_name = parse_aimail_persona(my_to_addr, system_name) if my_to_addr else ('', '', '')
    if persona:
        if not PERSONA_SUPPORTED:
            # 系统不支持 persona：收件地址归一为基础地址（剥离 persona 前缀），
            # 不做配置校验与派生地址保留——agent 身份即注册的基础地址。
            result["my_aimail_addr"] = agent_email
        else:
            # Validate persona against configured personalities
            configured = list_personas()
            if persona in configured:
                result["my_aimail_addr"] = my_to_addr
            else:
                logger.warning("[aimail_gateway] Persona '%s' not found in agent.personalities — falling back to base address", persona)
                # 未配置 persona：剥离 persona 前缀，回退注册基础地址（与创建端幂等）
                result["my_aimail_addr"] = agent_email
    if not result.get("my_aimail_addr"):
        result["my_aimail_addr"] = my_to_addr or agent_email

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
    my_addr = result.get("my_aimail_addr", "")
    if mid and my_addr:
        from aimail_tools import store_inbound_message, _log_aimail
        store_inbound_message(mid, refs, my_addr, preprocessed_payload=result)
        # Lightweight log entry
        _from = raw_headers.get("from", payload.get("from", ""))
        _subj = (raw_headers.get("subject") or raw_headers.get("Subject")
                 or payload.get("subject") or payload.get("Subject") or "")
        _log_aimail("inbound", str(_from), my_addr, str(_subj))

    # ── a2a_board: [WhoAmI]问询检测 ──
    subject = (payload.get("subject") or "").strip()
    if subject.upper().startswith("[WHOAMI]"):
        ctx = build_ctx(result, dict(headers))
        whoami_raw = _read_role_file("whoami")
        if whoami_raw:
            result["_whoami_prompt"] = fill_template(whoami_raw, ctx)
        return result

    # ── B3: Role_Calibrator (persona 更新请求 / welcome 引导) ──
    # 2026-09-22 合并后两种识别形式:
    #   (a) 兼容旧: 主题含 "update persona"(保留一版; 下一版删除)
    #   (b) 合并后的 welcome: **主题标记 + 正文三标签同时命中**
    #       - 主题(小写化)含 "welcome to aimail world"
    #         (网关生成: "Welcome to AIMail World, {agent}, since {date}!")
    #       - 正文行首出现 persona: / signature: / current_time: 三标签
    # 命中 ⇒ 注入 Role_Calibrator(SOUL + skills 由 build_ctx 自动填充)并早返回, 以免被 board 角色覆盖。
    # 只命中其一 ⇒ 不注入 + WARN, 使模板漂移可见(不静默退化成默认 prompt)。
    _subj = subject.lower()
    _body_lc = (result.get("body") or "").lower()
    _welcome_marker = "welcome to aimail world" in _subj
    _labels_ok = all(
        any(ln.lstrip().startswith(f"{k}:") for ln in _body_lc.splitlines())
        for k in ("persona", "signature", "current_time")
    )
    if "update persona" in _subj or (_welcome_marker and _labels_ok):
        ctx = build_ctx(result, dict(headers))
        calib_raw = _read_role_file("role_calibrator")
        if calib_raw:
            result["_role_prompt"] = fill_template(calib_raw, ctx)
        else:
            logger.warning("[aimail_gateway] Role_Calibrator role file missing — persona update will proceed without a role prompt")
        return result
    if _welcome_marker or _labels_ok:
        logger.warning(
            "[aimail_gateway] role prompt marker mismatch (welcome_marker=%s labels=%s) — default role prompt used",
            _welcome_marker,
            _labels_ok,
        )

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

    # ── L4 网关 header → L5 本地 prompt_rules(裁决 2026-09-23) ──
    # 链序(首中即止, 只识别命中一次): [WHOAMI] > welcome > board >
    # X-AIMail-Prompt(网关判定"已命中"后盖头, 值=文件名不含.md/不带序号)
    # > 本地自定义(name 字母序)。任何一层注入成功即止;命中但文件缺失
    # ⇒ WARN 并继续向下(绝不阻断送达)。
    if not result.get("_role_prompt"):
        ctx = build_ctx(result, dict(headers))
        injected = False
        # header 双源(网关盖头位置未锁死 = 扩展点): 邮件头 payload.headers 优先,
        # HTTP 头参数兜底; key 大小写不敏感, 空值视为未给。
        hdr = ""
        for _src in (raw_headers, headers):
            for hk, hv in (_src or {}).items():
                if str(hk).lower() == "x-aimail-prompt" and str(hv).strip():
                    hdr = str(hv).strip()
                    break
            if hdr:
                break
        if hdr:
            hdr_raw = _read_role_file(hdr)
            if hdr_raw:
                result["_role_prompt"] = fill_template(hdr_raw, ctx)
                injected = True
            else:
                logger.warning(
                    "[aimail_gateway] X-AIMail-Prompt names a missing role file (%s) — falling through to local prompt_rules",
                    hdr)
        if not injected:
            sender_txt = str(result.get("sender") or result.get("from") or "")
            recp_txt = _recipients_text(result)
            for rule in read_prompt_rules():
                if not prompt_rule_matches(rule, str(result.get("subject", "")),
                                           str(result.get("body", "")),
                                           sender_txt, recp_txt):
                    continue  # 首中即止: 未命中继续按 name 序评估下一条
                raw = _read_role_file(str(rule["file"]))
                if not raw:
                    # 命中但文件缺失 ⇒ WARN 续走(批准语义: 不阻断、不注入)
                    logger.warning(
                        "[aimail_gateway] prompt rule %s matched but role file '%s' missing — next rule",
                        rule["name"], rule["file"])
                    continue
                result["_role_prompt"] = fill_template(raw, ctx)
                break

    return result


# ═══════════════════════════════════════════════════════════════
# Profile Hook System
# ═══════════════════════════════════════════════════════════════

_profile_hooks: Dict[str, List[Callable]] = {
    "profile_created": [],
    "profile_deleted": [],
}


# ── Hook: auto-register email on profile creation ──────────────

def parse_aimail_persona(email: str, system_name: str = "") -> tuple:
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
# ── Inbound notification (SDK → CLI, best-effort; ZERO bridge semantics) ──────
# owner 裁决 2026-09-28 (SDK 去桥化): 路由/桥 = **CLI 的环境职责**(cli/bridge_wire.py)。
# SDK 只报告自己真正掌握的两个事实 —— 本宿主服务的每个地址, 入站"在服务"/"已停止":
#     aimail address -a <addr> --inbound-live | --inbound-down
# 契约(与 TS 侧 mail-core/src/inbound-notify.ts 逐条同形):
#   · 二进制解析 AIMAIL_BIN → ~/.aimail/bin/aimail → PATH 的 aimail; **都没有 ⇒ 一行
#     debug 后跳过** —— 无 CLI 的机器 SDK 仍自足(照常绑定/服务/自注册)。
#   · **argv 传参(不经 shell)**、超时 3–5s、非 0/异常 ⇒ **一行日志后继续**: 绝不阻塞
#     宿主、不重试风暴、绝不抛给调用方。
#   · 载荷只有 address(零桥语义: 不提桥、不提端口、不提协议)。

INBOUND_NOTIFY_TIMEOUT = 4.0
INBOUND_STATE_FLAG = {"live": "--inbound-live", "down": "--inbound-down"}


def resolve_aimail_bin() -> str:
    """AIMAIL_BIN → ~/.aimail/bin/aimail → PATH `aimail`; '' = 本机没有 CLI。"""
    env = str(os.environ.get("AIMAIL_BIN") or "").strip()
    if env:
        return env
    try:
        canonical = aimail_home() / "bin" / "aimail"
        if canonical.is_file():
            return str(canonical)
    except OSError:
        pass
    import shutil
    return shutil.which("aimail") or ""


def notify_inbound_state(email: str, state: str,
                         timeout: float = INBOUND_NOTIFY_TIMEOUT, runner=None) -> dict:
    """best-effort 通知环境主控(CLI)某地址的入站状态。**永不抛、永不阻塞调用方**。

    runner 是测试注入点, 签名 ``(bin, args, timeout) -> (rc, detail)``; 缺省用
    subprocess(argv, 无 shell)。返回 ``{"state": "notified"|"no_cli"|"failed",
    "email", "bin", "detail"}``。
    """
    addr = str(email or "").strip()
    flag = INBOUND_STATE_FLAG.get(str(state))
    if not addr or not flag:
        return {"state": "failed", "email": addr, "bin": "",
                "detail": f"bad notification payload (state={state!r})"}
    binary = resolve_aimail_bin()
    if not binary:
        return {"state": "no_cli", "email": addr, "bin": "",
                "detail": "no aimail CLI on this machine"}
    args = ["address", "-a", addr, flag]
    try:
        if runner is not None:
            rc, detail = runner(binary, args, timeout)
        else:
            import subprocess
            proc = subprocess.run(  # noqa: S603 — argv, never a shell
                [binary, *args], timeout=timeout,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            rc, detail = proc.returncode, ("exit %s" % proc.returncode if proc.returncode else "")
        if rc:
            return {"state": "failed", "email": addr, "bin": binary, "rc": rc,
                    "detail": detail or f"exit {rc}"}
        return {"state": "notified", "email": addr, "bin": binary, "rc": 0}
    except Exception as e:  # noqa: BLE001 — 通知是 best-effort, 绝不冒泡
        return {"state": "failed", "email": addr, "bin": binary, "detail": str(e)}


def notify_inbound_for_system(state: str, system_id: str = "",
                              timeout: float = INBOUND_NOTIFY_TIMEOUT,
                              runner=None) -> list:
    """对本系统**每个地址**通知一次入站状态(宿主钩子载荷)。永不抛, 无绑定 ⇒ []。"""
    rows = iter_agentmail_configs(system_id)
    out = []
    for c in rows:
        email = str((c or {}).get("email") or "").strip()
        if not email:
            continue
        out.append(notify_inbound_state(email, state, timeout=timeout, runner=runner))
    return out


def format_inbound_notify_line(outcome: dict, state: str) -> str:
    """One-line English status for an inbound notification outcome."""
    what = "inbound live" if state == "live" else "inbound down"
    who = str((outcome or {}).get("email") or "")
    st = str((outcome or {}).get("state") or "")
    if st == "notified":
        return f"{what} reported for {who} ({(outcome or {}).get('bin', '')})"
    if st == "no_cli":
        return (f"{what} not reported for {who}: no aimail CLI "
                f"({(outcome or {}).get('detail') or 'not found'})")
    return f"{what} not reported for {who}: {(outcome or {}).get('detail') or 'unknown'}"


def inbound_notify_is_warning(outcome: dict) -> bool:
    """True when the outcome deserves a warning line rather than an info line."""
    return str((outcome or {}).get("state")) == "failed"


def inbound_serving(target: str, timeout: float = 1.5) -> bool:
    """Is the local receive endpoint reachable? A remote endpoint is unprobeable locally."""
    t = (target or "").strip()
    if not t:
        return False
    try:
        from urllib.parse import urlparse
        u = urlparse(t)
        host = (u.hostname or "").strip("[]")
        port = u.port or (443 if u.scheme == "https" else 80)
    except Exception:
        return False
    if host not in ("127.0.0.1", "localhost", "::1"):
        return False
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def iter_agentmail_configs(system_id: str = "") -> list:
    """Every agentmail.json binding under systems/<sid>/ (all systems when sid is empty).

    Unreadable/partial files are skipped, never fatal (per-file tolerance).
    """
    base = aimail_home() / "systems"
    if system_id:
        roots = [base / system_id]
    else:
        try:
            roots = sorted(p for p in base.iterdir() if p.is_dir())
        except Exception:
            roots = []
    out: list = []
    for root in roots:
        try:
            subs = sorted(p for p in root.iterdir() if p.is_dir())
        except Exception:
            continue
        for sub in subs:
            p = sub / "agentmail.json"
            try:
                if not p.is_file():
                    continue
                d = json.loads(p.read_text())
            except Exception:
                continue
            if isinstance(d, dict) and d.get("email"):
                d.setdefault("_config_path", str(p))
                out.append(d)
    return out


# ═══════════════════════════════════════════════════════════════
# agent-scope 定时轮询入口(pull-entry)—— 适配器 install/初始化收尾接的最后一根线
# ═══════════════════════════════════════════════════════════════
# 背景: 地址级激活码兑换来的 agent-scope key **取不到 push** —— 网关给该地址写的
# 是 `webhook_url = NULL`, 只能自己定时 pull。库能力(pull_list/pull_ack/
# start_polling, aimail_tools)早已存在却**零调用方** ⇒ "agent 自主接入"在产线上
# 缺最后一跳。本段是那根线, 两个适配器(hermes / deer-flow)共用同一实现, 语义与
# TS mail-core `src/poll-entry.ts` 逐条对齐:
#
#   - **只有 agent 级激活产物启用**: 绑定 system_id 以 `shared_addr_` 开头。
#     这是网关侧现成事实(只有该宿主系统下的激活码可被兑换 —— advanced
#     `src/advanced/api/address.rs`: `if !sid.starts_with("shared_addr_")
#     { return Ok(Err(ActErr::Invalid)); }`), 平台注册/产品创建路径**永不**产生
#     该前缀 ⇒ 系统/bridge 绑定(走 push)不会被误判。
#   - **失败不 ack / 去重**: 由 start_polling 保证(on_email 抛错 ⇒ 不 ack +
#     下轮重取; 去重键 = delivery id)。
#   - **可关停 / 可配间隔**: 一条绑定一条循环, stop() 幂等; 间隔/批量/总开关走
#     env(名称与 TS 同名: AIMAIL_PULL / AIMAIL_PULL_INTERVAL_MS / AIMAIL_PULL_LIMIT)
#     或显式 override; 线程 daemon ⇒ 进程退出即停(不拖住退出)。
#   - **pull 与 push 共用同一条入站链**: 投递按 push 的原样请求打回宿主自己的入站
#     端点(同一路径 + 同一 HMAC 头)⇒ 富化/persona/附件/6 步回信协议全部生效。
#   - **本机 secret 自供**(2026-09-27 第 7 缺陷修 A): "重放"要 (url, secret) 成对
#     —— url 缺了有回退(宿主按契约常量拼), secret 原先**没有**回退 ⇒ 地址级激活
#     落的绑定(不含 webhook 两字段)一律 `no-secret` 跳过, pull 循环根本不启动。
#     现在两条腿都有: ①地址级激活落盘即带本机 secret
#     (aimail_tools.activate_address_code_persist); ②升级前落的老绑定在本入口
#     **就地自供**(``ensure_binding_webhook_secret``, 幂等: 已有 ⇒ 不覆盖)。
#     语义边界: 该 secret 只用于"agent 把 pull 到的信重放给**自己**的本机入站端点"
#     这本地一跳的签名, 云端既不下发也不需要知晓 ⇒ **不请求网关、不改云端契约**。

#: 地址级激活产物的系统 id 前缀(网关侧事实, 见上)。
AGENT_SCOPE_SYSTEM_PREFIX = "shared_addr_"

#: 默认轮询间隔(30s)/ 单轮批量(网关上限 200, pull_list 自行收敛)。
DEFAULT_PULL_INTERVAL_MS = 30_000
DEFAULT_PULL_LIMIT = 20

#: 间隔下限 1s(更密就是打网关, 没有业务理由)/ 上限 24h。
MIN_PULL_INTERVAL_MS = 1_000
MAX_PULL_INTERVAL_MS = 24 * 3600_000

#: 总开关/间隔/批量环境变量(与 TS poll-entry.ts 同名, 便于文档统一)。
PULL_ENABLE_ENV = "AIMAIL_PULL"
PULL_INTERVAL_ENV = "AIMAIL_PULL_INTERVAL_MS"
PULL_LIMIT_ENV = "AIMAIL_PULL_LIMIT"


def _env_flag_off(raw) -> bool:
    """总开关判定: 0/false/off/no(大小写不敏感)⇒ 关。缺省 = 开。"""
    if raw is None:
        return False
    return str(raw).strip().lower() in ("0", "false", "off", "no")


def _clamp_int(raw, default: int, lo: int, hi: int) -> int:
    """数值钳制(与 TS clampInt 同序: 非法/非正 ⇒ default; 否则 trunc 后钳 [lo, hi])。"""
    try:
        if raw is None or isinstance(raw, bool):
            raise ValueError(raw)
        n = float(str(raw).strip()) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError):
        return int(default)
    if n != n or n <= 0:  # NaN / 非正数 ⇒ 缺省
        return int(default)
    return int(max(lo, min(int(n), hi)))


def is_agent_scope_binding(cfg) -> bool:
    """该绑定是不是地址级激活(agent scope)产物 —— 用现成事实判定, 不猜。"""
    if not isinstance(cfg, dict):
        return False
    sid = str(cfg.get("system_id") or "")
    if not sid or not str(cfg.get("api_key") or ""):
        return False
    return sid.startswith(AGENT_SCOPE_SYSTEM_PREFIX)


def resolve_agent_pull_settings(cfg, env=None, overrides=None) -> dict:
    """解析一条绑定"要不要 poll / 怎么 poll"。

    优先级: 显式 override > env > 默认值。``enabled`` 对 agent-scope 绑定默认为
    真(那是它**唯一**的入站路径), 且**没有** agent-scope 绑定时永不为真。
    reason ∈ {enabled, no-binding, not-agent-scope, disabled-by-config}(日志与
    单测按它断言, 与 TS PullDecisionReason 同集合)。
    """
    env = os.environ if env is None else env
    ov = overrides or {}
    interval_ms = _clamp_int(
        ov.get("interval_ms", env.get(PULL_INTERVAL_ENV)),
        DEFAULT_PULL_INTERVAL_MS, MIN_PULL_INTERVAL_MS, MAX_PULL_INTERVAL_MS)
    limit = _clamp_int(
        ov.get("limit", env.get(PULL_LIMIT_ENV)), DEFAULT_PULL_LIMIT, 1, 200)
    out = {"enabled": False, "reason": "no-binding",
           "interval_ms": interval_ms, "interval": interval_ms / 1000.0,
           "limit": limit}
    if not cfg:
        return out
    if not is_agent_scope_binding(cfg):
        out["reason"] = "not-agent-scope"
        return out
    if ov.get("enabled") is None:
        off = _env_flag_off(env.get(PULL_ENABLE_ENV))
    else:
        off = ov.get("enabled") is False
    if off:
        out["reason"] = "disabled-by-config"
        return out
    out["enabled"] = True
    out["reason"] = "enabled"
    return out


def list_agent_scope_bindings(system_id: str = "") -> list:
    """所有 agent-scope 绑定(system_id 空 = 全部系统; 与 TS 的 listAgentConfigs 同义)。"""
    return [c for c in iter_agentmail_configs(system_id) if is_agent_scope_binding(c)]


def resolve_inbound_replay_target(cfg, default_url: str = "",
                                  route_secret: str = "",
                                  prefer_route_secret: bool = False) -> dict:
    """解析"把这封 pull 到的投递打回宿主入站端点"所需的 (url, secret)。

    - url: 绑定自身的 ``webhook_url``(注册链落盘的本地接收端点 = 唯一信任源)优先;
      缺失 ⇒ ``default_url``(宿主当前入站端点, 由适配器按契约常量拼出)。
    - secret: 绑定自身的 ``webhook_secret``(注册链与 webhook 路由同源)优先,
      回退 ``route_secret``(宿主路由表里的同名 secret)。
    - ``prefer_route_secret=True``: 宿主**进程外验签**的平台(hermes 的入站由宿主
      webhook 平台按 ``webhook_subscriptions.json`` 里的 secret 验)以**路由表**为准
      —— 它才是 live 验签真值, 绑定里的同名字段只是注册期副本(二者同源时无差异)。
      不这么做的后果: 地址级激活给绑定自供了一个新 secret 而宿主路由还是旧值 ⇒
      重放签名与验签方不一致 ⇒ 401 且永不 ack(邮件卡在网关)。deer-flow 相反:
      入站处理函数**自己读绑定**验签(aimail_inbound.py)⇒ 绑定为真值, 保持默认。

    返回 ``{"url", "secret", "ok", "reason"}``; ``ok=False`` 时 reason ∈
    {no-url, no-secret} —— 调用方据此**不启动**该绑定(不假装能投递)。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    url = str(cfg.get("webhook_url") or "").strip() or str(default_url or "").strip()
    binding_secret = str(cfg.get("webhook_secret") or "").strip()
    live_route_secret = str(route_secret or "").strip()
    if prefer_route_secret and live_route_secret:
        secret = live_route_secret
    else:
        secret = binding_secret or live_route_secret
    if not url:
        return {"url": "", "secret": secret, "ok": False, "reason": "no-url"}
    if not secret:
        return {"url": url, "secret": "", "ok": False, "reason": "no-secret"}
    return {"url": url, "secret": secret, "ok": True, "reason": ""}


# ── 本机 inbound secret 自供(地址级激活 + 老绑定)─────────────────────────
#: 生成用的字节数 —— 与系统级安装链**同一机制/同一格式**:
#: hermes ``aimail_hermes.py`` 与 deer-flow ``manage.py`` 都是
#: ``secrets.token_hex(32)``(⇒ 64 位十六进制)。这里只把"生成"收敛成一个入口,
#: 不新造机制。
WEBHOOK_SECRET_HEX_BYTES = 32


def new_webhook_secret() -> str:
    """生成一个本机 inbound 签名 secret(复用安装链既有机制与格式)。"""
    return secrets.token_hex(WEBHOOK_SECRET_HEX_BYTES)


def binding_config_path(cfg):
    """绑定的落盘路径 —— ``iter_agentmail_configs`` 读入时注入的 ``_config_path``。"""
    if not isinstance(cfg, dict):
        return None
    raw = str(cfg.get("_config_path") or "").strip()
    return Path(raw) if raw else None


def existing_binding_webhook_secret(system_id: str, email: str) -> str:
    """已落盘绑定里的本机 secret(未落盘/无该字段/读不到 ⇒ "")。

    地址级激活落盘时用它复用既有 secret(**不覆盖** —— 覆盖会让已经对账好的宿主
    入站路由密钥失配)。
    """
    try:
        d = json.loads(_agent_config_path(system_id, email).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 读不到就当作没有(由调用方生成新的)
        return ""
    if not isinstance(d, dict):
        return ""
    return str(d.get("webhook_secret") or "").strip()


def write_binding_config(path, cfg) -> None:
    """按**既有绑定格式**原子重写一条绑定(私有 0600)。

    格式与 ``save_agent_config`` / TS ``saveBinding`` 逐字同款(JSON indent=2、
    UTF-8 原文、结尾换行、tmp+rename、0600 —— 走 ``atomic_write_private``, 即私有
    落盘的单一入口, 不存在"先写后 chmod"的全局可读窗口)。内部字段(``_`` 前缀,
    如 ``_config_path``)不落盘; ``webhook_secret`` 紧跟 ``api_key``(与
    ``saveBinding`` 里 webhook_url/webhook_secret 相对 api_key 的位置一致)。
    """
    src = cfg if isinstance(cfg, dict) else {}
    fields: Dict[str, object] = {}
    for k, v in src.items():
        if str(k).startswith("_"):
            continue
        fields[k] = v
        if k == "api_key" and src.get("webhook_secret"):
            fields["webhook_secret"] = src["webhook_secret"]
    if src.get("webhook_secret") and "webhook_secret" not in fields:
        fields["webhook_secret"] = src["webhook_secret"]
    atomic_write_private(Path(path),
                         json.dumps(fields, indent=2, ensure_ascii=False) + "\n")


def ensure_binding_webhook_secret(cfg) -> dict:
    """给一条绑定**就地自供**本机 webhook secret 并落盘(幂等: 已有 ⇒ 不覆盖)。

    用途 = "纯地址级(无系统级安装)"的 pull 循环启动前的**防御性纠正**: 升级前落的
    绑定没有 webhook_secret, 而重放必须 (url, secret) 成对 ⇒ 就地补一个本机 secret
    并写回绑定, 让循环真能启动(而不是被 ``no-secret`` 永久跳过)。

    不请求网关、不改云端契约: 该 secret 只签"打回自己本机入站端点"这一跳。

    返回 ``{"changed", "secret", "path", "reason", "detail"}``, reason ∈
    {exists(已有, 未动), provisioned(本次生成并落盘), no-path(绑定没有落盘位置,
    例如内存构造的 cfg —— 不假装成功), write-failed}。
    """
    src = cfg if isinstance(cfg, dict) else {}
    p = binding_config_path(src)
    existing = str(src.get("webhook_secret") or "").strip()
    if existing:
        return {"changed": False, "secret": existing,
                "path": str(p or ""), "reason": "exists", "detail": ""}
    if p is None:
        return {"changed": False, "secret": "", "path": "",
                "reason": "no-path", "detail": ""}
    secret = new_webhook_secret()
    src["webhook_secret"] = secret          # 就地更新: 同一轮 target_for 立刻可见
    try:
        write_binding_config(p, src)
    except Exception as e:  # noqa: BLE001 — 自供失败只是回到"未自供", 不抛
        src.pop("webhook_secret", None)     # 内存里也不留没落盘的假值
        return {"changed": False, "secret": "", "path": str(p),
                "reason": "write-failed", "detail": e.__class__.__name__}
    return {"changed": True, "secret": secret, "path": str(p),
            "reason": "provisioned", "detail": ""}



def replay_inbound_delivery(cfg, mail: dict, url: str, secret: str,
                            timeout: float = 15.0) -> dict:
    """把 pull 到的一封投递按 **push 的原样请求**打回宿主入站端点(同一条链)。

    与 bridge/网关推来的请求逐字同形: body = envelope JSON(原样), 头
    ``X-AIMail-Email`` + ``X-Webhook-Signature = hex(HMAC-SHA256(body, secret))``
    (gateway webhook.rs sign_payload 同款)。

    失败语义(调用方据此 **不 ack** ⇒ 下轮重拉, 不丢件): 非 2xx / 链路异常 /
    端点明确回"没有该地址的绑定"(``no_agent`` / ``no_local_config``)⇒ 抛异常。
    端点回 ``intercepted``(ping/pong 已被共享链吞掉并发过 pong)/ ``ignored`` /
    ``delivered`` / ``duplicate`` ⇒ 视为**已投递**(该 ack)。
    """
    payload = mail.get("body")
    if not isinstance(payload, (dict, list)):
        raise RuntimeError(
            "pulled delivery %r carries no JSON payload — not acking"
            % (mail.get("id"),))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sig = hmac.new(str(secret).encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-AIMail-Email": str(mail.get("email", "")),
                 "X-Webhook-Signature": sig})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code, raw = int(resp.status), resp.read()
    except urllib.error.HTTPError as e:  # noqa: PERF203 — 明确分类, 不吞诊断
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise RuntimeError("inbound replay rejected: HTTP %s %s %s"
                           % (e.code, e.reason, detail)) from e
    except Exception as e:
        raise RuntimeError("inbound replay failed (%s: %s)" % (e.__class__.__name__, e)) from e
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        data = {}
    status = data.get("status") if isinstance(data, dict) else ""
    if code >= 300:
        raise RuntimeError("inbound replay rejected: HTTP %d %s" % (code, raw[:200]))
    if status in ("no_agent", "no_local_config"):
        raise RuntimeError("inbound replay not delivered: status=%s %s"
                           % (status, raw[:200]))
    return {"status": code, "remote": data}


class AgentPullHandle:
    """一条绑定的轮询句柄(与 TS AgentPullHandle 同形: 可查 stats / 可停 / 可等退出)。"""

    def __init__(self, email: str, system_id: str, settings: dict,
                 thread, stop_event) -> None:
        self.email = email
        self.system_id = system_id
        self.started = True
        self.reason = settings.get("reason", "")
        self.interval_ms = settings.get("interval_ms", DEFAULT_PULL_INTERVAL_MS)
        self.limit = settings.get("limit", DEFAULT_PULL_LIMIT)
        self._thread = thread
        self._stop_event = stop_event
        self._stats: dict = {"pulled": 0, "acked": 0, "errors": 0}

    def stop(self) -> None:
        """停这条循环(幂等; 睡眠中也能立刻停)。"""
        self._stop_event.set()

    def stats(self) -> dict:
        """当前计数视图(pulled 实时, acked/errors 循环结束后为终值)。"""
        return dict(self._stats)

    def alive(self) -> bool:
        return bool(self._thread.is_alive())

    def join(self, timeout: Optional[float] = None) -> bool:
        """等循环退出; True = 已退出。"""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return "<AgentPullHandle %s %s every %dms>" % (
            self.email, self.reason, self.interval_ms)


def start_agent_pull_entries(system_id: str = "", on_email=None, env=None,
                             overrides=None, log=None, client_for=None,
                             target_for=None, timeout: float = 15.0) -> list:
    """给**每一条 agent-scope 绑定**起一条真轮询循环, 投递给调用方的入站链。

    - ``on_email(cfg, mail)``: 适配器自己的入站入口(pull 与 push 必须共用同一条
      链)。给了 ``on_email`` 就原样用它; 没给则用内置的"打回本机入站端点"
      (``target_for(cfg)`` → resolve_inbound_replay_target, 失败不 ack)。
    - ``target_for``: 预检 + 内置回调用的目标解析器(返回 resolve_inbound_replay_target
      的形状)。ok=False 的绑定**不启动**(日志给 reason), 不假装能投递。
    - ``client_for``: 测试缝 —— 给绑定造请求客户端(默认 _GatewayClient)。
    - 返回句柄列表(空 = 没有任何 agent-scope 绑定要 poll, 即 push/系统场景);
      禁用场景**不抛异常**。
    """
    env = os.environ if env is None else env
    log = log or (lambda line: logger.info("%s", line))
    bindings = iter_agentmail_configs(system_id)
    handles: list = []
    for cfg in bindings:
        email = str(cfg.get("email") or "")
        settings = resolve_agent_pull_settings(cfg, env, overrides)
        if not settings["enabled"]:
            # 有绑定但被判不启用 → 留一条解释行(静默会掩盖接线错误)。
            log("[aimail-pull] %s: not polling (%s) — push/system binding unchanged"
                % (email, settings["reason"]))
            continue
        target = None
        if target_for is not None:
            target = target_for(cfg)
            if (target and not target.get("ok")
                    and target.get("reason") == "no-secret"):
                # 防御性自供(幂等): 升级前落的绑定没有本机 secret ⇒ 就地补一个并写回
                # 绑定, 再**重解析一次**(url 的回退在适配器侧照旧)。语义边界: 只签
                # "打回自己的本机入站端点", 不请求网关、不改云端契约。已有 secret
                # 的绑定不会走到这里(那是有意的: 绝不覆盖)。
                prov = ensure_binding_webhook_secret(cfg)
                if prov.get("changed"):
                    log("[aimail-pull] %s: binding had no local webhook secret — "
                        "provisioned one into %s and re-resolved the replay target"
                        % (email, prov["path"]))
                    target = target_for(cfg)
                else:
                    log("[aimail-pull] %s: local webhook secret could not be "
                        "self-provisioned (%s%s)"
                        % (email, prov.get("reason", ""),
                           (": " + str(prov.get("detail"))) if prov.get("detail") else ""))
            if not target or not target.get("ok"):
                reason = (target or {}).get("reason", "no-target")
                log("[aimail-pull] %s: agent-scope binding but no local inbound "
                    "endpoint (%s) — polling not started; the mail stays pending "
                    "on the gateway until the endpoint+secret exist" % (email, reason))
                continue
        try:
            if client_for is not None:
                client = client_for(cfg)
            else:
                from aimail_tools import _GatewayClient
                client = _GatewayClient(str(cfg.get("gateway_url") or ""),
                                        str(cfg.get("api_key") or ""),
                                        timeout=timeout, identity=email)
        except Exception as e:  # noqa: BLE001 — 单条绑定失败不拖垮其余
            log("[aimail-pull] %s: client build failed (%s) — binding skipped"
                % (email, e))
            continue

        if on_email is None:
            def _cb(c, mail, _t=target):
                return replay_inbound_delivery(
                    c, mail, url=_t["url"], secret=_t["secret"], timeout=timeout)
            cb = _cb
        else:
            cb = on_email

        stop_event = threading.Event()
        handle = AgentPullHandle(email, str(cfg.get("system_id") or ""),
                                 settings, None, stop_event)

        def _live(_c, mail, _h=handle, _cb=cb):  # 实时计数(失败照旧抛出 ⇒ 不 ack)
            _h._stats["pulled"] += 1
            return _cb(_c, mail)

        def _run(_client=client, _cfg=cfg, _settings=settings,
                 _live=_live, _handle=handle, _stop=stop_event):
            stats = _client.start_polling(
                (lambda mail: _live(_cfg, mail)),
                interval=_settings["interval"], limit=_settings["limit"],
                stop_event=_stop)
            _handle._stats = dict(stats)

        thread = threading.Thread(
            target=_run, name="aimail-pull:%s" % email, daemon=True)
        handle._thread = thread
        thread.start()
        handles.append(handle)
        log("[aimail-pull] %s: polling enabled every %dms (limit %d, agent-scope key)"
            % (email, settings["interval_ms"], settings["limit"]))
    if not handles:
        log("[aimail-pull] no agent-scope binding — polling not started "
            "(push path unchanged; address-code activation enables it)")
    return handles


def stop_agent_pull_entries(handles) -> None:
    """宿主机停服/进程退出时停掉全部句柄(幂等)。"""
    for h in handles or []:
        try:
            h.stop()
        except Exception:  # noqa: BLE001 — 关停路径不抛
            pass
def _is_absolute_http_url(value: str) -> bool:
    """Only an absolute http(s) URL can be delivered to: the gateway hands the stored
    value straight to its HTTP client (a scheme-less ``host:port`` ⇒ builder error)."""
    v = str(value or "").strip()
    return v.startswith("http://") or v.startswith("https://")


def resolve_register_webhook_url(gw: dict, local_webhook_url: str) -> str:
    """webhook_host 三态 → 地址注册参数 webhook_url(SDK 去桥化, 2026-09-28 收敛).

    注册值只有三个来源, 按此顺序判定 —— **只看 `webhook_host` 一个键**:

      ① 键缺席                      → 本绑定的本机接收端点(直连 / 独立场景)
      ② 显式空值("", "   ")         → 空注册值 = pull(云端不回调;空不是缺陷)
      ③ 是可投递的绝对 http(s) URL  → 用它(环境主控声明的 push 入口;桥在场时
                                      CLI 已把桥对外 URL 写在 webhook_host)
      ③' 值不可投递(裸 host / host:port)→ **大声告警** + 退回本机端点: 绝不把
         网关无法 POST 的值注册出去(实测 builder error); 有桥的部署应注册桥自己的
         URL, 而不是 host:port。

    · 经桥响应自采的第三来源 `webhook_register_url` **已退役**: 磁盘残留值一律忽略
      (SDK 也不写它) —— 注册值不再由 SDK 从桥那里学, 桥语义不属于 SDK。
    · agentmail.json 的 webhook_url 恒为本地接收端点(与注册值是两个值)。
    """
    whh = gw.get("webhook_host") if isinstance(gw, dict) else None
    if whh is None:
        # 键缺席 = 没有环境声明的回调入口 ⇒ 直连/独立场景: 注册本机端点。
        return local_webhook_url
    declared = str(whh).strip()
    if not declared:
        return ""                                    # ①/② 显式空 = pull
    if _is_absolute_http_url(declared):
        return declared                              # ③ push 入口(必须是可投递 URL)
    logger.warning(
        "[aimail] webhook_host %r is not an absolute http(s) URL — the cloud cannot "
        "POST to it (builder error), so the local endpoint %r is registered instead. "
        "For a bridged deployment register the bridge's own URL "
        "(http://<host>/webhooks/aimail-inbound), not a bare host:port.",
        declared, local_webhook_url)
    return local_webhook_url                         # ③' 不可投递 ⇒ 退回本机端点


# ═══════════════════════════════════════════════════════════════
# manager 解析 + 注册/写白名单硬门(P1, owner 裁决 2026-09-30)
# ═══════════════════════════════════════════════════════════════
# 三个既有 env 名**互认**, 不新增第四种名: CLI 的集成契约 export
# `INTEGRATE_MANAGER_ADDRESS`、SDK 契约 export `AIMAIL_MANAGER_ADDRESS`、
# deerflow/历史侧只读 `AIMAIL_MANAGER` —— 三处互不相同正是 deerflow 白名单行
# 落成 `value=''` 的根因之一(取证 p1-p2-forensics-20260930.md)。
MANAGER_ENV_VARS = ("AIMAIL_MANAGER", "AIMAIL_MANAGER_ADDRESS",
                    "INTEGRATE_MANAGER_ADDRESS")


class ManagerRequiredError(RuntimeError):
    """注册/写白名单硬门: manager 解析链(显式参数 → env 三名互认)走完仍为空。

    以空值注册会在网关建出 `value=''` 的 manager 白名单行 ⇒ 出站
    `550 Sender not whitelisted`(deerflow J4e/J5-1 实测)。owner 裁决:
    不允许静默降级, 必须响亮失败。注销/只读等不需要 manager 的路径不走此门。
    """


def resolve_manager_address(manager_address: str = "") -> str:
    """显式参数 → env 三名互认 → 仍空返回 `''`(由调用方决定是否报错)。"""
    v = str(manager_address or "").strip()
    if v:
        return v
    for key in MANAGER_ENV_VARS:
        e = str(os.environ.get(key) or "").strip()
        if e:
            return e
    return ""


def require_manager_address(manager_address: str = "", where: str = "register") -> str:
    """硬门: 解析完仍为空 ⇒ 抛 ManagerRequiredError(rc≠0, 文案指明缺哪块)。"""
    v = resolve_manager_address(manager_address)
    if not v:
        raise ManagerRequiredError(
            f"缺 manager(参数/env 均未给): {where} 的注册/白名单不接受空值 —— "
            f"请给显式参数,或设置 env {' / '.join(MANAGER_ENV_VARS)}"
            f"(禁止以 '' 注册, 否则白名单行 value='' ⇒ 出站 550 Sender not whitelisted)")
    return v


def register_agent_email(client, system_id: str, email: str,
                         webhook_url: str = "", webhook_secret: str = "",
                         manager_address: str = "") -> dict:
    """注册链（幂等，4 步）：register_email(generate_code) → 已存在更新 webhook →
    manager 白名单 → activate_address。返回 {"api_key", "activation_code"}
    （api_key 为空 = 激活 pending/已存在；activation_code 供延迟激活语义）。
    域由 gateway 从 email 地址自行提取（2026-08-18 起不再传 mx_domain/domain 参数）。

    client 须提供：register_email / list_system_domains / update_system_domain /
    activate_address（aimail_tools._GatewayClient 全具备；白名单由网关注册接口自动创建）。

    **P1 硬门(2026-09-30 owner 裁决)**: 这是"注册/写白名单"的公共收口,
    install / register / reconcile 三条路都过这道门 —— 显式参数与 env 兜底
    走完仍为空即抛 `ManagerRequiredError`, 不允许以 `''` 注册(注销/只读路径
    走 `deregister_agent_email`, 不经此处)。
    """
    manager_address = require_manager_address(manager_address,
                                              where=f"register {email}")
    result = client.register_email(
        system_id=system_id, email=email,
        webhook_url=webhook_url, webhook_secret=webhook_secret,
        manager_address=manager_address, generate_code=True,
    )
    activation_code = ""
    if isinstance(result, dict):
        activation_code = result.get("activation_code", "") or ""
        status = result.get("status", "")
        # status=0 = 传输层失败(_request 兜底),与 HTTP 错误同等对待:
        # 静默吞掉会让调用方误判为"激活 pending"继续走完链(审计教训:静默失败面)
        if not status or str(status) not in ("created", "200", "201", 200, 201):
            msg = str(result.get("error", "")) + str(result.get("detail", ""))
            if "already exists" in msg.lower() or "exists" in msg.lower():
                activation_code = ""
                # 已存在 → 更新 webhook 配置（幂等）；两个值全空时跳过——
                # 空 body 更新会让网关把已存 webhook_url/secret 无条件覆写为 NULL
                if not webhook_url and not webhook_secret:
                    return {"api_key": "", "activation_code": ""}
                # 缺 secret 不得覆写（第 10 缺陷防御纵深，2026-09-28）：网关 PUT 是**全量
                # 覆写**（body 里没有 webhook_secret 键 ⇒ 云端 secret 被写成 NULL），
                # 而验签方按**本地绑定**的 secret 验 ⇒ 两边分叉 = 入站恒 401。
                # 故本分支先按**真源**补全：调用方没给 ⇒ 读该地址的绑定（唯一真源）；
                # 仍没有 ⇒ **不写**（保留云端现值），大声告警，绝不抹空。
                # 2026-10-02 第 10 缺陷定因(E1): **绑定优先** —— 绑定是唯一真源(卡B 口径),
                # 调用方给的值只在绑定缺 secret 时才采用。原"参数优先"在 deerflow 上必分叉:
                # 一轮 install 注册 3 次(platforms.json install_steps: sdk_install →
                # register_default → register_all), 每次新铸随机值(manage.py:217/:362) ⇒
                # 新值 PUT 覆写网关(storage.rs:693 全量覆写), 而绑定只在拿到
                # api_key/activation_code 时才落盘(manage.py:224-235) ⇒ 网关=第 3 把 /
                # 绑定=第 1 把 ⇒ D2B 两侧摘要不等(历史 13 红全在 deerflow, 其余四平台 56 绿)。
                _bp = _agent_config_path(system_id, email)
                _bind_secret = str(existing_binding_webhook_secret(system_id, email) or "").strip()
                secret = _bind_secret or str(webhook_secret or "").strip()
                if not secret:
                    # 2026-10-01 owner 裁决(方案B): 绑定缺 secret ⇒ 就地补一把并**携带**到网关,
                    # 绝不出 url-only PUT(保留 2026-09-28 第 10 缺陷防御: 不把云端 secret 抹成 NULL)。
                    # 单一真源=绑定 ⇒ 网关采纳同一把 ⇒ D2B 两侧摘要同源(修"两把钥匙")。
                    # 原"弃权返回"会让 D2B 判 BAD(两侧不等), 故改为可自愈补全; 补不全才弃权且大声。
                    if _bp is None:
                        logger.warning(
                            "[aimail] registration update for %s skipped: no binding path "
                            "to provision webhook_secret (url-only PUT would null the cloud "
                            "secret); run 'aimail repair'", email)
                        return {"api_key": "", "activation_code": ""}
                    _bcfg = {}
                    try:
                        if _bp.exists():
                            _t = json.loads(_bp.read_text(encoding="utf-8"))
                            if isinstance(_t, dict):
                                _bcfg = _t
                    except Exception:  # noqa: BLE001 — 读坏就当没有, 由下方补全
                        _bcfg = {}
                    if not str(_bcfg.get("webhook_secret") or "").strip():
                        _bcfg["webhook_secret"] = new_webhook_secret()
                        try:
                            write_binding_config(_bp, _bcfg)
                        except Exception as _we:  # noqa: BLE001 — 补不全必须弃权(不许静默分叉)
                            logger.warning(
                                "[aimail] %s: binding webhook_secret provisioning failed (%s); "
                                "skipping update to avoid a url-only PUT", email, _we)
                            return {"api_key": "", "activation_code": ""}
                        logger.info(
                            "[aimail] %s: binding had no webhook_secret — provisioned locally "
                            "and carrying it to the gateway (single source: binding)", email)
                    secret = str(_bcfg.get("webhook_secret") or "").strip()
                    if not secret:
                        logger.warning(
                            "[aimail] %s: binding webhook_secret is empty after provisioning; "
                            "skipping update (url-only PUT would null the cloud secret)", email)
                        return {"api_key": "", "activation_code": ""}
                elif _bp is not None and _bp.exists() and not _bind_secret:
                    # 绑定文件在但缺 secret、调用方给了 ⇒ 先把这把回写绑定(真源补齐)再 PUT:
                    # 否则网关=参数值、绑定空 ⇒ 又是一对分叉(卡B ③ 只覆盖"两边都没给"的一半)。
                    # 回写失败 ⇒ 不发 PUT(保持现状, 不制造新分叉) —— 与卡B 同口径"宁可弃权"。
                    _bcfg_back = {}
                    try:
                        _t = json.loads(_bp.read_text(encoding="utf-8"))
                        if isinstance(_t, dict):
                            _bcfg_back = _t
                    except Exception:  # noqa: BLE001 — 读坏就按空配置重建字段
                        _bcfg_back = {}
                    if not str(_bcfg_back.get("webhook_secret") or "").strip():
                        _bcfg_back["webhook_secret"] = secret
                        try:
                            write_binding_config(_bp, _bcfg_back)
                        except Exception as _we:  # noqa: BLE001
                            logger.warning(
                                "[aimail] %s: binding webhook_secret backfill failed (%s); "
                                "skipping the gateway write to avoid a two-key split", email, _we)
                            return {"api_key": "", "activation_code": ""}
                        logger.info(
                            "[aimail] %s: binding had no webhook_secret — carried the caller's "
                            "value into the binding first (single source: binding)", email)
                try:
                    domains = client.list_system_domains(system_id)
                    for d in (domains if isinstance(domains, list) else []):
                        if isinstance(d, dict) and d.get("domain") == email:
                            client.update_system_domain(str(d.get("id", "")),
                                                        webhook_url, secret)
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

    # 注册链到此结束: **没有第 5 步**。路由是环境主控(CLI)的职责(2026-09-28 SDK 去桥化),
    # 由宿主在入站真的在服务时通知 CLI(`aimail address -a <addr> --inbound-live`)后由
    # CLI 自行决定; SDK 不写桥、不问桥、不写环境配置。
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

    # 3. 白名单清理 —— 精确匹配 (domain_addr==email, value==manager) 后**按 id 删**。
    #    审计 2026-09-21: 原实现走 DELETE /api/v1/whitelists?domain_addr=&value=,
    #    而网关 admin 分支把 domain_addr 当**域**用(按域列 system 全部行再取首个
    #    value 命中) ⇒ 传完整地址得到空列表 → 404;传域则可能误删同 manager 的
    #    其它地址的行。夹具实测: 地址注销后 whitelists 行仍残留。
    try:
        domain = email.split("@", 1)[1] if "@" in email else ""
        if not domain or not hasattr(client, "list_whitelists_by_domain"):
            out["whitelist"] = "unsupported"
        else:
            rows = client.list_whitelists_by_domain(domain)
            mine = [r for r in rows
                    if isinstance(r, dict)
                    and r.get("domain_addr") == email
                    and r.get("id") is not None]
            exact = [r for r in mine if manager_address and r.get("value") == manager_address]
            # F10(2026-09-25): 精确匹配落空时的兜底 —— 本地址名下的行都是孤儿。
            # 注销的语义就是"该地址被拆除"(api-key / system_domains 已删), 而
            # domain_addr==email 已唯一钉住地址 ⇒ 不会误伤别的 agent(2026-09-21 要求
            # value==manager 是为了防"按 value 盲删"跨地址误伤, 不适用于按地址收敛)。
            # 实测两类落空: openclaw 绑定里 manager_address 为空(CLI 未导出 AIMAIL_*),
            # deerflow 的白名单行 value 本身为空 ⇒ 只按 value 匹配必然留残留。
            targets = exact or mine
            if targets:
                st = []
                for r in targets:
                    rr = client.delete_whitelist_entry_by_id(int(r["id"]))
                    st.append(str(rr.get("status", rr)) if isinstance(rr, dict) else str(rr))
                out["whitelist"] = ",".join(st) + ("" if exact else "(by_addr)")
            else:
                out["whitelist"] = "not_found_addr" if rows else "not_found"
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
    L1-only `aimail install --system-only` (the single activation implementation;
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
    #    B ruling 2026-09-23: the standalone subcommand is gone — the ABI is
    #    `aimail install --system-only` now (mirror: tssdk ensure-system.ts).
    argv = [cli, "install", "--system-only"]
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
                "error": f"aimail install --system-only returned unparsable output (exit {out.returncode})",
                "hint": "run `aimail install --home <root>` manually to see the error"}
    if parsed.get("success") is not True or out.returncode != 0:
        r = {"ok": False, "error": str(parsed.get("error") or f"install --system-only failed (exit {out.returncode})")}
        if parsed.get("hint"):
            r["hint"] = str(parsed["hint"])
        return r
    return {"ok": True,
            "system_id": str(parsed.get("system_id", "")),
            "activated": parsed.get("path") == "activation"}

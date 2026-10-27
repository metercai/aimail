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
from aimail_contract import POINTER_FILE, BINDING_FILE


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


# ── persona capability switches (shared across agent systems, set by capability when integrating new systems) ────
# True  = supports persona: derived address {role}.{profile}@{domain} preserved + config validation +
#         LLM session pre persona switch (Hermes full capabilities, default value)
# False = does not support persona: role = independent agent, recipient address normalized to base address
#         (OpenClaw etc.: aimail_base imported then set to False by system layer)
# Processing logic framework is consistent; differences are driven only by this switch——read internally by preprocess.
PERSONA_SUPPORTED = True



# ═══════════════════════════════════════════════════════════════
# a2a_board helpers — template filling, role/context utilities
# ═══════════════════════════════════════════════════════════════


def fill_template(text: str, ctx: dict) -> str:
    """Replace {{KEY}} placeholders with values from ctx (keys uppercase)."""
    for key, val in ctx.items():
        text = text.replace("{{" + key + "}}", str(val))
    return text


def _read_role_file(name: str, *, allow_common: bool = False) -> str:
    """Read a2a_board role file — address-level first, then system-level.

    Role names are case-insensitive: the filename is always lowercased
    before lookup, so callers may pass any casing.

    Priority:
    1. ~/.aimail/systems/{sid}/{addr}/role_prompt/{name}.md  (address override)
    2. ~/.aimail/systems/{sid}/board/role_prompt/{name}.md   (system-level)
    3. common.md fallback (system-level dir) — **board channel only**
       (``allow_common=True``, ruling 2026-10-25: the a2a board keeps its
       generic role template when the named role is missing)

    When the named file is missing and common.md is not allowed (channels
    whoami / Role_Calibrator / X-AIMail-Prompt / prompt_rules), logs an
    ERROR (missing name + searched paths) and returns "" — the caller then
    continues the chain without injecting (defect 2, 2026-10-25).
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
    # 3) common.md — board channel only (ruling 2026-10-25)
    common = sys_role_dir / "common.md"
    if allow_common and common.exists():
        logger.info("[a2a_board] role '%s' not found, using common.md", name)
        return common.read_text(encoding="utf-8")
    searched = [sys_role_dir / f"{name}.md"]
    if addr:
        searched.insert(0, _aimail_system_dir(sid) / addr / "role_prompt" / f"{name}.md")
    if allow_common:
        searched.append(common)
    logger.error(
        "[aimail_gateway] role file missing: '%s' — searched: %s — no injection, chain continues",
        name,
        " | ".join(str(p) for p in searched),
    )
    return ""


# ═══════════════════════════════════════════════════════════
# prompt_rules: customizable relationship between recognition conditions ↔ role files (ruling 2026-09-23)
#   - fields: subject/body/sender/recipient; each field "contains" match (case-insensitive),
#     Multiple keywords within a field = OR, between fields = AND (ruling ①)
#   - name = {index}_{filename}, index 10..99 (ruling ②); actual loading depends on rule["file"]
#     (explicitly written when CLI persists; read side does not do string derivation)
#   - Storage: prompt_rules key in agentmail.json (only for this agent; ruling ④ key name)
# Single source of truth: CLI `aimail prompt test` dry-run also calls the functions here.
# ═══════════════════════════════════════════════════════════
PROMPT_RULE_NAME_RE = re.compile(r"^[1-9][0-9]_[a-z0-9_-]{1,64}$")


def prompt_rule_name_ok(name: str) -> bool:
    """name = {10..99 sequence}_{filename}; filename segment 1..64 chars (ruling ② + CLI validation)."""
    return bool(PROMPT_RULE_NAME_RE.fullmatch(name or ""))


def read_prompt_rules() -> list:
    """Read agent-level prompt_rules (agentmail.json injected via adapter layer config loader).

    Every item read passes a validity filter (bad items are discarded + WARN —— the delivery chain never fails due to bad rules),
    returns the rule table sorted by name alphabetically (= sequence numeric order, prefix fixed-width).
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
    """OR within a field / AND across fields (ruling ①). Absent fields do not participate; empty list = not given; bad type = no match."""
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


def build_ctx(payload: dict, headers: dict, config: Optional[dict] = None) -> dict:
    """Build template context dict from available data.

    ``config`` is the current agent's loaded config (identity: email/agent_id/
    system_id) — the per-agent anchor the SOUL/skills/toolset providers resolve
    from. SOUL_MD_CONTENT / SKILLS_LIST / TOOLSET_LIST all reflect the host's
    actual state for this agent (host-injected; empty when the host injects
    nothing or the value cannot be determined).
    """
    return {
        "AGENTMAIL_ADDRESS": payload.get("my_aimail_addr", ""),
        "BOARD_ID": payload.get("board_id", ""),
        "BOARD_ROLE": payload.get("board_role", ""),
        "FROM_ROLE": payload.get("from_role", ""),
        "INQUIRY_SENDER": payload.get("from", ""),
        "INQUIRY_SUBJECT": payload.get("subject", ""),
        "SOUL_MD_CONTENT": _read_soul_md(config),
        "SKILLS_LIST": ", ".join(_read_skills(config)),
        "TOOLSET_LIST": _read_toolset(config),
    }


# ── Config helpers ──

def aimail_home() -> Path:
    """Canonical aimail home root (single source of truth).

    Resolves env AIMAIL_HOME to a home-root dir,
    falling back to ~/.aimail. All path constructors (three-layer convergence 2026-09-23:
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
    raw_webhook = os.environ.get("AIMAIL_BRIDGE_URL", "")
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
            # Not an "error" but "not installed on this machine/adapter layer has not injected pointer": callers (inbound preprocessing,
            # platform hooks) all degrade to "no gateway" —— B1 contact profile and attachment download skipped, the email itself
            # is delivered to the agent as usual. Only a locatable warn is left here; see check_profile_pointer() for the startup
            # self-check see check_profile_pointer().
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
            # Audit D3: the gateway file written by agent self-activation is agent-scoped (scope=agent,
            # without admin key) —— it is also "available", the determination cannot only look at admin_key/product_code.
            if cfg.get("gateway_url") and (
                cfg.get("admin_key") or cfg.get("product_code") or cfg.get("scope") == "agent"
            ):
                return cfg
        except Exception:
            pass

    return None


# ── Injection point (set by adapter layer; Hermes → pysdk/hermes/aimail_hermes.py,
#             DeerFlow → pysdk/deer-flow/aimail_deerflow.py）────────────
# Platform differences (config source/personas/profile directory/board registration) are injected by the adapter layer,
# the common core remains platform-agnostic. Safe defaults (None/empty/no-op) are used when not injected.
# _SOUL/_SKILLS/_TOOLSET_PROVIDER receive the current agent's config (agent identity: email/agent_id/
# system_id), the adaptation layer uses this to resolve the SOUL.md / skills / toolset of "this agent"
# — multi-agent platform different agents have different SOULs. The core only passes identity, does not
# touch the file system. All three reflect the host's actual state: empty when the host injects nothing
# or the value cannot be determined (no fallback to any static constant).
_CONFIG_LOADER = None          # () -> Optional[dict]      agent config loader
_PERSONAS_PROVIDER = None      # () -> dict                personas config
_PROFILE_DIR_RESOLVER = None   # () -> Optional[str]       profile directory (gateway config location)
_SOUL_PROVIDER = None          # (config) -> str           SOUL content (board ctx, resolved by agent identity)
_SKILLS_PROVIDER = None        # (config) -> list[str]     skills list (board ctx, resolved by agent identity)
_TOOLSET_PROVIDER = None       # (config) -> list[str]     toolset names actually registered on the host (board ctx)
_BOARD_GATEWAY_SINK = None     # (board_id, gateway_url) -> None


def _read_soul_md(config: Optional[dict] = None) -> str:
    """SOUL content (injection point, resolved by agent identity). Injected by adapter layer; default empty."""
    if _SOUL_PROVIDER is None:
        return ""
    try:
        return _SOUL_PROVIDER(config) or ""
    except Exception:
        return ""


def _read_skills(config: Optional[dict] = None) -> list:
    """skills list (injection point, resolved by agent identity). Injected by adapter layer; default empty."""
    if _SKILLS_PROVIDER is None:
        return []
    try:
        out = _SKILLS_PROVIDER(config)
        return list(out) if out else []
    except Exception:
        return []


def _read_toolset(config: Optional[dict] = None) -> str:
    """Agent-level tool names actually registered on this host, comma-joined (injection point,
    resolved by agent identity). Injected by the adapter layer from the host's actual state;
    empty when nothing is registered or the state cannot be determined. No fallback to any
    static constant."""
    if _TOOLSET_PROVIDER is None:
        return ""
    try:
        out = _TOOLSET_PROVIDER(config)
        return ", ".join(out) if out else ""
    except Exception:
        return ""


def _load_profile_config() -> Optional[dict]:
    """agent config loader (injection point). Adapter layer injects platform implementation; returns None if not injected
    (preprocess goes to 'not configured' branch)."""
    if _CONFIG_LOADER is not None:
        return _CONFIG_LOADER()
    return None


def check_profile_pointer() -> bool:
    """Self-check after the adapter-layer registration injection: whether the .agentmail pointer in the profile dir resolves to a system_id.

    The pointer is part of the platform contract (preprocess B1 step calls gateway config resolution with no args,
    can only rely on it to locate system_id). Returning False does not mean failure, but "not yet installed/not injected on this machine":
    at this time inbound preprocessing degrades (B1 contact profile and attachment download skipped, **email delivered to agent as usual**,
    will not fail entirely), this function also leaves a warn to make the degradation visible, and surfaces the configuration issue to
    the adapter layer startup instead of every email.
    """
    resolver = _PROFILE_DIR_RESOLVER
    profile_dir = None
    if resolver is not None:
        try:
            profile_dir = resolver()
        except Exception:  # noqa: BLE001 — self-check itself never raises
            profile_dir = None
    ok = False
    if profile_dir:
        pointer = Path(profile_dir) / ".agentmail"
        if pointer.is_file():
            try:
                ok = bool(json.loads(pointer.read_text()).get("system_id"))
            except Exception:  # noqa: BLE001 — corrupted pointer treated as not installed
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
    """personas config (injection point). Default empty (no persona config)."""
    if _PERSONAS_PROVIDER is not None:
        return _PERSONAS_PROVIDER()
    return {}


def _register_board_gateway(board_id: str, gateway_url: str) -> None:
    """board gateway registration (injection point). Hermes adapter layer injects writing profile_cfg; default no-op."""
    if _BOARD_GATEWAY_SINK is not None:
        _BOARD_GATEWAY_SINK(board_id, gateway_url)


# ── Platform-agnostic agent context (fallback MCP/CLI shared, promoted on 2026-08-18) ─────
# Fallback MCP/CLI and each platform adapter layer share the same implementation (single authority): any agent system
# Just drop agentmail.json into the shared layout to reuse it.
_ACTIVE_AGENT_CONFIG: Optional[dict] = None  # Configuration from the most recent set_agent_context


def _scan_systems_for_agent(agent_id: str, system_id: str = "") -> Optional[dict]:
    """Walk systems/{sid}/*/agentmail.json by agentId to find matching configs (platform-agnostic).

    If system_id is omitted, scan all systems/ directories; return agentmail.json content on match.
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
    """Find the address-key config by agentId (shared layout agentmail.json, platform-agnostic)."""
    return _scan_systems_for_agent(agent_id, system_id)


def save_agent_config(agent_id: str, cfg: dict, system_id: str) -> Path:
    """Atomic write address key agentmail.json (shared layout, platform-agnostic) — the only one on the Python side
    shared persistence implementation, aligned with TS mail-core config.ts saveBinding (tmp+rename+0600).

    hermes/deer-flow registration chains uniformly persist via this; if agent_id is non-empty, write to cfg
    (load_agent_config/set_agent_context match by agent_id).
    """
    cfg = dict(cfg)
    if agent_id:
        cfg["agent_id"] = agent_id
    cleaned = _clean_agent_dir_name(cfg.get("email", ""))
    p = aimail_home() / "systems" / str(system_id) / cleaned / "agentmail.json"
    # Address directory 0o700: directory containing credentials (api_key), group/others cannot enter
    p.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    # tmp created with 0600: writing then chmod creates a brief globally readable window (including api_key)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(p)
    return p


# ── Semantic thin entry points on the CLI side (owner ruling A, 2026-09-28)────────────────────────────
# Layering ruling: System-level environment files are written by **CLI**, per-agent binding files by **SDK**.
# When CLI needs to modify binding content, it no longer holds write calls itself, only calls these semantic thin functions below; internally all three go through
# the existing `save_agent_config` above (atomic tmp+rename + 0600 semantics unchanged verbatim, implementation not copied).
# Writing conventions: The docstrings of these functions **do not contain** the literal binding file name —— the contract literal ratchet is by
# "file × key" only allows reduction not addition, this module is already the existing holder of this key, cannot add due to new code.

def update_binding(system_id: str, cfg: dict, updates: dict) -> Path:
    """Field-level update binding content and atomically persist (semantic thin function: merge updates → `save_agent_config`).

    CLI side only triggers and gets values (`aimail prompt add|rm` modifies prompt_rules, `aimail address
    set-manager` modifies manager_address); judgment and persistence are on the SDK side.
    """
    merged = dict(cfg)
    merged.update(updates or {})
    return save_agent_config(merged.get("agent_id", ""), merged, system_id)


def backfill_binding(cfg: dict, system_id: str) -> Path:
    """Persist the **already calculated full** binding content (pure write-back entry).

    Judgment (which fields to fill, whether webhook target is aligned) is handled by upper-layer repair: SDK gate
    `update(action="repair")` self-judges and self-fills; this function is only responsible for writing back via `save_agent_config`.
    """
    return save_agent_config(cfg.get("agent_id", ""), cfg, system_id)


def rename_binding(old_dir, new_email: str, cfg: dict, system_id: str) -> dict:
    """Local binding migration when renaming an address —— **directory + content = the same thing**.

    Bindings are stored by address directory, so "moving directory" and "writing content" separated will leave intermediate state (directory renamed,
    content not persisted); both are completed here, content still goes through `save_agent_config` (atomic+0600).
    `old_dir` must be the **actual** binding directory located by the caller (tolerating legacy naming), no secondary derivation.

    Returns ``{"path": Path, "dir": Path, "moved": bool, "merged": bool}`` --
    ``merged=True`` indicates target directory already exists (only merge content, do not move directory), caller warns.
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


def rename_address(system_id: str, old_email: str, new_name: str, cfg: dict) -> dict:
    """Complete SDK domain operation for address rename (owner ruling 2026-10-02: agent count and naming = SDK domain,
    rename is also SDK CRUD; CLI is just caller/data consumer).

    Order (stop if any step throws, previously effective parts are stated in exception text):
      1. Validation + derivation (`email_for_agent` unique naming source of truth) + conflict pre-check (local binding, same old semantics);
      2. Cloud rename (throws on failure — local unchanged);
      3. Whitelist orphan cleanup: query by **full old_email** as key (address line ∈ this system ⇒ shared domain
         also correctly resolves to this system, rather than the domain owner) —— best-effort;
      4. Local binding migration (`rename_binding`: directory+content completed at once);
      5. Platform root pointer (contract POINTER_FILE and historical alternative names) synchronization —— retain existing fields;
      6. **Rename finish = online signal** `address --inbound-live` (contract payload=address only):
         Environment Master (CLI) performs full reconciliation of routing based on persisted data upon receipt — timing and content are therefore unique and idempotent.

    Returns ``{"old_email", "new_email", "unchanged", "dir", "moved", "merged",
    "migrated", "signal"}``。
    """
    name = str(new_name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]+", name):
        raise ValueError(
            f"invalid address name {new_name!r}: must be atext without '.' "
            f"(no dot/space/@)")
    old = str(old_email or "").strip()
    if not old or "@" not in old:
        raise ValueError(f"invalid current address {old_email!r}")
    domain = str(cfg.get("domain") or "").strip()
    if not domain:
        raise ValueError("system config lacks domain")
    system_name = str(cfg.get("system_name") or "").strip()
    gw = str(cfg.get("gateway_url") or "").rstrip("/")
    ak = str(cfg.get("admin_key") or "")
    if not gw or not ak:
        raise ValueError("system config lacks gateway_url/admin_key")
    # Single source of truth for unique naming (no manual concatenation): cleaning rules and alias normalization are all inside email_for_agent
    new = email_for_agent(name, domain, system_name, default_aliases=())
    if new == old:
        return {"old_email": old, "new_email": old, "unchanged": True,
                "dir": "", "moved": False, "merged": False, "migrated": True,
                "signal": None}
    # 1) Conflict pre-check: base name collision with other registered addresses in the same system (local binding = same semantics as old CLI,
    #    no new network dependencies; cloud uniqueness is guaranteed by the gateway rename endpoint).
    for row in iter_agentmail_configs(system_id):
        em = str((row or {}).get("email") or "")
        if not em or em == old:
            continue
        base = em.split("@", 1)[0].split(".", 1)[0]
        if base == name:
            raise ValueError(
                f"new address name {name!r} conflicts with registered address {em}")
    from aimail_tools import _GatewayClient
    client = _GatewayClient(gw, ak)
    # 2) Cloud rename —— server-side validates old exists (404 ⇒ throw), local unchanged
    try:
        client._request("POST", f"/api/v1/admin/systems/{system_id}/addresses/rename",
                        body={"old_email": old, "new_email": new})
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"server-side rename {old} -> {new} failed "
                         f"(local untouched): {e}") from e
    # 3) Whitelist orphan line cleanup (best-effort): key = full old address ⇒ resolves to this system's line
    try:
        if hasattr(client, "list_whitelists_by_domain"):
            rows = client.list_whitelists_by_domain(old)
            n = 0
            for r in (rows if isinstance(rows, list) else []):
                if (isinstance(r, dict) and r.get("domain_addr") == old
                        and r.get("id") is not None):
                    client.delete_whitelist_entry_by_id(int(r["id"]))
                    n += 1
            if n:
                logger.info("rename: removed %d stale whitelist row(s) for %s", n, old)
    except Exception as e:  # noqa: BLE001
        logger.warning("rename: whitelist cleanup for %s failed: %s", old, e)
    # 4) Local migration: directory rename + email update (single SDK call, owner decision A)
    base_root = aimail_home() / "systems" / str(system_id)
    dir_path, moved, merged, migrated = "", False, False, True
    if base_root.is_dir():
        for d in base_root.iterdir():
            jf = d / "agentmail.json"
            try:
                acfg = json.loads(jf.read_text())
            except Exception:  # noqa: BLE001 — skip non-bound directories
                continue
            if str(acfg.get("email") or "") != old:
                continue
            acfg["email"] = new
            try:
                res = rename_binding(d, new, acfg, system_id)
                dir_path = str(res.get("dir") or "")
                moved = bool(res.get("moved"))
                merged = bool(res.get("merged"))
            except Exception as e:  # noqa: BLE001 — cloud has renamed, not fatal
                migrated = False
                logger.warning("rename: local binding migration failed "
                               "(cloud already renamed, reinstall rebuilds local): %s", e)
            break
    # 5) Platform root pointer sync (contract POINTER_FILE + historical alternate names; preserve existing fields in pointer)
    home = str(cfg.get("system_home") or "")
    if home and Path(home).is_dir():
        for ptr_name in (POINTER_FILE, ".aimail"):
            pp = Path(home) / ptr_name
            try:
                if not pp.is_file():
                    continue
                pdata = json.loads(pp.read_text())
                if str(pdata.get("email") or "") == old:
                    pdata["email"] = new
                    atomic_write_private(
                        pp, json.dumps(pdata, ensure_ascii=False, indent=2) + "\n")
            except Exception as e:  # noqa: BLE001
                logger.warning("rename: pointer %s update failed: %s", pp, e)
    # 6) Rename finalization = online signal (triggers full CLI reconciliation; never raises)
    signal = None
    if migrated:
        signal = notify_inbound_state(new, "live")
    return {"old_email": old, "new_email": new, "unchanged": False,
            "dir": dir_path, "moved": moved, "merged": merged,
            "migrated": migrated, "signal": signal}


def cleanup_system_whitelists(client, system_id: str, addresses, domains,
                              deregistered) -> dict:
    """Uninstall fallback: clean **this system's own** whitelist rows. Returns {"removed", "per_key", "errors"}.

    N2-B (decision 2026-10-02, same pattern closure as rename_address): cloud CRUD for whitelist resources belongs to SDK,
    CLI only triggers and reports. Factual basis /tmp/d2-repro.log (gateway SQL data layer replay):
      · Query key **can only** be this system's system_domains row (address row + own bare domain row; upstream =
        list_system_domains(sid)) —— domain_addr UNIQUE ⇒ reverse resolution always yields this system;
        shared tenants have no bare domain row, old implementation using bare cfg_domain as key would resolve to **domain owner system**
        ⇒ delete platform/domain owner lines, miss cleaning own lines (shared domain dual-half defect).
      · Address keys that have been successfully deregistered are no longer scanned (their rows are cleaned by the deregistration chain itself).
      · Before deletion, filter again by response system_id==sid (response includes system_id; gateway types.rs:279);
        missing (None) is treated as compatible with old gateway, trusting proven keys.
      · If key set is empty, skip (better not to scan if ownership cannot be determined) —— the old cfg_domain fallback was exactly the root cause.
    client is injected by caller (same convention as deregister_agent_email: this function family shares the same client).
    """
    if not hasattr(client, "list_whitelists_by_domain"):
        return {"removed": 0, "per_key": {}, "errors": []}
    sweep_keys = sorted(set(list(domains or []) +
                            [a for a in (addresses or []) if a not in (deregistered or set())]))
    if not sweep_keys:
        return {"removed": 0, "per_key": {}, "errors": []}
    removed = 0
    per_key: Dict[str, int] = {}
    errors: List[str] = []
    seen = set()
    for key in sweep_keys:
        try:
            rows = client.list_whitelists_by_domain(key)
        except Exception as e:  # noqa: BLE001 — single key failure does not block other keys
            errors.append(f"list {key}: {e}")
            continue
        n = 0
        for r in (rows if isinstance(rows, list) else []):
            if not isinstance(r, dict) or r.get("id") is None:
                continue
            if r.get("system_id") not in (None, system_id):
                continue                      # Double insurance: never touch others' rows
            rid = int(r["id"])
            if rid in seen:
                continue
            seen.add(rid)
            try:
                client.delete_whitelist_entry_by_id(rid)
                n += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"delete id={rid} {r.get('domain_addr')}: {e}")
        per_key[key] = n
        removed += n
    return {"removed": removed, "per_key": per_key, "errors": errors}


def set_agent_manager(system_id: str, email: str, manager: str,
                      cfg: dict, binding_cfg: dict) -> None:
    """set-manager SDK domain closure (owner decision 2026-10-02): cloud agent-meta and local
    binding are two halves of the same resource, completed in one call; cloud failure raises (local unchanged)."""
    mgr = str(manager or "").strip()
    if not mgr or "@" not in mgr:
        raise ValueError(f"invalid manager address {manager!r}")
    gw = str(cfg.get("gateway_url") or "").rstrip("/")
    ak = str(cfg.get("admin_key") or "")
    if not gw or not ak:
        raise ValueError("system config lacks gateway_url/admin_key")
    from aimail_tools import _GatewayClient
    try:
        _GatewayClient(gw, ak)._request(
            "PUT", f"/api/v1/admin/agent-meta/{email}",
            body={"manager_address": mgr})
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"updating manager on the gateway failed: {e}") from e
    update_binding(system_id, binding_cfg, {"manager_address": mgr})


def _ensure_private_dir(d: Path, mode: Optional[int]) -> None:
    """Private directory: create if not exists according to mode; tighten if exists but too wide (only directories managed by this SDK)."""
    d.mkdir(parents=True, mode=mode or 0o700, exist_ok=True)
    if mode is None:
        return
    try:
        if d.stat().st_mode & 0o077:
            os.chmod(d, mode)
    except OSError:
        pass


def atomic_write_private(path: Path, text: str, ensure_dir_mode: Optional[int] = 0o700) -> None:
    """Atomic write of private content —— single entry point (audit 2026-09-21: multiple places writing private data with default umask).

    Convention: parent directory 0700 (except host-owned directories where None can be passed) + tmp created with 0600 (no "write first then
    "write then chmod") + atomic replace. Credentials/pointers/email bodies/snapshots/thread summaries
    all go through here, no longer using `tmp.write_text(...)` individually (default umask produces 0644).
    """
    _ensure_private_dir(path.parent, ensure_dir_mode)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    tmp.replace(path)


def append_private(path: Path, text: str, ensure_dir_mode: Optional[int] = 0o700) -> None:
    """Private log append (logs cannot be atomically replaced): tighten directory + create file with 0600."""
    _ensure_private_dir(path.parent, ensure_dir_mode)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(text)


def set_agent_context(agent_id: str, system_id: str = "") -> None:
    """Attach current agent's config to public core injection point (platform-agnostic, fallback for MCP service).

    Original OpenClaw version (reads ~/.openclaw/.agentmail pointer + AIMAIL_AGENT_EMAIL) promoted to
    shared implementation: iterates systems/{sid}/*/agentmail.json to match agent_id, upon hit sets
    _CONFIG_LOADER and AIMAIL_AGENT_EMAIL (log location), shared by preprocess and 6 tools.
    Unregistered → RuntimeError (consistent with OpenClaw original semantics).
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
    """agent address → directory name: replace non [A-Za-z0-9_.-] characters with _.

    Email addresses are 7-bit ASCII (RFC 5321), re.ASCII makes \\w degrade to [a-zA-Z0-9_],
    Any non-ASCII character (theoretically should not occur) is also normalized to _, aligning 1:1 with TS cleanAddr(/[^\\w.-]/g)
    1:1 alignment, and filesystem paths are always ASCII-safe.
    Same naming rule as the bridge top-level agent directory (mike_aimail.token.tm).
    """
    return re.sub(r"[^\w.\-]", "_", addr, flags=re.ASCII)


def _agent_config_path(system_id: str, email: str) -> Path:
    """Address key per-agent config path: systems/{sid}/{cleaned_addr}/agentmail.json."""
    return _aimail_system_dir(system_id) / _clean_agent_dir_name(email) / "agentmail.json"


def route_agent_for_email(registry: dict, email: str) -> str:
    """Recipient address → agent_id (exact match + persona prefix stripping: support.alice@… → alice@…).

    Shared for multi-recipient inbound routing (single-inbound multi-outbound platforms like OpenClaw/DeerFlow).
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
    """Assemble the enriched AIMail payload into the agent input message.

    Aligns with Hermes webhook.py empty template fallback rendering semantics
    (json.dumps(payload, indent=2)[:4000]), keeping consistency across platforms.
    """
    return json.dumps(payload, indent=2)[:4000]


def _read_pointer(pointer: Path) -> dict:
    """Read the {dir}/.aimail pointer ({system_id, email}). Returns {} when missing/corrupt."""
    if pointer.is_file():
        try:
            return json.loads(pointer.read_text())
        except Exception:
            pass
    return {}


def _write_pointer(pointer: Path, system_id: str, email: str) -> None:
    """Write {dir}/.aimail pointer (single source of truth for system identity).

    Audit D2: The original implementation uses the default umask (measured 0664 files / 0775 directories) + non-atomic write ⇒
    Unified to use atomic_write_private (dir 0700, tmp 0600, atomic replace).
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
    """board credential storage (shared default implementation): writes per-agent board_creds.json.

    Shared by all platforms. Previously a sink injected by the Hermes adapter layer (OpenClaw did not inject → credential
    silently not persisted), now promoted to the shared core as the default implementation, so platforms get persistence without injection.
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
        # token is as sensitive as api_key: dir 0700 + tmp 0600 + atomic replace (Audit D2:
        # original mkdir had no mode, so if this function created the dir first, it would be wider than 0700).
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
    """email → system_id (three-layer convergence layout systems/{sid}/{addr}/… ownership resolution, 2026-09-23).

    Order: 1) Active profile config (when email matches, zero scan)
          2) Scan {home}/systems/*/{cleaned}/agentmail.json landing points (authoritative mapping,
             covers any address, e.g., ping recipients)
          3) the platform pointer .agentmail system_id as fallback
    Return "" on parse failure —— caller falls back to systems/_unassigned/, keeping the three-layer layout pure
    (never falls back to old top-level logs/ or mail/).
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

    Three-layer convergence (2026-09-23 decision): Each agent's logs land in the agent layer, in the same directory as agentmail.json
    Same directory —— {home}/systems/{system_id}/{cleaned_addr}/agentmail.log;
    No more top-level logs/ (original logs/aimail.{cleaned_addr}.log is deprecated).
    If system_id resolution fails, converge to systems/_unassigned/ (do not use old paths).
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
            # System does not support persona: normalize recipient address to base address (strip persona prefix),
            # No configuration validation or derived address retention — the agent identity is the base address for registration.
            result["my_aimail_addr"] = agent_email
        else:
            # Validate persona against configured personalities
            configured = list_personas()
            if persona in configured:
                result["my_aimail_addr"] = my_to_addr
            else:
                logger.warning("[aimail_gateway] Persona '%s' not found in agent.personalities — falling back to base address", persona)
                # Persona not configured: strip persona prefix, fall back to registered base address (idempotent with creation side)
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

    # ── a2a_board: [WhoAmI] inquiry detection ──
    subject = (payload.get("subject") or "").strip()
    if subject.upper().startswith("[WHOAMI]"):
        ctx = build_ctx(result, dict(headers), config)
        whoami_raw = _read_role_file("whoami")
        if whoami_raw:
            result["_whoami_prompt"] = fill_template(whoami_raw, ctx)
        return result

    # ── B3: Role_Calibrator (persona update request / welcome guidance) ──
    # Two identification forms after 2026-09-22 merge:
    #   (a) Security officer instruction email: subject contains "update persona" (role update
    #       instructions, production flow, long-term retention — adjudicated 2026-10-23, not temporary compatibility)
    #   (b) Merged welcome: **subject marker + body three tags hit simultaneously**
    #       - Subject (lowercased) contains "welcome to aimail world"
    #         (generated by gateway: "Welcome to AIMail World, {agent}, since {date}!")
    #       - persona: / signature: / current_time: tags appear at the start of body lines
    # Hit ⇒ inject Role_Calibrator(SOUL + skills auto-filled by build_ctx) and return early, to avoid being overridden by board role.
    # Only one hit ⇒ do not inject + WARN, making template drift visible (do not silently degrade to default prompt).
    _subj = subject.lower()
    _body_lc = (result.get("body") or "").lower()
    _welcome_marker = "welcome to aimail world" in _subj
    _labels_ok = all(
        any(ln.lstrip().startswith(f"{k}:") for ln in _body_lc.splitlines())
        for k in ("persona", "signature", "current_time")
    )
    if "update persona" in _subj or (_welcome_marker and _labels_ok):
        ctx = build_ctx(result, dict(headers), config)
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

    # ── a2a_board: Board context detection (injected by Rust A2aInterceptor with board_id / board_role) ──
    board_id = result.get("board_id")
    board_role = result.get("board_role")
    if board_id and board_role:
        ctx = build_ctx(result, dict(headers), config)
        role_raw = _read_role_file(board_role, allow_common=True)
        if role_raw:
            result["_role_prompt"] = fill_template(role_raw, ctx)
        sender = result.get("from", "")
        result["_a2a_session_key"] = f"a2a:{board_id}:{sender}"

    # ── L4 gateway header → L5 local prompt_rules (ruling 2026-09-23) ──
    # Chain order (stop on first hit, only recognize one hit): [WHOAMI] > welcome > board >
    # X-AIMail-Prompt (gateway stamps header after determining "hit", value = filename without .md/no sequence number)
    # > local custom (name alphabetical order). Stop if any layer injection succeeds; if hit but file missing
    # ⇒ WARN and continue down (never block delivery).
    if not result.get("_role_prompt"):
        ctx = build_ctx(result, dict(headers), config)
        injected = False
        # header dual source (gateway stamp position not locked = extension point): email header payload.headers preferred,
        # HTTP header parameter fallback; key case-insensitive, empty value treated as not provided.
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
                    continue  # Stop on first hit: if not hit, continue evaluating next by name order
                raw = _read_role_file(str(rule["file"]))
                if not raw:
                    # Hit but file missing ⇒ WARN and continue (approved semantics: do not block, do not inject)
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
    # push payloads carry "from"; the pull body may carry only "sender".
    from_addr = payload.get("from") or payload.get("sender") or ""
    if ".a2a@" not in from_addr and not subject.startswith("[A2A]"):
        return
    token_match = re.search(r'Token:\s*(bdt_\S+)', body)
    gw_match = re.search(r'API:\s*(https?://\S+)', body)
    if not gw_match:
        return
    gateway_url = gw_match.group(1).rstrip()
    # Board ID: the invite states it — authoritative. Fallback = the gateway's
    # derive_board_id: sha256(FULL board address)[:20]. The address carries the
    # MAIL domain of the sender, never the gateway host — they differ whenever
    # the gateway is not served on the mail domain (that mismatch used to key
    # board_creds.json with an id the gateway never knew, so every board call
    # answered 401).
    bid_match = re.search(r'Board ID:\s*([0-9a-f]{20})', body)
    addr_match = re.search(r'([A-Za-z0-9._\-]+\.a2a@[A-Za-z0-9._\-]+)', from_addr)
    board_email = addr_match.group(1).lower() if addr_match else ""
    board_id = bid_match.group(1).lower() if bid_match else ""
    if not board_id and board_email:
        board_id = hashlib.sha256(board_email.encode()).hexdigest()[:20]
    if not board_id:
        return
    _register_board_gateway(board_id, gateway_url)
    if token_match:
        token = token_match.group(1).rstrip()
        _store_board_credential(board_id, gateway_url, token)

# ═══════════════════════════════════════════════════════════════
# Lifecycle common chain (register/unregister agent address — unified across agent systems,
# shared by Hermes adapter layer and OpenClaw registration scripts, revisions only change here)
# ═══════════════════════════════════════════════════════════════

def email_for_agent(agent_id: str, domain: str, system_name: str = "",
                    default_aliases: tuple = ("default",)) -> str:
    """agent address derivation (cross-system unified rules + pre-registration compliance cleaning).

    1. Default name normalization: **each system's own default agent name** → "agent"
       (Hermes passes ("default",), OpenClaw passes ("main",); they do not replace each other——Hermes's
       "main" profile remains "main", OpenClaw's "default" agent remains "default").
    2. Illegal character cleaning (applies to **original address name** base segment, excluding shared domain system_name identifier):
       - '.' → '_' **strictly unified across all systems** (dot is persona separator reserved slot + gateway dot rule:
         shared exactly 1 dot / non-shared 0 dots, base containing dot must be rejected; unrelated to persona support)
       - Other non atext-no-dot characters → '_' (charset = gateway is_atext_no_dot)
       - If empty after cleaning → fallback to "agent"
    """
    base = "agent" if agent_id in default_aliases else agent_id
    # Strict cleaning: non atext-no-dot characters (including '.') → '_'; empty result fallback to "agent"
    cleaned = re.sub(r"[^A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]", "_", base)
    base = cleaned or "agent"
    if system_name:
        return f"{base}.{system_name}@{domain}"
    return f"{base}@{domain}"


def plan_address_name(requested_name: str, *, agent_id: str, domain: str,
                      system_name: str = "", aliases=(),
                      register_argv=()) -> dict:
    """Single source of truth for "naming" in registration orchestration (owner ruling 2026-10-02 P6-A / C1).

    input = intent + mapping_layer_data(platform_registry(typed_constants): alias_table/registration_template argv —— by
    CLI as mapping layer); all rules in SDK, CLI only "get plan → execute":
      1. Validation: requested name must be atext-no-dot (raise ValueError if invalid, CLI _fail presents);
      2. Alias normalization: platform internal id (openclaw main / hermes default) is not address base name → "agent";
      3. Direct hit determination (F13 root): registration template contains {name}/{email} ⇒ registrar accepts address name → direct target
         (needs_rename=False); otherwise register with platform default name first, must rename after registration to close loop
         (needs_rename=True —— intermediate state leaves orphan rows without local bindings at the gateway);
      4. Email derivation: email_for_agent (single source of truth for rules).
    Returns:
      default_name : platform default base name (after alias normalization)
      target_name  : target base name (requested name normalized, no requested name = default_name)
      reg_as       : base name to use for this registration (reachable ⇒ target_name, otherwise default_name)
      needs_rename : reg_as != target_name ⇒ rename required after registration to close loop
      email        : full address landed for this registration (email_for_agent(reg_as))
      target_email : final target address (email_for_agent(target_name))
    """
    name = requested_name or ""
    # Verbatim equivalent to the old CLI: fullmatch on the **original request** (including empty string ⇒ invalid), message unchanged
    if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]+", name):
        raise ValueError(f"illegal address name '{name}': must be atext-no-dot characters (no dot/space/@)")
    als = tuple(aliases or ())
    plat_default = (agent_id or "").strip() or "agent"
    default_name = "agent" if plat_default in als else plat_default
    target_name = ("agent" if name in als else name) if name else default_name
    accepts_target = any(("{email}" in str(x)) or ("{name}" in str(x))
                         for x in (register_argv or ()))
    reg_as = target_name if (accepts_target and name) else default_name
    return {
        "default_name": default_name,
        "target_name": target_name,
        "reg_as": reg_as,
        "needs_rename": reg_as != target_name,
        "email": email_for_agent(reg_as, domain, system_name, default_aliases=()),
        "target_email": email_for_agent(target_name, domain, system_name,
                                        default_aliases=()),
    }


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
# owner ruling 2026-09-28 (SDK de-bridging): routing/bridge = **CLI's environmental responsibility** (cli/bridge_wire.py).
# SDK only reports the two facts it truly knows — every address of this host service, inbound "in service"/"stopped": 
#     aimail address -a <addr> --inbound-live | --inbound-down
# Contract (item-by-item isomorphic with TS side mail-core/src/inbound-notify.ts):
#   · Binary parsing AIMAIL_BIN → ~/.aimail/bin/aimail → aimail in PATH; **none found ⇒ one line
#     skip after debug** —— the machine SDK without CLI is still self-contained (binds/serves/self-registers as usual).
#   · **argv passing (no shell)**, timeout 3–5s, non-0/exception ⇒ **continue after one log line**: never block
#     host, no retry storm, never throw to caller.
#   · Payload contains only address (zero-bridge semantics: no mention of bridge, no mention of port, no mention of protocol).

INBOUND_NOTIFY_TIMEOUT = 4.0
INBOUND_STATE_FLAG = {"live": "--inbound-live", "down": "--inbound-down"}


def resolve_aimail_bin() -> str:
    """AIMAIL_BIN → ~/.aimail/bin/aimail → PATH `aimail`; '' = no CLI on this machine."""
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
                         timeout: float = INBOUND_NOTIFY_TIMEOUT, runner=None,
                         url: str = "") -> dict:
    """best-effort notify the environment master (CLI) of the inbound status of a certain address. **Never throws, never blocks the caller**. 

    runner is the test injection point, signature ``(bin, args, timeout) -> (rc, detail)``; default uses
    subprocess(argv, no shell). Returns ``{"state": "notified"|"no_cli"|"failed",
    "email", "bin", "detail"}``.

    ``url`` = the confirmed inbound URL (contract §4.3): the URL this listener **actually serves**. Only when the host knows its own
    specific service address (loopback default is specific; wildcard binding is not) —— when non-empty, argv inserts ``--inbound-url <url>`` **before the status flag**
    Insert ``--inbound-url <url>`` (flag is always the last item, verbatim compatible with the no-URL form).
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
    # Confirmed URL (§4.3): inserted before the state flag ⇒ flag is always the last argv item; empty ⇒ form is verbatim identical to pre-§4.3.
    confirmed = str(url or "").strip()
    args = ["address", "-a", addr]
    if confirmed:
        args += ["--inbound-url", confirmed]
    args.append(flag)
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
    except Exception as e:  # noqa: BLE001 — notification is best-effort, never bubbles up
        return {"state": "failed", "email": addr, "bin": binary, "detail": str(e)}


def notify_inbound_for_system(state: str, system_id: str = "",
                              timeout: float = INBOUND_NOTIFY_TIMEOUT,
                              runner=None, url: str = "") -> list:
    """Notify inbound state once for **each address** of this system (host hook payload). Never throws, no bindings ⇒ [].

    ``url`` = confirmed inbound URL (§4.3), passed through to ``notify_inbound_state`` for each address.
    """
    rows = iter_agentmail_configs(system_id)
    out = []
    for c in rows:
        email = str((c or {}).get("email") or "").strip()
        if not email:
            continue
        out.append(notify_inbound_state(email, state, timeout=timeout, runner=runner, url=url))
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
# agent-scope scheduled polling entry (pull-entry) —— the last wire connected at the end of adapter install/initialization
# ═══════════════════════════════════════════════════════════════
# Background: the agent-scope key redeemed via address-level activation code **cannot get push** —— the gateway writes for that address
# `webhook_url = NULL` for that address, so it can only pull on a schedule. Library capabilities (pull_list/pull_ack/
# start_polling, aimail_tools) have long existed but had **zero callers** ⇒ "agent autonomous access" was missing the last hop in production.
# This section is that wire, shared by two adapters (hermes / deer-flow) with the same implementation, semantics aligned item-by-item with
# TS mail-core `src/poll-entry.ts`:
#
#   - **Enabled only for agent-level activation artifacts**: binding system_id starts with `shared_addr_`.
#     This is a ready-made fact on the gateway side (only activation codes under that host system can be redeemed —— advanced
#     `src/advanced/api/address.rs`: `if !sid.starts_with("shared_addr_")
#     { return Ok(Err(ActErr::Invalid)); }`), platform registration/product creation paths **never** produce
#     that prefix ⇒ system/bridge bindings (using push) will not be misjudged.
#   - **No ack on failure / deduplication**: guaranteed by start_polling (on_email throws ⇒ no ack +
#     re-fetch next round; dedup key = delivery id).
#   - **Stoppable / configurable interval**: one loop per binding, stop() is idempotent; interval/batch/master switch via
#     env (names same as TS: AIMAIL_PULL / AIMAIL_PULL_INTERVAL_MS / AIMAIL_PULL_LIMIT)
#     or explicit override; thread daemon ⇒ stops on process exit (does not delay exit).
#   - **pull and push share the same inbound chain**: delivery is bounced back to the host's own inbound as the original push request
#     endpoint (same path + same HMAC header) ⇒ enrichment/persona/attachments/6-step reply protocol all take effect.
#   - **Local secret self-supplied** (2026-09-27 defect 7 fix A): "replay" requires (url, secret) pair
#     —— url has a fallback if missing (host constructs it per contract constants), secret originally **had no** fallback ⇒ address-level activation
#     resulting bindings (not containing the two webhook fields) are all skipped as `no-secret`, the pull loop simply does not start.
#     Now both legs are covered: ① address-level activation persists with the local secret
#     (aimail_tools.activate_address_code_persist); ② old bindings persisted before the upgrade are
#     **self-provisioned in place** (``ensure_binding_webhook_secret``, idempotent: existing ⇒ no overwrite).
#     Semantic boundary: this secret is only used for the signature of the local hop where the "agent replays pulled mail to **its own** local inbound endpoint"
#     This local hop's signature is neither issued by nor needs to be known to the cloud ⇒ **no gateway request, no cloud contract change**.

#: System id prefix for address-level activation artifacts (gateway-side fact, see above).
AGENT_SCOPE_SYSTEM_PREFIX = "shared_addr_"

#: Default polling interval (30s) / batch size per round (gateway limit 200, pull_list converges on its own).
DEFAULT_PULL_INTERVAL_MS = 30_000
DEFAULT_PULL_LIMIT = 20

#: Interval lower bound 1s (denser is just hitting the gateway, no business reason) / upper bound 24h.
MIN_PULL_INTERVAL_MS = 1_000
MAX_PULL_INTERVAL_MS = 24 * 3600_000

#: Master switch / interval / batch environment variables (same names as TS poll-entry.ts, for documentation consistency).
PULL_ENABLE_ENV = "AIMAIL_PULL"
PULL_INTERVAL_ENV = "AIMAIL_PULL_INTERVAL_MS"
PULL_LIMIT_ENV = "AIMAIL_PULL_LIMIT"
PULL_MODE_ENV = "AIMAIL_PULL_MODE"

#: Long-pull / push contract (gateway types.rs single source; ratchet-pinned in tests).
#: hold = gateway max hold per long-pull round; client HTTP timeout must exceed it.
PULL_HOLD_MAX_MS = 50_000
PULL_HTTP_TIMEOUT_S = PULL_HOLD_MAX_MS // 1000 + 15  # 65s > 50s hold
PULL_STREAM_READ_TIMEOUT_S = 90  # per-read bound on the SSE socket
PULL_STREAM_PING_TIMEOUT_S = 30  # no frame (incl. ping) within ⇒ stream stalled
PULL_REPROBE_INTERVAL_S = 600  # Poll ⇒ re-probe /stream every 10 min


def _env_flag_off(raw) -> bool:
    """Master switch check: 0/false/off/no (case-insensitive) ⇒ off. Default = on."""
    if raw is None:
        return False
    return str(raw).strip().lower() in ("0", "false", "off", "no")


def parse_pull_mode(raw) -> str:
    """AIMAIL_PULL_MODE ⇒ "auto" (default) / "stream" / "poll". Unknown ⇒ "auto"."""
    if raw is None:
        return "auto"
    v = str(raw).strip().lower()
    if v in ("stream", "poll"):
        return v
    return "auto"


def pull_stream_backoff_secs(streak: int) -> int:
    """Stream re-backoff: 1,2,4,8,16,30 (s). Parity with bridge/ts."""
    if streak <= 0:
        return 0
    return min(1 << min(streak - 1, 5), 30)


def pull_poll_backoff_secs(interval_s: int, failures: int) -> int:
    """Poll failure backoff: interval_s * 2^failures, cap 300s (s).

    Exact mirror of bridge ``poll_backoff_secs(poll_interval, failures)``:
    ``interval * 2^failures``, cap 300s (no lower bound: interval 0 ⇒ backoff 0).
    """
    return min(max(0, int(interval_s)) * (1 << min(max(0, failures), 6)), 300)


def pull_decide_next_mode(override_mode: str, probe, stream_fail_streak: int):
    """Pure state-machine decision (three-client parity; probe ∈ {None, 'ready', 'unavailable'}).

    Returns (mode, delay_s): mode ∈ {"stream", "poll"}.
    - override "poll" ⇒ poll immediately (kill switch)
    - probe ready ⇒ stream now; probe unavailable ⇒ poll (re-probe on schedule)
    - probe None (last probe outcome): override "stream" ⇒ stay stream w/ backoff;
      streak >= 3 ⇒ downgrade to poll; else retry stream w/ backoff
    """
    if override_mode == "poll":
        return ("poll", 0)
    if probe == "ready":
        return ("stream", 0)
    if probe == "unavailable":
        return ("poll", 0)
    if override_mode == "stream":
        return ("stream", pull_stream_backoff_secs(stream_fail_streak))
    if stream_fail_streak >= 3:
        return ("poll", 0)
    return ("stream", pull_stream_backoff_secs(stream_fail_streak))


def _clamp_int(raw, default: int, lo: int, hi: int) -> int:
    """Numeric clamping (same order as TS clampInt: invalid/non-positive ⇒ default; otherwise truncate then clamp to [lo, hi])."""
    try:
        if raw is None or isinstance(raw, bool):
            raise ValueError(raw)
        n = float(str(raw).strip()) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError):
        return int(default)
    if n != n or n <= 0:  # NaN / non-positive ⇒ default
        return int(default)
    return int(max(lo, min(int(n), hi)))


def is_agent_scope_binding(cfg) -> bool:
    """Is this binding an artifact of address-level activation (agent scope) — determined by existing facts, not guesswork."""
    if not isinstance(cfg, dict):
        return False
    sid = str(cfg.get("system_id") or "")
    if not sid or not str(cfg.get("api_key") or ""):
        return False
    return sid.startswith(AGENT_SCOPE_SYSTEM_PREFIX)


def resolve_agent_pull_settings(cfg, env=None, overrides=None) -> dict:
    """Parse whether a binding "should poll / how to poll".

    Priority: explicit override > env > default. ``enabled`` is bound to agent-scope by default as
    true for agent-scope bindings (that is their **only** inbound path), and is never true when there are **no** agent-scope bindings.
    reason ∈ {enabled, no-binding, not-agent-scope, disabled-by-config, paused} (logs and
    unit tests assert on this, same set as TS PullDecisionReason).
    """
    env = os.environ if env is None else env
    ov = overrides or {}
    interval_ms = _clamp_int(
        ov.get("interval_ms", env.get(PULL_INTERVAL_ENV)),
        DEFAULT_PULL_INTERVAL_MS, MIN_PULL_INTERVAL_MS, MAX_PULL_INTERVAL_MS)
    limit = _clamp_int(
        ov.get("limit", env.get(PULL_LIMIT_ENV)), DEFAULT_PULL_LIMIT, 1, 200)
    mode = ov.get("mode") or parse_pull_mode(env.get(PULL_MODE_ENV))
    out = {"enabled": False, "reason": "no-binding",
           "interval_ms": interval_ms, "interval": interval_ms / 1000.0,
           "limit": limit, "mode": mode}
    if not cfg:
        return out
    if not is_agent_scope_binding(cfg):
        out["reason"] = "not-agent-scope"
        return out
    if cfg.get("paused") is True:
        # Local lifecycle switch (address pause): the mailbox stays bound
        # (key intact) but this profile must not poll it — purely local,
        # no cloud involvement.
        out["reason"] = "paused"
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
    """All agent-scope bindings (empty system_id = all systems; synonymous with TS listAgentConfigs)."""
    return [c for c in iter_agentmail_configs(system_id) if is_agent_scope_binding(c)]


def resolve_inbound_replay_target(cfg, default_url: str = "",
                                  route_secret: str = "",
                                  prefer_route_secret: bool = False) -> dict:
    """Resolve the (url, secret) needed to "bounce this pulled delivery back to the host inbound endpoint".

    - url: The binding's own ``webhook_url`` (local receiving endpoint persisted by the registration chain = single source of truth) takes priority;
      missing ⇒ ``default_url`` (host's current inbound endpoint, constructed by the adapter from contract constants).
    - secret: The binding's own ``webhook_secret`` (same source as registration chain and webhook routing) takes priority,
      fallback to ``route_secret`` (same-named secret in the host routing table).
    - ``prefer_route_secret=True``: For platforms where the host performs **out-of-process signature verification** (hermes inbound is verified by the host
      the webhook platform verifies with the secret in ``webhook_subscriptions.json``) — the **route table** is authoritative
      — it is the live signature truth; the same-named field in the binding is only a registration-time copy (no difference when both share a source).
      Consequence of not doing this: address-level activation self-provisions a new secret for the binding while the host routing still has the old value ⇒
      replay signature and verifier mismatch ⇒ 401 and never ack (mail stuck at gateway). deer-flow is the opposite:
      inbound handler **reads the binding itself** to verify signature (aimail_inbound.py) ⇒ binding is the truth, keep default.

    Returns ``{"url", "secret", "ok", "reason"}``; when ``ok=False``, reason ∈
    {no-url, no-secret} —— the caller uses this to **not start** the binding (does not pretend it can deliver).
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


# ── Local inbound secret self-provisioning (address-level activation + old bindings)─────────────────────────
 #: bytes used for generation — **same mechanism / same format** as the system-level install chain:
#: hermes ``aimail_hermes.py`` and deer-flow ``manage.py`` both use
#: ``secrets.token_hex(32)`` (⇒ 64 hex digits). Here we just converge "generation" into a single entry point,
#: not creating a new mechanism.
WEBHOOK_SECRET_HEX_BYTES = 32


def new_webhook_secret() -> str:
    """Generate a local inbound signature secret (reusing existing installation chain mechanism and format)."""
    return secrets.token_hex(WEBHOOK_SECRET_HEX_BYTES)


def binding_config_path(cfg):
    """On-disk path of the binding — the ``_config_path`` injected when ``iter_agentmail_configs`` reads it in."""
    if not isinstance(cfg, dict):
        return None
    raw = str(cfg.get("_config_path") or "").strip()
    return Path(raw) if raw else None


def existing_binding_webhook_secret(system_id: str, email: str) -> str:
    """The local secret in a persisted binding (empty string if not persisted / field missing / unreadable ⇒ "").

    Use it to reuse the existing secret when address-level activation is persisted to disk (**do not overwrite** —— overwriting would cause hosts that have already been reconciled
    inbound routing key).
    """
    try:
        d = json.loads(_agent_config_path(system_id, email).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — treat as absent if unreadable (caller will generate a new one)
        return ""
    if not isinstance(d, dict):
        return ""
    return str(d.get("webhook_secret") or "").strip()


def write_binding_config(path, cfg) -> None:
    """Atomically rewrite a binding in the **existing binding format** (private 0600).

    The format is byte-for-byte identical to ``save_agent_config`` / TS ``saveBinding`` (JSON indent=2,
    UTF-8 original, trailing newline, tmp+rename, 0600 —— use ``atomic_write_private``, i.e., private
    persistence, eliminating any global-readable window from "write then chmod"). Internal fields (prefixed with ``_``,
    e.g., ``_config_path``) are not persisted; ``webhook_secret`` follows ``api_key`` (consistent with
    ``saveBinding`` has the same relative position of webhook_url/webhook_secret to api_key).
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
    """**Self-provision** a local webhook secret for a binding in-place and persist it (idempotent: if exists ⇒ do not overwrite).

    purpose = "pure address-level (no system-level installation)" pull loop pre-start **defensive correction**: dropped before upgrade
    before the upgrade lack webhook_secret, while replay requires (url, secret) pairs ⇒ provision a local secret in-place
    and write it back to the binding, allowing the loop to actually start (instead of being permanently skipped by ``no-secret``).

    Does not request the gateway or change the cloud contract: this secret only signs the hop "back to its own local inbound endpoint".

    Returns ``{"changed", "secret", "path", "reason", "detail"}``, where reason ∈
    {exists (already present, untouched), provisioned (generated and persisted this time), no-path (binding has no persistence location,
    e.g. memory-constructed cfg —— not pretending success), write-failed}.
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
    src["webhook_secret"] = secret          # In-place update: immediately visible to target_for in the same round
    try:
        write_binding_config(p, src)
    except Exception as e:  # noqa: BLE001 — self-provisioning failure just reverts to "not provisioned", no exception raised
        src.pop("webhook_secret", None)     # Do not leave a fake value in memory that was not persisted
        return {"changed": False, "secret": "", "path": str(p),
                "reason": "write-failed", "detail": e.__class__.__name__}
    return {"changed": True, "secret": secret, "path": str(p),
            "reason": "provisioned", "detail": ""}


# ═══════════════════════════════════════════════════════════════
# Address-level local lifecycle (pause / resume / unbind-archive /
# re-bind restore) — owner rulings 2026-10-27.
#
# Purely local: the cloud key stays `active` the whole time (hash-only
# storage server-side; the plaintext key exists ONLY in this binding
# file). "Unbinding" therefore CANNOT destroy the file — it ARCHIVES
# it (same-directory rename, 0600 preserved) so the key bytes survive
# and a code-less re-bind restores receiving. No gateway call, no new
# endpoint.
# ═══════════════════════════════════════════════════════════════

#: Suffix that archives a binding out of the active scan (D2 ruling).
#: Derived from the contract binding filename, never spelled out alone.
BINDING_ARCHIVED_SUFFIX = ".unbound"


def archived_binding_path(path):
    """The archive name for a binding path (same directory, 0600 on rename)."""
    p = Path(path)
    return p.with_name(p.name + BINDING_ARCHIVED_SUFFIX)


def binding_alive(cfg) -> dict:
    """Current local state of a binding — the file IS the signal.

    Returns ``{"state", "path", "archived"}`` where state ∈
    {alive, paused, archived, missing}:

    - ``alive`` — the binding file is present (``paused`` flag false/absent).
    - ``paused`` — file present but its ``paused`` flag is true.
    - ``archived`` — file absent, the archived copy (binding file name +
      ``BINDING_ARCHIVED_SUFFIX``) present (unbind performed; key bytes
      still on disk).
    - ``missing`` — neither file present (never activated / destroyed).

    A running pull/stream loop re-checks this on every natural beat
    (poll interval / stream round): archived ⇒ the file is gone ⇒ stop;
    paused ⇒ stop. No cross-process hook is needed — the file itself is
    the single source of truth a CLI in another process can rename.
    """
    src = cfg if isinstance(cfg, dict) else {}
    p = binding_config_path(src)
    if p is None:
        return {"state": "missing", "path": "", "archived": ""}
    p = Path(p)
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — unreadable ⇒ treat as alive (fail closed to current behaviour)
            d = {}
        if isinstance(d, dict) and d.get("paused") is True:
            return {"state": "paused", "path": str(p),
                    "archived": str(archived_binding_path(p))}
        return {"state": "alive", "path": str(p),
                "archived": str(archived_binding_path(p))}
    a = archived_binding_path(p)
    if a.is_file():
        return {"state": "archived", "path": str(p), "archived": str(a)}
    return {"state": "missing", "path": str(p), "archived": str(a)}


def _binding_email_matches(p, email: str) -> bool:
    """Second-check guard (R2): the file's own ``email`` field must equal the
    requested address before any rename — never archive on path alone."""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return isinstance(d, dict) and str(d.get("email") or "") == email


def archive_binding(cfg, email: str = "") -> dict:
    """Unbind (D2): rename the binding file in place to the archived copy
    (binding file name + ``BINDING_ARCHIVED_SUFFIX``; atomic same-directory
    rename; 0600 preserved).

    The key bytes survive — that is what makes a code-less re-bind
    possible (cloud hash is one-way, the activation code is consumed).
    Refuses (``reason="not-bound"``) when no active file exists; when an
    archive already exists, ``reason="already-archived"`` (idempotent).
    ``email`` given ⇒ the file's own email field must match first (R2).
    No gateway request is made.
    """
    src = cfg if isinstance(cfg, dict) else {}
    p = binding_config_path(src)
    if p is None:
        return {"success": False, "reason": "not-bound",
                "path": "", "archived": ""}
    p = Path(p)
    a = archived_binding_path(p)
    if a.is_file() and not p.is_file():
        return {"success": True, "reason": "already-archived",
                "path": str(p), "archived": str(a)}
    if not p.is_file():
        return {"success": False, "reason": "not-bound",
                "path": str(p), "archived": str(a)}
    if email and not _binding_email_matches(p, email):
        return {"success": False, "reason": "email-mismatch",
                "path": str(p), "archived": str(a)}
    try:
        os.rename(p, a)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "reason": "rename-failed",
                "path": str(p), "archived": str(a),
                "detail": e.__class__.__name__}
    return {"success": True, "reason": "archived",
            "path": str(p), "archived": str(a)}


def restore_binding(cfg, email: str = "") -> dict:
    """Re-bind (D2): rename the archived copy (binding file name +
    ``BINDING_ARCHIVED_SUFFIX``) back to the active binding file and
    clear the ``paused`` flag — 0 gateway calls, cloud key untouched.

    Refuses when no archive exists (``reason="not-archived"``); when the
    binding is already active, ``reason="already-active"`` (idempotent).
    ``email`` given ⇒ the archive's own email field must match first (R2).
    """
    src = cfg if isinstance(cfg, dict) else {}
    p = binding_config_path(src)
    if p is None:
        return {"success": False, "reason": "not-bound",
                "path": "", "archived": ""}
    p = Path(p)
    a = archived_binding_path(p)
    if p.is_file() and not a.is_file():
        return {"success": True, "reason": "already-active",
                "path": str(p), "archived": str(a)}
    if not a.is_file():
        return {"success": False, "reason": "not-archived",
                "path": str(p), "archived": str(a)}
    if email and not _binding_email_matches(a, email):
        return {"success": False, "reason": "email-mismatch",
                "path": str(p), "archived": str(a)}
    try:
        os.rename(a, p)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "reason": "rename-failed",
                "path": str(p), "archived": str(a),
                "detail": e.__class__.__name__}
    # Clear the paused flag so the restored mailbox receives again
    # (pause state never survives an unbind/re-bind cycle).
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("paused") is True:
            d.pop("paused", None)
            write_binding_config(p, d)
    except Exception:  # noqa: BLE001 — restore succeeded; a stale paused flag is loud via the pull decision
        pass
    return {"success": True, "reason": "restored",
            "path": str(p), "archived": str(a)}


def set_binding_paused(cfg, paused: bool, email: str = "") -> dict:
    """Pause / resume: flip the local ``paused`` flag (atomic rewrite).

    Purely local — no gateway request, no cloud state change. Reads the
    file FRESH (not the in-memory cfg) so a concurrent writer's fields
    are not clobbered. Idempotent: flipping an already-set state reports
    ``already-paused`` / ``already-active``.
    """
    src = cfg if isinstance(cfg, dict) else {}
    p = binding_config_path(src)
    if p is None:
        return {"success": False, "reason": "not-bound", "path": ""}
    p = Path(p)
    if not p.is_file():
        return {"success": False, "reason": "not-bound", "path": str(p)}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"success": False, "reason": "unreadable", "path": str(p)}
    if not isinstance(d, dict):
        return {"success": False, "reason": "unreadable", "path": str(p)}
    if email and str(d.get("email") or "") != email:
        return {"success": False, "reason": "email-mismatch", "path": str(p)}
    if paused == (d.get("paused") is True):
        return {"success": True,
                "reason": "already-paused" if paused else "already-active",
                "path": str(p)}
    if paused:
        d["paused"] = True
    else:
        d.pop("paused", None)
    try:
        write_binding_config(p, d)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "reason": "write-failed", "path": str(p),
                "detail": e.__class__.__name__}
    return {"success": True, "reason": "paused" if paused else "resumed",
            "path": str(p)}


def clear_pointer_if_email_match(profile_dir: str, email: str) -> dict:
    """Unbind's pointer half: remove ``{profile}/{POINTER_FILE}`` ONLY when
    the pointer's own email equals the unbound address — another mailbox's
    pointer in this profile is never touched. No gateway request.
    """
    d = str(profile_dir or "").strip()
    if not d:
        return {"cleared": False, "reason": "no-profile"}
    ptr = Path(os.path.expanduser(d)) / POINTER_FILE
    if not ptr.is_file():
        return {"cleared": False, "reason": "absent"}
    try:
        data = json.loads(ptr.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"cleared": False, "reason": "unreadable"}
    if not isinstance(data, dict) or str(data.get("email") or "") != email:
        return {"cleared": False, "reason": "email-mismatch"}
    try:
        ptr.unlink()
    except Exception:  # noqa: BLE001
        return {"cleared": False, "reason": "unlink-failed"}
    return {"cleared": True, "reason": "cleared", "path": str(ptr)}


def pointer_view(profile_dir: str) -> dict:
    """The profile's pointer as a dict (``{system_id, email}``), ``{}`` when
    absent/corrupt — read-only, for the unified tool return shape."""
    d = str(profile_dir or "").strip()
    if not d:
        return {}
    return _read_pointer(Path(os.path.expanduser(d)) / POINTER_FILE)


def find_binding_by_email(email: str) -> dict:
    """Locate every LOCAL trace of one address: the active binding AND its
    archive (if any). The directory name is the cleaned address (single rule
    shared with the register chain), so no full scan is needed — the
    candidate dir is ``systems/{sid}/{cleaned}`` for each system.

    Email comparison is case-insensitive (the gateway stores the address
    lowercased; the file's own ``email`` field must match — R2 double-check).

    Returns ``{"active": cfg|None, "active_path": str, "archived_path": str}``
    (empty strings when absent). Used by the lifecycle tool to decide the
    bind mode (D3) without guessing.
    """
    base = aimail_home() / "systems"
    cleaned = _clean_agent_dir_name(email)
    want = str(email or "").strip().lower()
    active_cfg, active_path, archived_path = None, "", ""
    try:
        roots = sorted(p for p in base.iterdir() if p.is_dir())
    except Exception:
        roots = []
    for root in roots:
        cand = root / cleaned
        p = cand / BINDING_FILE
        if p.is_file():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                d = None
            if isinstance(d, dict) and str(d.get("email") or "").lower() == want:
                d["_config_path"] = str(p)
                active_cfg, active_path = d, str(p)
        a = cand / (BINDING_FILE + BINDING_ARCHIVED_SUFFIX)
        if a.is_file() and not archived_path:
            archived_path = str(a)
    return {"active": active_cfg, "active_path": active_path,
            "archived_path": archived_path}



def replay_inbound_delivery(cfg, mail: dict, url: str, secret: str,
                            timeout: float = 15.0) -> dict:
    """Send a pulled delivery back to the host inbound endpoint as the **original push request** (same chain).

    Byte-for-byte identical to requests pushed by bridge/gateway: body = envelope JSON (as-is), headers
    ``X-AIMail-Email`` + ``X-Webhook-Signature = hex(HMAC-SHA256(body, secret))``
    (same as gateway webhook.rs sign_payload).

    Failure semantics (caller uses this to **not ack** ⇒ re-pull next round, no loss): non-2xx / chain exception /
    endpoint explicitly returns "no binding for this address" (``no_agent`` / ``no_local_config``) ⇒ raise exception.
    Endpoint returns ``intercepted`` (ping/pong already consumed by shared chain and pong sent) / ``ignored`` /
    ``delivered`` / ``duplicate`` ⇒ treat as **delivered** (should ack).
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
    except urllib.error.HTTPError as e:  # noqa: PERF203 — explicit classification, do not swallow diagnostics
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
    """Polling handle for a single binding (same shape as TS AgentPullHandle: stats queryable / stoppable / awaitable for exit)."""

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
        self._stop_reason = ""  # "file" = the file-as-signal stopped it (archived/paused/missing)
        self._stats: dict = {"pulled": 0, "acked": 0, "errors": 0}

    def stop(self) -> None:
        """Stop this loop (idempotent; can stop immediately even while sleeping)."""
        self._stop_event.set()

    def stats(self) -> dict:
        """Current counter view (pulled is real-time, acked/errors are final values after loop ends)."""
        return dict(self._stats)

    def alive(self) -> bool:
        return bool(self._thread.is_alive())

    def join(self, timeout: Optional[float] = None) -> bool:
        """Wait for loop exit; True = exited."""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def __repr__(self) -> str:  # pragma: no cover - for diagnostics
        return "<AgentPullHandle %s %s every %dms>" % (
            self.email, self.reason, self.interval_ms)


class _BindingWatchedStop:
    """``stop_event`` proxy that makes the loop's OWN natural beats the
    binding-file re-check (file-as-signal, no cross-process hook).

    ``is_set()`` is true when the base event is set OR the binding file
    no longer backs this loop: archived/missing (unbind renamed it away)
    or paused (local lifecycle switch). The loop already calls ``is_set()``
    on every round and between stream chunks (SSE heartbeat ≤15s), so the
    release delay after a cross-process unbind is bounded by one natural
    beat; ``wait()`` slices the sleep into ≤0.5s steps so a paused/archived
    file also interrupts the interval sleep without a watcher thread.
    """

    def __init__(self, base_event, cfg, handle=None):
        self._base = base_event
        self._cfg = cfg
        self._handle = handle

    def is_set(self) -> bool:
        if self._base.is_set():
            return True
        # File-as-signal applies ONLY to bindings with a known
        # persistence path. A memory-constructed cfg (test seams, some
        # adapters) has no file to watch — watching would stop it on the
        # first beat (path unknown ⇒ "missing" ⇒ false file-signal).
        if binding_config_path(self._cfg) is None:
            return self._base.is_set()
        try:
            state = binding_alive(self._cfg).get("state")
        except Exception:  # noqa: BLE001 — a re-check failure must not kill the loop
            return self._base.is_set()
        if state in ("archived", "missing", "paused"):
            # Record WHY the loop is stopping: a file-signal stop is
            # re-armable (resume / re-bind re-fires the hot-add hook);
            # an explicit stop (runtime teardown, max_rounds test
            # convergence) is not.
            if self._handle is not None:
                self._handle._stop_reason = "file"
            return True
        return self._base.is_set()

    def wait(self, timeout=None) -> bool:
        if timeout is None:
            while True:
                if self.is_set():
                    return True
                self._base.wait(0.5)
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            if self.is_set():
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                return self._base.is_set()
            self._base.wait(min(0.5, left))

    def set(self) -> None:
        self._base.set()


def start_agent_pull_entry(cfg: dict, on_email=None, env=None, overrides=None,
                           log=None, client_for=None, target_for=None,
                           timeout: float = PULL_HTTP_TIMEOUT_S):
    """Start a polling loop for **one** agent-scope binding — the minimal unit for hot-add.

    Address code activation occurs after the host process starts, while ``start_agent_pull_entries`` only
    enumerates bindings once at startup ⇒ new bindings will not be picked up by already-running loops. ``start_agent_level`` calls
    this function via ``register_binding_created_hook`` after a new binding is persisted, connecting it to the same
    process, **without restart**. Semantics are fully consistent with the batch version (disabled does not raise, failure does not ack).
    """
    env = os.environ if env is None else env
    log = log or (lambda line: logger.info("%s", line))
    email = str(cfg.get("email") or "")
    settings = resolve_agent_pull_settings(cfg, env, overrides)
    if not settings["enabled"]:
        # Binding exists but judged as not enabled → leave an explanatory line (silence would mask wiring errors).
        log("[aimail-pull] %s: not polling (%s) — push/system binding unchanged"
            % (email, settings["reason"]))
        return None
    target = None
    if target_for is not None:
        target = target_for(cfg)
        if (target and not target.get("ok")
                and target.get("reason") == "no-secret"):
            # Defensive self-provisioning (idempotent): bindings created before upgrade lack local secret ⇒ add one in place and write back
            # the binding, then **re-parse once**. Bindings with existing secrets will not reach here (never overwrite).
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
            return None
    try:
        if client_for is not None:
            client = client_for(cfg)
        else:
            from aimail_tools import _GatewayClient
            client = _GatewayClient(str(cfg.get("gateway_url") or ""),
                                    str(cfg.get("api_key") or ""),
                                    timeout=timeout, identity=email)
    except Exception as e:  # noqa: BLE001 — single binding failure does not drag down the rest
        log("[aimail-pull] %s: client build failed (%s) — binding skipped"
            % (email, e))
        return None

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
    # File-as-signal: the loop re-checks its own binding file (archived /
    # paused / missing ⇒ stop) on every natural beat, so a cross-process
    # unbind (CLI rename) releases this handle without any IPC hook. The
    # proxy marks the handle when it is the file that stops the loop.
    watched_stop = _BindingWatchedStop(stop_event, cfg, handle)

    def _live(_c, mail, _h=handle, _cb=cb):  # real-time count (failures still raise ⇒ no ack)
        _h._stats["pulled"] += 1
        return _cb(_c, mail)

    def _run(_client=client, _cfg=cfg, _settings=settings,
             _live=_live, _handle=handle, _stop=watched_stop):
        stats = _client.start_polling(
            (lambda mail: _live(_cfg, mail)),
            interval=_settings["interval"], limit=_settings["limit"],
            stop_event=_stop)
        _handle._stats = dict(stats)

    thread = threading.Thread(
        target=_run, name="aimail-pull:%s" % email, daemon=True)
    handle._thread = thread
    thread.start()
    log("[aimail-pull] %s: polling enabled every %dms (limit %d, agent-scope key)"
        % (email, settings["interval_ms"], settings["limit"]))
    return handle


def start_agent_pull_entries(system_id: str = "", on_email=None, env=None,
                             overrides=None, log=None, client_for=None,
                             target_for=None, timeout: float = PULL_HTTP_TIMEOUT_S) -> list:
    """Start a real polling loop for **every agent-scope binding**, delivering to the caller's inbound chain.

    - ``on_email(cfg, mail)``: the adapter's own inbound entry point (pull and push must share the same chain).
    - ``target_for``: target resolver for pre-check + built-in callbacks. Bindings with ok=False are **not started**.
    - ``client_for``: test seam — create a request client for the binding (default _GatewayClient).
    - Returns list of handles (empty = no agent-scope bindings to poll); disabled scenarios **do not raise**.

    Unscoped call (``system_id == ""`` ⇒ the profile's pointer is missing):
    the profile's own mailbox cannot be located, so a **silent full scan is
    forbidden** (task-3 O1): exactly ONE agent-scope binding on disk starts
    (unambiguous); two or more ⇒ loud log + NO loop (mail stays pending on
    the gateway rather than being replayed into the wrong profile's inbound).
    A scoped call (``system_id`` set) is unambiguous by definition ⇒ unchanged.
    """
    env = os.environ if env is None else env
    log = log or (lambda line: logger.info("%s", line))
    handles: list = []
    unscoped_refused = False
    if system_id:
        # Scoped call: the pointer (or an explicit scope) names this profile's
        # system — per-binding decisions proceed exactly as before.
        for cfg in iter_agentmail_configs(system_id):
            handle = start_agent_pull_entry(
                cfg, on_email=on_email, env=env, overrides=overrides, log=log,
                client_for=client_for, target_for=target_for, timeout=timeout)
            if handle is not None:
                handles.append(handle)
    else:
        cfgs = [c for c in iter_agentmail_configs("")
                if is_agent_scope_binding(c)]
        if len(cfgs) == 1:
            log("[aimail-pull] no profile pointer — exactly one agent-scope "
                "binding on disk (%s); starting it (unambiguous)"
                % (cfgs[0].get("email", ""),))
            handle = start_agent_pull_entry(
                cfgs[0], on_email=on_email, env=env, overrides=overrides,
                log=log, client_for=client_for, target_for=target_for,
                timeout=timeout)
            if handle is not None:
                handles.append(handle)
        elif len(cfgs) > 1:
            unscoped_refused = True
            log("[aimail-pull] NO profile pointer and %d agent-scope bindings "
                "on disk — refusing to start: with no pointer the profile's "
                "own mailbox cannot be located, and a full scan would replay "
                "every mailbox into THIS profile's inbound (wrong routing, "
                "401, never acked). Mail stays pending on the gateway. "
                "Re-run `aimail agent bind` (or restore the pointer) to pick "
                "a mailbox; restart the host afterwards."
                % len(cfgs))
    if not handles and not unscoped_refused:
        log("[aimail-pull] no agent-scope binding — polling not started "
            "(push path unchanged; address-code activation enables it)")
    return handles


# ── binding-created hook: activate persistence → hot-add pull handle in same process (no restart needed) ──
_BINDING_HOOKS: list = []


def register_binding_created_hook(fn):
    """Register the "new binding persisted" callback; triggered after the activation path saves the binding."""
    _BINDING_HOOKS.append(fn)
    return fn


def unregister_binding_created_hook(fn) -> None:
    try:
        _BINDING_HOOKS.remove(fn)
    except ValueError:
        pass


def _fire_binding_created(cfg: dict) -> None:
    for fn in list(_BINDING_HOOKS):
        try:
            fn(dict(cfg))
        except Exception as e:  # noqa: BLE001 — hook failure does not drag down activation itself
            logger.warning("[aimail] binding-created hook failed: %s", e)


def stop_agent_pull_entries(handles) -> None:
    """Stop all handles when host service stops/process exits (idempotent)."""
    for h in handles or []:
        try:
            h.stop()
        except Exception:  # noqa: BLE001 — shutdown path does not raise
            pass
def _is_absolute_http_url(value: str) -> bool:
    """Only an absolute http(s) URL can be delivered to: the gateway hands the stored
    value straight to its HTTP client (a scheme-less ``host:port`` ⇒ builder error)."""
    v = str(value or "").strip()
    return v.startswith("http://") or v.startswith("https://")


def resolve_register_webhook_url(gw: dict, local_webhook_url: str) -> str:
    """webhook_host three states → address registration parameter webhook_url (SDK de-bridged, 2026-09-28 convergence).

    Registration values have only three sources, determined in this order —— **only look at the `webhook_host` key**:

      ① Key absent                      → local receiving endpoint for this binding (direct / standalone scenario)
      ② Explicit empty value ("", "   ")         → empty registration value = pull (cloud does not callback; empty is not a defect)
      ③ Deliverable absolute http(s) URL  → use it (push entry declared by environment master; when bridge is present
                                      CLI has written bridge's external URL to webhook_host)
      ③' Value not deliverable (bare host / host:port)→ **loud warning** + fallback to local endpoint: never register
         a value that gateway cannot POST (measured builder error); deployments with bridge should register bridge's own
         URL, not host:port.

    · Third source `webhook_register_url` self-collected via bridge response **retired**: residual disk values always ignored
      (the SDK does not write it either) — the registration value is no longer learned by the SDK from the bridge; bridge semantics do not belong to the SDK.
    · webhook_url in agentmail.json is always the local receive endpoint (a different value from the registration value).
    """
    whh = gw.get("webhook_host") if isinstance(gw, dict) else None
    if whh is None:
        # Key absent = no environment-declared callback entry ⇒ direct/standalone scenario: register local endpoint.
        return local_webhook_url
    declared = str(whh).strip()
    if not declared:
        return ""                                    # ①/② explicit empty = pull
    if _is_absolute_http_url(declared):
        return declared                              # ③ push entry (must be deliverable URL)
    logger.warning(
        "[aimail] webhook_host %r is not an absolute http(s) URL — the cloud cannot "
        "POST to it (builder error), so the local endpoint %r is registered instead. "
        "For a bridged deployment register the bridge's own URL "
        "(http://<host>/webhooks/aimail-inbound), not a bare host:port.",
        declared, local_webhook_url)
    return local_webhook_url                         # ③' not deliverable ⇒ fallback to local endpoint


# ═══════════════════════════════════════════════════════════════
# manager resolution + registration/whitelist write hard gate (P1, owner decision 2026-09-30)
# ═══════════════════════════════════════════════════════════════
# Two existing env names **mutually recognized**, no third name added: SDK contract export
# `AIMAIL_MANAGER_ADDRESS`, CLI integration contract export `INTEGRATE_MANAGER_ADDRESS`.
# Historical bare name `AIMAIL_MANAGER` has been retired (2026-10-17 owner ruling: treat as non-existent) ——
# The chaos of old three-name mutual recognition is one of the root causes of deerflow whitelist line landing as `value=''`
# (forensics p1-p2-forensics-20260930.md).
MANAGER_ENV_VARS = ("AIMAIL_MANAGER_ADDRESS",
                    "INTEGRATE_MANAGER_ADDRESS")


class ManagerRequiredError(RuntimeError):
    """Registration/whitelist write hard gate: manager resolution chain (explicit param → env name mutual recognition) completed but still empty.

    Registering with empty value creates `value=''` manager whitelist line in gateway ⇒ outbound
    `550 Sender not whitelisted`(deerflow J4e/J5-1 tested). owner ruling:
    No silent degradation allowed, must fail loudly. Paths like deregistration/read-only that do not need manager do not go through this gate.
    """


def resolve_manager_address(manager_address: str = "") -> str:
    """Explicit parameter → env name mutual recognition → return `''` if still empty (caller decides whether to raise an error)."""
    v = str(manager_address or "").strip()
    if v:
        return v
    for key in MANAGER_ENV_VARS:
        e = str(os.environ.get(key) or "").strip()
        if e:
            return e
    return ""


def require_manager_address(manager_address: str = "", where: str = "register") -> str:
    """Hard gate: if still empty after parsing ⇒ raise ManagerRequiredError(rc≠0, message specifies which part is missing)."""
    v = resolve_manager_address(manager_address)
    if not v:
        raise ManagerRequiredError(
            f"manager missing (not given via arg or env): {where} register/allowlist does not accept empty values — "
            f"Please provide explicit parameters, or set env {' / '.join(MANAGER_ENV_VARS)}"
            f"(Registration with '' is forbidden, otherwise whitelist line value='' ⇒ outbound 550 Sender not whitelisted)")
    return v


def register_agent_email(client, system_id: str, email: str,
                         webhook_url: str = "", webhook_secret: str = "",
                         manager_address: str = "") -> dict:
    """Registration chain (idempotent, 4 steps): register_email(generate_code) → update webhook if exists →
    manager whitelist → activate_address. Returns {"api_key", "activation_code"}
    (api_key empty = activation pending/exists; activation_code for delayed activation semantics).
    The domain is extracted by the gateway from the email address itself (no mx_domain/domain parameter passed since 2026-08-18).

    client must provide: register_email / list_system_domains / update_system_domain /
    activate_address (aimail_tools._GatewayClient has all; whitelist is auto-created by gateway registration interface).

    **P1 Hard Gate (2026-09-30 owner ruling)**: This is the common entry point for "registration/whitelist writing",
    install / register / reconcile all pass through this gate —— if explicit parameters and env fallback
    are still empty, raise `ManagerRequiredError`, registration with `''` is not allowed (deregistration/read-only paths
    go through `deregister_agent_email`, not here).
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
        # status=0 = transport layer failure (_request fallback), treated same as HTTP error:
        # Silently swallowing it would make caller misjudge as "activation pending" and continue the chain (audit lesson: silent failure surface)
        if not status or str(status) not in ("created", "200", "201", 200, 201):
            msg = str(result.get("error", "")) + str(result.get("detail", ""))
            if "already exists" in msg.lower() or "exists" in msg.lower():
                activation_code = ""
                # Exists → update webhook config (idempotent); skip if both values are empty——
                # Empty body update would make gateway unconditionally overwrite stored webhook_url/secret with NULL
                if not webhook_url and not webhook_secret:
                    return {"api_key": "", "activation_code": ""}
                # Missing secret must not overwrite (defense in depth for defect #10, 2026-09-28): Gateway PUT is **full
                # Override** (no webhook_secret key in body ⇒ cloud secret is written as NULL),
                # while verifier verifies against **locally bound** secret ⇒ divergence on both sides = inbound always 401.
                # So this branch first completes based on **source of truth**: if caller didn't provide ⇒ read binding for that address (sole source of truth);
                # if still missing ⇒ **do not write** (preserve current cloud value), warn loudly, never wipe to empty.
                # 2026-10-02 Defect #10 root cause (E1): **Binding priority** —— binding is the sole source of truth (Card B standard),
                # value provided by caller is only adopted when binding lacks secret. Original "parameter priority" will always diverge on deerflow:
                # One install registers 3 times (registry install_steps: sdk_install →
                # register_default → register_all), each time minting a new random value (manage.py:217/:362) ⇒
                # New value PUT overwrites gateway (storage.rs:693 full overwrite), while binding is only persisted when
                # api_key/activation_code is obtained (manage.py:224-235) ⇒ gateway=3rd key /
                # binding=1st key ⇒ D2B side digests unequal (historical 13 reds all on deerflow, other four platforms 56 green).
                _bp = _agent_config_path(system_id, email)
                _bind_secret = str(existing_binding_webhook_secret(system_id, email) or "").strip()
                secret = _bind_secret or str(webhook_secret or "").strip()
                if not secret:
                    # 2026-10-01 owner ruling (Plan B): binding lacks secret ⇒ mint one on the spot and **carry** it to gateway,
                    # never issue url-only PUT (preserve 2026-09-28 defect #10 defense: do not wipe cloud secret to NULL).
                    # Single source of truth=binding ⇒ gateway adopts same key ⇒ D2B side digests same source (fixes "two keys").
                    # Original "abstain and return" would make D2B judge BAD (sides unequal), so changed to self-healing completion; only abstain if cannot complete, and loudly.
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
                    except Exception:  # noqa: BLE001 — treat read failure as missing, completed by below
                        _bcfg = {}
                    if not str(_bcfg.get("webhook_secret") or "").strip():
                        _bcfg["webhook_secret"] = new_webhook_secret()
                        try:
                            write_binding_config(_bp, _bcfg)
                        except Exception as _we:  # noqa: BLE001 — must abstain if cannot complete (no silent divergence allowed)
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
                    # Binding file exists but lacks secret, caller provided ⇒ first write this key back to binding (complete source of truth) then PUT:
                    # otherwise gateway=parameter value, binding empty ⇒ another pair of divergences (Card B ③ only covers the half where "neither side provided").
                    # Write-back failure ⇒ do not send PUT (keep status quo, do not create new divergence) —— same standard as Card B "better to abstain".
                    _bcfg_back = {}
                    try:
                        _t = json.loads(_bp.read_text(encoding="utf-8"))
                        if isinstance(_t, dict):
                            _bcfg_back = _t
                    except Exception:  # noqa: BLE001 — treat read failure as empty config and rebuild fields
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

    # Registration chain ends here: **no step 5**. Routing is the responsibility of the environment controller (CLI) (2026-09-28 SDK de-bridging),
    # notified by the host to the CLI (`aimail address -a <addr> --inbound-live`) when inbound is actually live, then
    # the CLI decides on its own; the SDK does not write bridges, does not ask about bridges, and does not write environment configuration.
    return {"api_key": api_key, "activation_code": activation_code}


def deregister_agent_email(client, system_id: str, email: str,
                           manager_address: str = "") -> dict:
    """Deregistration chain (API part, idempotent): api-key → domain → whitelist.
    Returns the status of each step {api_key, domain, whitelist}.

    client must provide: get_api_key_by_email / delete_api_key / list_system_domains /
    delete_whitelist_by_value。
    """
    out: dict = {}
    # 1. Delete API key (look up id by email)
    try:
        k = client.get_api_key_by_email(email)
        if isinstance(k, dict) and k.get("id"):
            r = client.delete_api_key(k["id"])
            out["api_key"] = str(r.get("status", r))
        else:
            out["api_key"] = "not_found"
    except Exception as e:
        out["api_key"] = f"err:{e}"

    # 2. Delete domain entry (by id, fallback by name)
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

    # 3. Whitelist cleanup —— exact match (domain_addr==email, value==manager) then **delete by id**.
    #    Audit 2026-09-21: The original implementation used DELETE /api/v1/whitelists?domain_addr=&value=,
    #    while the gateway admin branch treats domain_addr as a **domain** (lists all system rows by domain then takes the first
    #    value hit) ⇒ passing the full address yields an empty list → 404; passing the domain may accidentally delete entries with the same manager
    #    other addresses with the same manager. Fixture test: whitelists rows remain after address deregistration.
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
            # F10(2026-09-25): Fallback when exact match fails -- all rows under this address are orphans.
            # The semantics of deregistration is "this address is dismantled" (api-key / system_domains already deleted), and
            # domain_addr==email uniquely pins the address ⇒ will not affect other agents (2026-09-21 requirement
            # value==manager was to prevent "blind deletion by value" cross-address accidents, not applicable to address-based convergence).
            # Empirically two types of misses: in openclaw bindings manager_address is empty (CLI did not export AIMAIL_*),
            # deerflow whitelist rows have empty value ⇒ matching by value alone will inevitably leave residuals.
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

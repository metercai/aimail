"""aimail_hermes.py — Hermes adapter layer (platform wiring for the public core)

The public core (pysdk/aimail_base.py / aimail_tools.py / aimail_board.py)
remains platform-agnostic, providing business logic via injection points; this module:
  1. Injects the Hermes platform implementation (config loading / personas / profile directory / SOUL / skills /
     board registration / persona context)
  2. Provides Hermes-specific features (profile lifecycle hooks / webhook routing / port management)
  3. Registers in the Hermes runtime (webhook preprocessor / tools registry / lifecycle hooks)

DeerFlow corresponding adapter layer: pysdk/deer-flow/aimail_deerflow.py
"""

import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

# ── Hermes runtime location: this module is bundled and lands in {install-root}/tools/hermes/,
 #    the shared core (aimail_base/tools/board/gateway_api) is in the parent directory. Hermes as a package
 #    loads the tools (tools.*); the top-level import needs the tools/ dir on sys.path — the parent dir
 #    detection location is agnostic (works for bundled / pip site-packages / repo dev tri-state). Directly importing
 #    this module (standalone scripts / register_profiles.py) also relies on this bootstrap.
_HERMES_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERMES_TOOLS_DIR not in sys.path:
    sys.path.insert(0, _HERMES_TOOLS_DIR)

import aimail_base as core
import aimail_tools as tools
import aimail_board as board
# agent-side contract constants (dsh/pi/deer-flow/hermes share the same copy; single source = repo root
# contract/aimail-contract.json, gate tests/contract/check-contract-single-source.py)
import aimail_contract as _contract

# ── Host identification (ported from tssdk "activate on entry load"): declared=hermes, on entry load
#    re-verify that the current environment is indeed the hermes host. Never raises (wrong host only warns, see
#    hosts/activate.py); complements CLI assembly-time detection (CLI locates uninstalled state, this confirms installed state).
#    core directory is already on sys.path (L29-31), hosts is its subpackage: pip layout uses aimail.hosts,
#    repo dev / bundle flat layout imports hosts directly.
try:
    from aimail import hosts as _aimail_hosts
except ImportError:
    import hosts as _aimail_hosts
_HOST_VERIFY = _aimail_hosts.activate_host(
    "hermes", os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))

# API client (public aimail_tools._GatewayClient all methods)
_GatewayClient = tools._GatewayClient

# ── public-function forwarding (relocated functions / bare calls inside _handle_* — same-source public core) ──
_aimail_system_dir = core._aimail_system_dir
_load_gateway_config = core._load_gateway_config
list_personas = core.list_personas
# ── Main model injection X-AIMail-Agent(profile config.yaml → model.default)──
def _init_agent_model() -> None:
    try:
        profile_dir = os.environ.get("HERMES_PROFILE_DIR", "") or str(Path.home() / ".hermes")
        cfg = Path(profile_dir) / "config.yaml"
        if cfg.exists():
            import yaml
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            model = ((data.get("model") or {}).get("default") or "")
            if isinstance(model, str) and model.strip():
                tools.set_agent_model(model.strip())
    except Exception:
        pass  # identity stays platform/version only


_init_agent_model()

# 6 Tool function bodies (registry handler wrapper bare calls)
send_mail = tools.send_mail
manage_contacts = tools.manage_contacts
contact_profile = tools.contact_profile
set_contact_profile = tools.set_contact_profile
email_summary = tools.email_summary
set_email_summary = tools.set_email_summary
search_mail = tools.search_mail
# A2A Board tool function bodies (registry registered handler bare calls)
board_task_show = board.board_task_show
board_task_list = board.board_task_list
board_members = board.board_members
board_roles = board.board_roles
board_status = board.board_status
board_heartbeat = board.board_heartbeat
_TOOLSET = _contract.AGENT_TOOLSET_NAME  # Contract value "agentmail" (not external brand aimail)

logger = logging.getLogger(__name__)

# profile lifecycle hook registry (Hermes platform exclusive)
_profile_hooks: Dict[str, List[Callable]] = {
    "profile_created": [],
    "profile_deleted": [],
}
# ═══════════════════════════════════════════════════════════════
# 1. Hermes platform implementation (Hermes exclusive functions moved out from public core)
# ═══════════════════════════════════════════════════════════════
def _read_soul_md(config=None) -> str:
    """Read SOUL.md from the current Hermes profile (per-agent).

    Anchored via _resolve_profile_dir(): HERMES_PROFILE_DIR env → live
    HERMES_HOME (contextvar-aware, per-profile — set per agent by the gateway
    spawner) → ~/.hermes. The old code checked only HERMES_PROFILE_DIR and
    fell back to the DEFAULT profile's ~/.hermes, so when the process ran with
    HERMES_HOME pointed at a profile dir it read the wrong (default) SOUL.
    """
    profile_dir = _resolve_profile_dir()
    if not profile_dir:
        profile_dir = str(Path.home() / ".hermes")
    soul = Path(profile_dir) / "SOUL.md"
    if soul.exists():
        return soul.read_text(encoding="utf-8")
    return ""


def _read_skills(config=None) -> list[str]:
    """Enumerate the skills available to this Hermes agent (per-agent).

    Single source of truth = the host's own `skills_list` tool — the
    authoritative "skills this agent can use" entry point. It resolves the
    LIVE profile-scoped skills dir (HERMES_HOME, the per-agent anchor set by
    the gateway spawner) plus config external_dirs, applies the host's
    platform/env/disabled gates and name dedup, and includes plugin-provided
    skills — exactly the list the agent itself sees.

    The old code read config.yaml's `skills:` field, which is the skills
    subsystem's *config block* (external_dirs / template_vars / ...), not a
    skill name list — so it returned that dict (joined to garbage in the
    board ctx) or [] when the key was absent. Never the real list.

    Falls back to [] when the host module is unavailable (running outside a
    Hermes process).
    """
    try:
        from tools.skills_tool import skills_list
    except Exception:
        return []
    try:
        import json
        data = json.loads(skills_list())
    except Exception:
        return []
    if not isinstance(data, dict) or not data.get("success"):
        return []
    out: list = []
    seen = set()
    for s in data.get("skills") or []:
        n = s.get("name") if isinstance(s, dict) else None
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


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


_profile_config_path = core._agent_config_path  # Address key per-agent config path (shared)


def _load_profile_config() -> Optional[dict]:
    """Load per-agent config from the address-keyed directory.

    Uses {profile_dir}/.aimail pointer → {system_id} + {email} → path:
      ~/.aimail/systems/{system_id}/{cleaned_addr}/agentmail.json
    """
    profile_dir = _resolve_profile_dir() or ""
    
    search_paths = []

    if profile_dir:
        # Pointer file (contract name single source of truth: aimail_contract.POINTER_FILE)
        pointer_data = core._read_pointer(Path(profile_dir) / _contract.POINTER_FILE)
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
    """Write per-agent aimail config.

    Address-keyed: ~/.aimail/systems/{system_id}/{cleaned_addr}/agentmail.json
    Pointer file:  {profile_dir}/.aimail  (contains system_id + email for discovery)

    Merges with existing config — preserves fields not in the new config
    (e.g. api_key from previous activation).
    """
    system_id = config.get("system_id", "")
    email = config.get("email", "")

    # Write primary config to the address-keyed directory — via the SHARED
    # atomic writer (aimail_base.save_agent_config, tmp+rename+0600, parity
    # with TS mail-core config.ts saveBinding). Merge first to preserve
    # fields like api_key from a previous registration.
    if system_id and email:
        existing = {}
        p = core._agent_config_path(system_id, email)
        if p.is_file():
            try:
                existing = json.loads(p.read_text())
            except Exception:
                pass
        merged = {**existing, **config}
        # Prevent activation_code + api_key coexistence
        if merged.get("api_key") and merged.get("activation_code"):
            merged.pop("activation_code", None)
        core.save_agent_config(config.get("agent_id", ""), merged, system_id)

    # Write pointer file (contract name see aimail_contract.POINTER_FILE)
    core._write_pointer(Path(profile_dir) / _contract.POINTER_FILE, system_id, email)


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
    # (home resolution goes through this module's resolver — occupied ports of custom HERMES_HOME instances must also enter the scan set)
    max_port = base_port - 1
    _home = Path(_resolve_profile_dir() or os.path.expanduser(
        os.environ.get("HERMES_HOME", "~/.hermes")))
    default_cfg = _home / "config.yaml"
    max_port = max(max_port, _read_webhook_port(default_cfg))
    profiles_dir = _home / "profiles"
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
            logger.warning("[aimail_gateway] Failed to read webhook config: %s", e)
    
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
        # Audit D2: Original writing `cfg_path.with_suffix(".tmp")` yields "config.tmp" (name is also wrong),
        # and writes with default umask ⇒ overwrites host's 0600 config.yaml to 0644, and leaves a copy containing
        # the config.tmp of the webhook HMAC secret. uniformly goes through core.atomic_write_private
        # (the host's own profile dir is not chmod'd ⇒ ensure_dir_mode=None).
        core.atomic_write_private(
            cfg_path,
            yaml.safe_dump(existing, allow_unicode=True, sort_keys=False),
            ensure_dir_mode=None,
        )
        return {
            "enabled": True,
            "host": "0.0.0.0",
            "port": port,
            "secret": secret,
        }
    except Exception as e:
        # Fail loudly (C-13, 2026-10-11): swallowing this produced
        # "install reports success, but platforms.webhook was never written",
        # which the harness could only report as "port not set" — the host
        # could never receive inbound mail. A missing YAML parser (PyYAML is a
        # declared dependency now) must surface here instead of degrading.
        raise RuntimeError(
            f"failed to write hermes webhook config {cfg_path}: {e}"
        ) from e


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
                "[aimail_gateway] Persona '%s' not found in agent.personalities. "
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
        "description": f"aimail inbound email route ({route_name})",
        "events": [],
        "secret": secret,
        "preprocess": "aimail_gateway",    # triggers preprocess_mail_payload
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
    # Routing table contains HMAC secret: tmp is created with 0600 (default umask 0644 is globally readable)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(subs, indent=2, ensure_ascii=False))
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
    simply skipped. On profile_created with NO system yet, the host
    auto-ensures one via the CLI reverse-call ABI (`aimail install
    --system-only`, L1 only — the single activation implementation), then
    re-loads: a fresh activation makes auto-registration work on machines that
    never ran `aimail install` (parity with the TS host plugins).
    """
    try:
        config = _load_gateway_config()
    except RuntimeError:
        config = None

    if not config and event == "profile_created":
        # Unify ensure home with runtime detection (HERMES_PROFILE_DIR > hermes
        # get_hermes_home() > ~/.hermes)——
        # Custom home will cause ownership mismatch (AUDIT-1 P1-6).
        hermhome = _resolve_profile_dir() or str(Path.home() / ".hermes")
        try:
            r = core.ensure_system(hermhome)
            if r.get("ok"):
                if r.get("activated"):
                    logger.info("[aimail_gateway] system activated via CLI: %s", r.get("system_id"))
                # Activation/reuse success → read config directly by returned sid, do not rely on pointer file
                # (aimail_contract.POINTER_FILE; 78a0262 closed-loop gap: activation does not produce pointer; AUDIT-1 P1-6)
                sid = r.get("system_id") or ""
                if sid:
                    try:
                        config = core._load_gateway_config(sid) or None
                    except Exception:  # noqa: BLE001
                        config = None
            else:
                hint = f" ({r.get('hint')})" if r.get("hint") else ""
                logger.warning(
                    "[aimail_gateway] no aimail system yet — %s%s",
                    r.get("error", ""), hint,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("[aimail_gateway] system ensure failed: %s", e)
        if not config:
            try:
                config = _load_gateway_config()
            except RuntimeError:
                config = None

    if not config:
        logger.debug("[aimail_gateway] No gateway config -- skipping hooks for %s", event)
        return

    for cb in _profile_hooks.get(event, []):
        try:
            cb(profile_name, profile_dir, config)
        except Exception as e:
            logger.warning("[aimail_gateway] hook %s for '%s' failed: %s", event, profile_name, e)


def _auto_register_email(name: str, profile_dir: str, config: dict) -> None:
    """When a new Profile is created, register its email with aimail:
    1. Create domain entry for {name}@{domain}
    2. Create activation code for the agent
    3. Ensure aimail-inbound webhook route on the gateway
    4. Inject config into profile directory
    
    The registered address is the agent's identity. Persona switching is
    handled at inbound time by parse_aimail_persona() — the aimail skill
    extracts persona from the 'to' address (persona.profile@domain format).
    """
    gateway_url = config.get("gateway_url", "")
    admin_key = config.get("admin_key", "")
    domain = config.get("domain", "")
    system_id = config.get("system_id", "")

    if not gateway_url or not admin_key:
        logger.warning("[aimail_gateway] Cannot auto-register: gateway_url or admin_key not configured")
        return
    if not domain:
        logger.warning("[aimail_gateway] Cannot auto-register: domain not configured")
        return
    if not system_id:
        logger.warning("[aimail_gateway] Cannot auto-register: system_id not configured")
        return

    client = _GatewayClient(gateway_url, admin_key)
    system_name = config.get("system_name", "") or ""
    # Address derivation uniformly uses public email_for_agent (Hermes default name default → agent; others keep original name;
    # Illegal character cleaning '.' → '_'; compliance standard validated by gateway register_address——http.rs)
    email = core.email_for_agent(name, domain, system_name)
    manager_address = config.get("manager_address", "")

    # Source of truth priority: if agentmail.json already has webhook_url/secret, reuse (aimail sole
    # trust source; profile config is Hermes runtime copy, values are same source——re-registration does not drift)
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
        # Auto-configure or read profile webhook config. _ensure_profile_webhook
        # raises when the config cannot be written; do not convert that back into
        # a silent "inbound mail disabled" state (C-13/C-5 class).
        wh_config = _ensure_profile_webhook(profile_dir)
        if not wh_config:
            raise RuntimeError(
                f"failed to configure webhook for {profile_dir} — inbound mail would be disabled"
            )
        else:
            webhook_secret = wh_config["secret"]
            wh_port = wh_config["port"]
            # Local receiving endpoint (in-process preprocess webhook route; hermes is the only
            # path exception —— contract value see aimail_contract.HERMES_INBOUND_PATH)
            local_webhook_url = _contract.hermes_inbound_url(wh_port)
            # Ensure aimail-inbound route exists (idempotent)
            # skills=[AGENT_SKILL_NAME] so webhook sessions get the agentmail
            # skill (send_mail protocol); without it the agent cannot reply by
            # email. Note: skills list = Agent internal identifier agentmail (not external brand
            # aimail); route name = hermes gateway own route name (contract HERMES_ROUTE_NAME).
            _ensure_webhook_route(
                _contract.HERMES_ROUTE_NAME, webhook_secret, profile_dir=profile_dir,
                skills=[_contract.AGENT_SKILL_NAME],
            )

    # Registration parameter tri-state (webhook_host): push=bridge public entry / pull=empty / no bridge=local endpoint
    reg_url = core.resolve_register_webhook_url(config, local_webhook_url)

    # Register the email + generate activation code (public registration chain, idempotent)
    reg = core.register_agent_email(
        client, system_id, email, reg_url, webhook_secret,
        manager_address,
    )
    logger.info("[aimail_gateway] Registered email %s (api_key=%s)",
                email, "ok" if reg.get("api_key") else "pending")
    activation_code = reg.get("activation_code", "")
    api_key = reg.get("api_key", "")

    if not activation_code:
        # Already registered: profile should have existing api_key or activation_code
        logger.info("[aimail_gateway] Email %s already registered — using existing credentials", email)
        # Don't pass activation_code to inject; merge preserves existing value
        # Check if existing config has a pending activation_code to activate
        try:
            if system_id and email:
                existing_cfg_path = _profile_config_path(system_id, email)
                if existing_cfg_path.is_file():
                    existing_cfg = json.loads(existing_cfg_path.read_text())
                    if existing_cfg.get("activation_code") and not existing_cfg.get("api_key"):
                        activation_code = existing_cfg["activation_code"]
                        logger.info("[aimail_gateway] Found pending activation_code for %s — will activate", email)
        except Exception:
            pass

    inject_cfg = {
        "agent_id": name,
        "email": email,
        "gateway_url": gateway_url,
        "domain": config["domain"],
        "system_id": system_id,
        "manager_address": manager_address,
        "save_raw_snapshots": config.get("save_raw_snapshots", True),
        "webhook_host": config.get("webhook_host", ""),
        # aimail sole trust source: webhook_url + webhook_secret persisted as pair
        # (registration chain/signature verification/routing uniformly read from agentmail.json; profile config's
        # platforms.webhook is just Hermes runtime copy, values are same source)
        "webhook_url": local_webhook_url,
        "webhook_secret": webhook_secret,
    }
    if api_key:
        inject_cfg["api_key"] = api_key
    if activation_code:
        inject_cfg["activation_code"] = activation_code
    _inject_profile_config(profile_dir, inject_cfg)

    # Inbound notification (owner ruling 2026-09-28, SDK de-bridging): hermes is the host
    # whose gateway serves this address's inbound, and the webhook route above is
    # now registered — so the SDK reports exactly that, best-effort, per address:
    #     aimail address -a <addr> --inbound-live
    # The CLI owns the environment (routes included) and decides what it means; the
    # SDK speaks no bridge. Never fatal: a missing CLI / a non-zero exit is one line.
    if local_webhook_url:
        # the confirmation URL (contract §4.3): local_webhook_url is the inbound URL the hermes gateway actually serves
        # (contract path derived, not guessed) ⇒ CLI can pin route to deterministic port based on this.
        _note = core.notify_inbound_state(email, "live", url=local_webhook_url)
        _line = f"[aimail_gateway] {core.format_inbound_notify_line(_note, 'live')}"
        if core.inbound_notify_is_warning(_note):
            logger.warning("%s", _line)
        else:
            logger.info("%s", _line)

    # Activate the profile immediately after registration.
    # register_agent_email already activated when it returned an api_key —
    # only re-activate (via activation_code) when no api_key came back.
    if activation_code and not api_key:
        try:
            _auto_activate_profile(profile_dir, config)
        except Exception as e:
            logger.error("[aimail_gateway] Activation failed for %s: %s — activation_code retained", email, e)


def _auto_activate_profile(profile_dir: str, config: dict) -> None:
    """Activate a pending profile (has activation_code, no api_key yet).

    Called by the agent startup process when it detects an activation code
    in the profile config. This ensures the raw_key is only visible to
    the agent process itself.

    Reads from the address-keyed ~/.aimail/systems/{system_id}/{addr}/ path only.
    """
    # Read system_id + email from pointer file to find address-keyed config
    sid = ""
    email = ""
    pointer_path = Path(profile_dir) / _contract.POINTER_FILE
    if pointer_path.is_file():
        try:
            pd_data = json.loads(pointer_path.read_text())
            sid = pd_data.get("system_id", "")
            email = pd_data.get("email", "")
        except Exception:
            pass

    if not sid:
        logger.warning(
            "[aimail_gateway] No system_id in %s pointer for %s — cannot activate",
            _contract.POINTER_FILE,
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

    def _persist(prof: dict) -> None:
        """Persist agentmail.json — unified shared atomic write (0600/tmp+rename,
        AUDIT-1 P1-5: previously 4 places open+json.dump direct write, non-atomic and no 0600 guarantee)."""
        core.save_agent_config(prof.get("agent_id", ""), prof, sid)

    activation_code = prof.get("activation_code", "")
    if not activation_code:
        return  # Already activated or no code

    if prof.get("api_key"):
        # Already has a key -- clean up stale activation_code
        prof.pop("activation_code", None)
        _persist(prof)
        return

    client = _GatewayClient(config.get("gateway_url", prof.get("gateway_url", "")),
                          "", timeout=5)
    result = client.activate_address(activation_code, email_address=prof.get("email", ""))
    if result.get("success") and result.get("raw_key"):
        prof["api_key"] = result["raw_key"]
        prof.pop("activation_code", None)
        prof.pop("last_activation_attempt", None)
        _persist(prof)
        logger.info("[aimail_gateway] Activated profile, api_key saved")

        # Note: old "Port refresh" (refresh bridge route on activation) and same file sync double write
        # both deleted (2026-08-18/2026-09-07)——config_path was originally address-keyed
        # agentmail.json, activation write already persisted to sole trust source; bridge route target unchanged.
    else:
        # Rate-limit retries: skip if recently attempted (avoids spamming gateway
        # with a permanently invalid activation code)
        import time as _time
        now_ts = _time.time()
        last = prof.get("last_activation_attempt", 0)
        if last and (now_ts - last) < 300:
            logger.debug("[aimail_gateway] Skipping activation retry for %s (last attempt %ds ago)",
                         config_path, int(now_ts - last))
        else:
            prof["last_activation_attempt"] = now_ts
            _persist(prof)
            logger.warning("[aimail_gateway] Failed to activate profile %s: %s",
                           config_path, result.get("error", result))


def _auto_deregister_email(name: str, profile_dir: str, config: dict) -> None:
    """When a Profile is deleted, clean up its AIMail registration
    (API deregistration chain uses public deregister_agent_email——idempotent, fills original gap)."""
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
        logger.info("[aimail_gateway] Deregistered %s: %s", email, status)
    except Exception as e:
        logger.warning("[aimail_gateway] Deregister failed for %s: %s", email, e)


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
    """Current persona name (injection point). Hermes adapter layer injects (derived from profile directory);
    OpenClaw has no persona concept → None (sending uses base address)."""
    return _PERSONA_NAME_PROVIDER() if _PERSONA_NAME_PROVIDER is not None else None


def _hermes_persona_name() -> Optional[str]:
    """Hermes version persona name: derived from profile directory (original aimail_tools implementation).
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

def _handle_search_mail(args, **_kw):
    return tool_result(search_mail(
        query=args.get("query", ""),
        scope=args.get("scope", "all"),
        since=args.get("since", ""),
        until=args.get("until", ""),
        from_=args.get("from", ""),
        limit=args.get("limit", 20),
    ))


def _handle_activate_address_code(args, **_kw):
    """Address-level self-service activation (code → own agent key). Audit D1: fills missing first-class tool on Hermes side."""
    return tool_result(tools.activate_address_code(
        code=args.get("code", ""),
        address=args.get("address", ""),
        gateway_url=args.get("gateway_url"),
    ))


# ═══════════════════════════════════════════════════════════════
# 2. Injection points for public core
# ═══════════════════════════════════════════════════════════════
core._PROFILE_DIR_RESOLVER = _resolve_profile_dir
core._CONFIG_LOADER = _load_profile_config
core._PERSONAS_PROVIDER = _list_personas
core._SOUL_PROVIDER = _read_soul_md
core._SKILLS_PROVIDER = _read_skills
core._BOARD_GATEWAY_SINK = _register_board_gateway
# Post-injection self-check: when pointer cannot be resolved, inbound preprocessing degrades (email delivered as usual, loses album/attachment download),
# leave a warn at startup, instead of reporting for every email.
core.check_profile_pointer()
# board credential storage (_store_board_credential) has been promoted to public core default implementation;
# cross-module names (store_inbound_message/_log_aimail/_GatewayClient) are already resolved by public core
# function-level import——no adapter layer injection needed.
tools._PERSONA_NAME_PROVIDER = _hermes_persona_name
_PERSONA_NAME_PROVIDER = _hermes_persona_name          # Adapter layer namespace (_current_persona_name injection point)
core.PERSONA_SUPPORTED = True  # Hermes full capability (default value, explicit declaration)

# ═══════════════════════════════════════════════════════════════
# 3. Hermes platform registration (executed in gateway process)
# ═══════════════════════════════════════════════════════════════
# 3a. webhook preprocessor (inbound enrichment)
try:
    from gateway.platforms.webhook import register_preprocessor

    register_preprocessor("aimail_gateway", core.process_inbound_mail)
    logger.info("aimail preprocessor registered with webhook gateway")
except ImportError:
    pass

# 3b. tools registry (6 email tools + board tools)
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
            name="board_roles",
            toolset=_TOOLSET,
            schema={
                "name": "a2a_roles",
                "description": "List a board's role permissions (optionally one role's members and verbs).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "board": {"type": "string", "description": "Board ID (b_ prefix)"},
                        "role": {"type": "string", "description": "Filter by role name"}
                    },
                    "required": ["board"]
                }
            },
            handler=board_roles,
            emoji="🎭",
        )

        registry.register(
            name="board_status",
            toolset=_TOOLSET,
            schema={
                "name": "a2a_status",
                "description": "Get a board's working status: goal, progress per status with assignees, and blockers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "board": {"type": "string", "description": "Board ID (b_ prefix)"}
                    },
                    "required": ["board"]
                }
            },
            handler=board_status,
            emoji="📊",
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

# 3b.2 Email tool registration (6: send_mail / contacts / summary)
# Same mechanism as the board tool: registry.register runs at import time,
# the name must match the entries in toolsets.py _HERMES_CORE_TOOLS.
try:
    registry.register(
        name="send_mail",
        toolset=_TOOLSET,
        schema={
            "name": "send_mail",
            "description": "Send an email via your aimail address. For replies, pass the original email's message_id to thread automatically (In-Reply-To/References/persona).",
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
    logger.warning("[aimail] send_mail registration failed: %s", _e)

try:
    registry.register(
        name="search_mail",
        toolset=_TOOLSET,
        schema={
            "name": "search_mail",
            "description": "Search YOUR OWN locally stored mail (offline, no network). "
                           "Matches keywords in subject/body/attachment text; filter by "
                           "mailbox (inbound/outbound/all), time window (since/until) and "
                           "sender (from). Use it to recall past conversations, find when "
                           "an event happened, or recover attachment content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Space-separated keywords (AND); empty = browse by filters only"},
                    "scope": {"type": "string", "enum": ["all", "inbound", "outbound"], "description": "Mailbox scope (default all)"},
                    "since": {"type": "string", "description": "Start date YYYY-MM-DD (inclusive)"},
                    "until": {"type": "string", "description": "End date YYYY-MM-DD (inclusive)"},
                    "from": {"type": "string", "description": "Sender substring filter (case-insensitive)"},
                    "limit": {"type": "integer", "description": "Max results 1-50 (default 20)"},
                },
                "required": [],
            },
        },
        handler=_handle_search_mail,
        emoji="🔎",
    )
except Exception as _e:
    logger.warning("[aimail] search_mail registration failed: %s", _e)

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
    logger.warning("[aimail] manage_contacts registration failed: %s", _e)

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
    logger.warning("[aimail] contact_profile registration failed: %s", _e)

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
    logger.warning("[aimail] set_contact_profile registration failed: %s", _e)

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
    logger.warning("[aimail] email_summary registration failed: %s", _e)

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
    logger.warning("[aimail] set_email_summary registration failed: %s", _e)

# 3b-bis. Identity first-class tools (Audit D1: Hermes previously registered only 13, inconsistent with the 15 declared in README/adapter-playbook
#         the declared 15 and the TS side 15/15 mismatched — filling in these two; the descriptions align verbatim with MCP TOOLS).
try:
    registry.register(
        name="activate_address_code",
        toolset=_TOOLSET,
        schema={
            "name": "activate_address_code",
            "description": (
                "Activate the AIMail mailbox your user gave you: exchange the one-time "
                "activation code (shared_a-…) for your own key, store it locally and make "
                "the other mail tools work. Address-level self-service — no CLI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "One-time activation code (shared_a-…)"},
                    "address": {"type": "string", "description": "The mailbox address it unlocks"},
                    "gateway_url": {"type": "string", "description": "AIMail gateway URL (or set AIMAIL_GW_URL)"},
                },
                "required": ["code", "address"],
            },
        },
        handler=_handle_activate_address_code,
        emoji="🔑",
    )
except Exception as _e:
    logger.warning("[aimail] activate_address_code registration failed: %s", _e)

# 3c. profile lifecycle hooks (automatic address registration/deregistration)
register_profile_hook("profile_created", _auto_register_email)
register_profile_hook("profile_deleted", _auto_deregister_email)


# ═══════════════════════════════════════════════════════════════
# 4. the agent-scope periodic polling entry (pull-entry) — the last wire hooked up at the tail of install/initialization
# ═══════════════════════════════════════════════════════════════
# Addresses redeemed via address-level activation codes **have no push path** (the gateway writes webhook_url = NULL for them),
# so they can only pull on a schedule. Decision/interval/shutdown/no-ack-on-failure all reuse the shared core
# (core. that set, semantics aligned item-by-item with TS mail-core poll-entry.ts), this section only does two
# hermes-specific things:
#   1. resolve the "bounce back to the local inbound endpoint" target — using the webhook_url /
#      webhook_secret in the binding + the profile's webhook port (**no extra information needed**);
#   2. At the end of host initialization (within the gateway process, injected by patch_webhook in webhook.py
#      aimail_hermes import site in webhook.py via ensure_agent_pull_started()), and stop it on process
#      exit (dual guarantee via atexit + daemon thread).
#
# note (deliberately not auto-started): when this module is imported by the CLI/install chain it does not start polling — that would
# spawn threads out of thin air in the install process to hit the gateway. Start/stop is explicitly handled by the **host process** at the end, same shape as the TS adapter
# (openclaw/pi's startInboundPull is called by the host plugin).
_PULL_HANDLES: List = []
_pull_lock = threading.Lock()
_pull_atexit_armed = False


def _pull_route_secret() -> str:
    """Read the secret of the contract route name in the webhook route table (written by the registration chain, same source as the binding)."""
    hermhome = _resolve_profile_dir() or str(Path.home() / ".hermes")
    subs_path = Path(hermhome) / "webhook_subscriptions.json"
    try:
        subs = json.loads(subs_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    entry = subs.get(_contract.HERMES_ROUTE_NAME) if isinstance(subs, dict) else None
    if isinstance(entry, dict):
        return str(entry.get("secret") or "")
    return ""


def _pull_replay_target(cfg: dict) -> dict:
    """Resolve the (url, secret) needed to "bounce this pulled delivery back to this process's inbound endpoint".

    ``prefer_route_secret=True``: hermes inbound signature verification is done by the **host webhook platform** according to
    done via the route secret of ``webhook_subscriptions.json`` (not by us reading the binding) — the route table
    is the live truth for signature verification; the same-named field in the binding is just a same-source copy from registration time. When they conflict, the route takes precedence,
    otherwise replay signature and verifier mismatch ⇒ 401 and never ack (when address-level activation supplies a new binding's own secret
    will hit this conflict: the host route still has the old value from install time).
    """
    profile_dir = _resolve_profile_dir() or ""
    default_url = ""
    if profile_dir:
        port = _read_webhook_port(Path(profile_dir) / "config.yaml")
        if port:
            default_url = _contract.hermes_inbound_url(port)
    return core.resolve_inbound_replay_target(
        cfg, default_url=default_url, route_secret=_pull_route_secret(),
        prefer_route_secret=True)


def _pull_system_id() -> str:
    """The system_id bound to this profile (if there is a pointer, use it to locate my own chain; otherwise scan all systems)."""
    profile_dir = _resolve_profile_dir() or ""
    if not profile_dir:
        return ""
    pointer = core._read_pointer(Path(profile_dir) / _contract.POINTER_FILE)
    return str(pointer.get("system_id") or "")


def _on_pull_email(cfg: dict, mail: dict) -> dict:
    """Mail pulled in goes through the **same** inbound chain as webhook push — the raw request is bounced back to the local endpoint.

    Path/signature headers are verbatim identical to bridge forwarding/gateway direct push (contract HERMES_INBOUND_PATH +
    per-address HMAC), so the inbound preprocessor (enrichment/persona/attachments/ping-pong interception)
    and the 6-step reply protocol all take effect. Failure raises an exception ⇒ start_polling does not ack ⇒ re-pull next round.
    """
    target = _pull_replay_target(cfg)
    if not target.get("ok"):
        raise RuntimeError(
            "no local inbound endpoint for %s (%s) — not acking"
            % (cfg.get("email", ""), target.get("reason", "")))
    return core.replay_inbound_delivery(
        cfg, mail, url=target["url"], secret=target["secret"])


def start_agent_pull(env=None, overrides=None, log=None) -> list:
    """Start agent-scope scheduled polling (idempotent: returns the handle as-is if already running).

    Only agent-scope bindings (address code activation artifacts under ``shared_addr_*`` host systems) will start the loop;
    returns an empty list if there is none — **no throw** in disabled/system scenarios.
    """
    global _PULL_HANDLES, _pull_atexit_armed
    with _pull_lock:
        if _PULL_HANDLES:
            return _PULL_HANDLES
        _PULL_HANDLES = core.start_agent_pull_entries(
            system_id=_pull_system_id(),
            on_email=_on_pull_email,
            target_for=_pull_replay_target,
            env=env, overrides=overrides, log=log,
        )
        if _PULL_HANDLES and not _pull_atexit_armed:
            try:
                import atexit
                atexit.register(stop_agent_pull)  # Stop on process exit (plus daemon fallback)
                _pull_atexit_armed = True
            except Exception:
                pass
        return _PULL_HANDLES


def ensure_agent_pull_started(env=None, overrides=None, log=None) -> list:
    """Called at the end of host initialization (idempotent, never raises): connects the "own pull wake-up path"."""
    try:
        return start_agent_pull(env=env, overrides=overrides, log=log)
    except Exception as e:  # noqa: BLE001 — wiring failure must not crash gateway startup
        logger.warning("[aimail-pull] start failed: %s", e)
        return []


def stop_agent_pull() -> None:
    """Stop all polling handles (host shutdown/process exit; idempotent)."""
    global _PULL_HANDLES
    with _pull_lock:
        if not _PULL_HANDLES:
            return
        core.stop_agent_pull_entries(_PULL_HANDLES)
        _PULL_HANDLES = []
    # Reliable teardown hook (atexit + daemon fallback) ⇒ the mirror of the live
    # notification: this gateway stops serving these addresses' inbound, so report
    # it best-effort, per address (`aimail address -a <addr> --inbound-down`).
    # The CLI owns the environment (routes included); the SDK speaks no bridge.
    # Never fatal, never blocking: one line per address.
    try:
        for _n in core.notify_inbound_for_system("down", system_id=_pull_system_id()):
            _line = "[aimail_gateway] " + core.format_inbound_notify_line(_n, "down")
            if core.inbound_notify_is_warning(_n):
                logger.warning("%s", _line)
            else:
                logger.info("%s", _line)
    except Exception as e:  # noqa: BLE001 — shutdown notification must never crash host service stop
        logger.debug("[aimail_gateway] inbound-down notification skipped: %s", e)

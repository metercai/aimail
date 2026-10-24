#!/usr/bin/env python3
"""aimail_deerflow.py — DeerFlow adapter layer.

Isomorphic with aimail_hermes (three-piece set):
  ① Platform implementation: config loading / profile directory / set_agent_context
  ② Injection point assignment + capability switch: PERSONA_SUPPORTED = False
  ③ Platform registration: identity injection (deerflow/{ver})

Shared core (pysdk/aimail_base) already provides platform-agnostic set_agent_context
(scans per agentmail.json layout), this adapter layer only does:
  - Forward shared functions (same as Hermes)
  - Inject DeerFlow identity (X-AIMail-Agent: deerflow/...)

Inbound: preprocessing completed within DeerFlow local gateway (8001) process,
aimail_inbound router directly imports this adapter layer to get injection point;
chain gateway→bridge→8001 /aimail/inbound (signature verification + preprocessing + start_run delivery).

Layout: ~/.aimail/systems/{sid}/{cleaned_addr}/agentmail.json (shared)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# ── aimail runtime core location (bundle / site-packages / repo dev; no longer depends on repo path) ──
def _aimail_bootstrap():
    """Locate aimail runtime core, assemble sys.path."""
    import importlib.util as _ilu
    _here = os.path.dirname(os.path.abspath(__file__))
    for _d in (_here, os.path.dirname(_here)):
        _p = os.path.join(_d, "_aimail_bootstrap.py")
        if os.path.isfile(_p):
            _spec = _ilu.spec_from_file_location("_aimail_bootstrap", _p)
            if _spec is None or _spec.loader is None:
                continue
            _m = _ilu.module_from_spec(_spec)
            sys.modules["_aimail_bootstrap"] = _m
            _spec.loader.exec_module(_m)
            _core = _m.ensure_core(_here)
            if _core is None:
                raise ImportError("aimail runtime core not found — set AIMAIL_RUNTIME_DIR")
            return _core
    raise ImportError("aimail runtime core not found — set AIMAIL_RUNTIME_DIR")


_aimail_bootstrap()

import aimail_base as _ab          # noqa: E402  (shared core)
import aimail_tools as _tools      # noqa: E402  (X-AIMail-Agent identity injection)

# ── Host identification (ported tssdk "activate on entry load"): declared=deerflow, entry load time
#    re-verify environment. never raises (wrong host install only warns); home semantics aligned with CLI (DEER_FLOW_HOME
#    or ~/.deer-flow; marker backend/app/gateway under it). _aimail_bootstrap has already
#    attached core to sys.path ⇒ hosts subpackage importable (pip via aimail.hosts, dev direct hosts).
try:
    from aimail import hosts as _aimail_hosts
except ImportError:
    import hosts as _aimail_hosts
_HOST_VERIFY = _aimail_hosts.activate_host(
    "deerflow", os.environ.get("DEER_FLOW_HOME") or os.path.expanduser("~/.deer-flow"))


# ── DeerFlow identity detection (only report real detection results) ───────────────────────────
def _detect_deerflow_version() -> str:
    """Detect DeerFlow version: prefer DEER_FLOW_HOME/backend/pyproject.toml,
    fallback to unknown on failure."""
    for pp in (
        os.path.join(os.environ.get("DEER_FLOW_HOME", ""), "backend", "pyproject.toml"),
        os.path.join(os.path.expanduser("~"), "deer-flow", "backend", "pyproject.toml"),
        os.path.join(os.path.expanduser("~"), "deer-flow", "pyproject.toml"),
    ):
        if pp and os.path.isfile(pp):
            try:
                with open(pp) as f:
                    for line in f:
                        if line.strip().startswith("version"):
                            v = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if v:
                                return v
            except Exception:
                pass
    return "unknown"


# ── Inject identity (same pattern as OpenClaw: directory detection can misjudge, explicit injection is required)────
_tools._AGENT_IDENTITY_OVERRIDE = f"deerflow/{_detect_deerflow_version()}"


# ── ① Platform implementation ──────────────────────────────────────────────────
def _deerflow_profile_dir() -> Optional[str]:
    """profile directory: current system directory (system_id resolution same as OpenClaw pointer)."""
    sid = os.environ.get("AIMAIL_SYSTEM_ID", "")
    if sid:
        return str(Path(_ab.aimail_home()) / "systems" / sid)
    return None


def _deerflow_soul_md(config=None) -> str:
    """SOUL.md per-agent: anchors to the agent's own address directory.

    DeerFlow 'directory is agent' (SOUL.md/agents/ — manage.py directory source of truth); SDK
    self-held unique per-agent directory = address key directory bound by save_agent_config persistence
    systems/{sid}/{cleaned_addr}/, SOUL anchored there: <dir>/SOUL.md. Missing ⇒ ''
    (agents without an authored SOUL render empty; documented). Scanning the whole agents/ tree belongs to upstream
    DeerFlow (manage.discover simplified stub, 'extended scan left for later'), not replicated here.
    """
    if not config:
        return ""
    sid = str(config.get("system_id") or "")
    email = str(config.get("email") or "")
    if not (sid and email):
        return ""
    soul = Path(_ab._aimail_system_dir(sid)) / _ab._clean_agent_dir_name(email) / "SOUL.md"
    if soul.is_file():
        return soul.read_text(encoding="utf-8")
    return ""


def _deerflow_skills(config=None) -> list:
    """skills list (per-agent). DeerFlow 'directory is agent': skills and SOUL.md
    anchored in the agent's own address directory. Enumerate subdirectories under systems/{sid}/{cleaned_addr}/skills/
    (one subdirectory per skill, same anchor as SOUL, same style as pi). Missing ⇒ []
    (do not fabricate; same shape as SOUL missing ⇒ ''). Full platform agents/ tree scan belongs to upstream DeerFlow
    (manage.discover simplified stub, 'extended scan left for later'), not replicated here."""
    if not config:
        return []
    sid = str(config.get("system_id") or "")
    email = str(config.get("email") or "")
    if not (sid and email):
        return []
    skills_dir = Path(_ab._aimail_system_dir(sid)) / _ab._clean_agent_dir_name(email) / "skills"
    try:
        return sorted(
            e.name for e in skills_dir.iterdir()
            if e.is_dir() and not e.name.startswith(".")
        )
    except OSError:
        return []


def set_agent_context(agent_id: str, system_id: str = "") -> None:
    """Attach current agent's config to public core injection point (forward shared implementation)."""
    _ab.set_agent_context(agent_id, system_id)


def make_client(api_key: str = "", system_id: str = ""):
    """Gateway client (aimail_tools._GatewayClient, full method set).

    api_key defaults to reading from current agent's agentmail.json.
    """
    cfg = load_agent_config_for_key(system_id)
    gw_url = (cfg or {}).get("gateway_url", "")
    key = api_key or ((cfg or {}).get("api_key", ""))
    return _tools._GatewayClient(gw_url, key)


def load_agent_config_for_key(system_id: str = "") -> Optional[dict]:
    """Read current system's gateway config (aimail_gateway.json)."""
    try:
        sid = system_id or os.environ.get("AIMAIL_SYSTEM_ID", "")
        path = Path(_ab.aimail_home()) / "systems" / sid / "aimail_gateway.json"
        if path.is_file():
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def load_gateway_config(system_id: str = "") -> Optional[dict]:
    """gateway connection config (forward shared helper)."""
    return _ab._load_gateway_config(system_id)


# Forward shared core functions (AUDIT-1 P0-2: manage.py previously called _base.aimail_home() directly
# AttributeError —— adapters only forward declared symbols)
aimail_home = _ab.aimail_home


def detect_system_id() -> str:
    """System identity: pointer file single source (~/.deer-flow/.agentmail or env)."""
    sid = os.environ.get("AIMAIL_SYSTEM_ID", "")
    if sid:
        return sid
    for ptr in (Path.home() / ".deer-flow" / ".agentmail",
                Path(_ab.aimail_home()) / ".agentmail"):
        d = _ab._read_pointer(ptr)
        if d.get("system_id"):
            return d["system_id"]
    return ""

# ── ② Injection points + capability switches ─────────────────────────────────────────
_ab.PERSONA_SUPPORTED = False        # DeerFlow has no persona derived address concept
_ab._PROFILE_DIR_RESOLVER = _deerflow_profile_dir
# per-agent SOUL/skills (fills the gap of deer-flow's original 0 injection: SOUL and skills both resolved by
# agent address directory; skills enumerates subdirectories of that directory's skills/, missing ⇒ [])
_ab._SOUL_PROVIDER = _deerflow_soul_md
_ab._SKILLS_PROVIDER = _deerflow_skills
# Post-injection self-check: when pointer resolution fails, inbound preprocessing degrades (email delivered as usual, profile/attachment download lost),
# Leave a warn at startup, rather than reporting on every email.
_ab.check_profile_pointer()


# ── ③ Identity injection (on top of ②, same OpenClaw pattern)──────────────────────
# Note: after inbound preprocessing merged into 8001 process, dispatch_to_deerflow (original ③) has been removed;
# Delivery is completed by start_run inside the aimail_inbound router of the deer-flow backend.

# ── Forward shared core (reuse surface, same as OpenClaw)────────────────────────
preprocess_mail_payload = _ab.preprocess_mail_payload
process_inbound_mail = _ab.process_inbound_mail
parse_aimail_persona = _ab.parse_aimail_persona
_extract_board_gateway = _ab._extract_board_gateway
register_board_gateway = _ab._register_board_gateway
store_board_credential = _ab._store_board_credential
email_for_agent = _ab.email_for_agent
register_agent_email = _ab.register_agent_email
deregister_agent_email = _ab.deregister_agent_email
load_agent_config = _ab.load_agent_config
render_message = _ab.render_message
agent_for_email = _ab.route_agent_for_email

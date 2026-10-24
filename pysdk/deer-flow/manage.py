#!/usr/bin/env python3
"""manage.py — DeerFlow platform lifecycle/inbound installation single management module (pysdk self-contained).

Migrated and aggregated from cli/deer-flow/{register_agent.py, reconcile.py, deregister_agent.py,
install-inbound.sh} (2026-09-02), removing dependency on cli/runtime_core.py:
Dependencies: The runtime core/adapter layer is located via dual-form bootstrap (see _bootstrap_runtime), after which
bare imports are usable —— import aimail_deerflow (adapter layer) / import aimail_tools (core).
Business logic preserved verbatim (idempotency/error handling/output lines), only structural relocation performed.

Functions:
  register_agents(manager, system_id, agent) -> int    ← register_agent.py main
  reconcile(system_id, manager, dry_run) -> int         ← reconcile.py main
  deregister_agents(agent, manager, system_id) -> int   ← deregister_agent.py main
  patch_backend_app(backend_dir) -> bool                ← python embedded in install-inbound.sh (patch section)
  install_bundle(backend_dir, source_root, force) -> int← first section of install-inbound.sh (runtime_bundle simplification)

Subcommand dispatch (if __name__ == '__main__', compatible with old script usage):

  python3 manage.py register    [--agent <id>|--all] [--manager M] [--system-id SID]
  python3 manage.py reconcile   [--system-id SID] [--manager M] [--dry-run]
  python3 manage.py deregister  --agent <id> [--manager M] [--system-id SID]
  python3 manage.py patch       [DEER_FLOW_ROOT]          # app.py patch section only (idempotent)
  python3 manage.py install     [DEER_FLOW_ROOT] [--source-root DIR] [--force]
                               # Full flow: bundled install + app.py patch + syntax check (original install-inbound.sh)

Dual-form bootstrap (removing runtime_core):
  Form 1 repo dev: this file is under pysdk/deer-flow/, dirname(dirname(__file__)) =
        pysdk/ (containing aimail_base.py) is the core directory, directly added to sys.path.
  Form 2 pip: site-packages/aimail/deer-flow/, the aimail/ package directory also contains
        aimail_base.py; `import aimail` (glue, __init__ inserts aimail/ into sys.path)
        as a fallback. This module's own directory (adapter layer, containing aimail_deerflow.py) is also added.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

STAMP_NAME = ".aimail-runtime.json"
MIN_PAYLOAD_VERSION = "0.1.0"

# Bundle definition: source relative path (relative to core directory/source root) → target filename (under host routers/)
_BUNDLE_FILES = [
    ("deer-flow/aimail_inbound.py", "aimail_inbound.py"),
    ("deer-flow/aimail_deerflow.py", "aimail_deerflow.py"),
    ("aimail_base.py", "aimail_base.py"),
    ("aimail_tools.py", "aimail_tools.py"),
    ("aimail_board.py", "aimail_board.py"),
    ("aimail_contract.py", "aimail_contract.py"),
    ("gateway_api.py", "gateway_api.py"),
    ("_aimail_bootstrap.py", "_aimail_bootstrap.py"),
]


# ── Dual-form bootstrap (replaces runtime_core.load_core/load_adapter)────────────────
def _bootstrap_runtime() -> tuple[str, str]:
    """Locate (adapter layer directory, core directory) and add to sys.path (idempotent).

    Form 1 repo pysdk: this file is at <core>/deer-flow/manage.py, parent directory contains
        aimail_base.py → core is dirname(dirname(__file__)).
    Form 2 pip site-packages/aimail/deer-flow/: the aimail/ package directory also contains
        aimail_base.py (same formula hits); then `import aimail` glue as fallback
        (aimail/__init__ inserts package directory into sys.path).
    Core missing → SystemExit(same style as original runtime_core).
    """
    _here = os.path.dirname(os.path.abspath(__file__))          # adapter layer directory
    _parent = os.path.dirname(_here)
    _core = _parent if os.path.isfile(os.path.join(_parent, "aimail_base.py")) else ""
    if not _core:
        try:
            import aimail  # glue: __init__ inserts aimail/ directory into sys.path  # noqa: F401
            _pkg = os.path.dirname(os.path.abspath(aimail.__file__))
            if os.path.isfile(os.path.join(_pkg, "aimail_base.py")):
                _core = _pkg
        except Exception:
            pass
    if not _core:
        raise SystemExit("ERROR: aimail runtime core not found (neither repo pysdk/ nor pip aimail available)")
    for _d in (_here, _core):
        if _d not in sys.path:
            sys.path.insert(0, _d)
    return _here, _core


_ADAPTER_DIR, _CORE_DIR = _bootstrap_runtime()

import aimail_deerflow as _base     # noqa: E402   (deer-flow adapter layer, this directory)
import aimail_base as _core         # noqa: E402   (shared core, parent directory)
import aimail_tools as _tools       # noqa: E402   (shared core, parent directory)
import aimail_contract as _contract  # noqa: E402  (shared core: agent side contract constants)
# Note: resolve_register_webhook_url is defined in the shared core aimail_base -- adapter layer aimail_deerflow
# does not forward, goes directly to _core. Routing/bridge not in SDK (2026-09-28 de-bridging): registration chain only writes binding and registration value,
# routing is pushed by CLI itself after "inbound truly in service" (cli/bridge_wire.py).

# DeerFlow local inbound endpoint (in-process preprocessing): path = contract INBOUND_PATH (immutable);
# port configurable (DEERFLOW_INBOUND_URL override, default = contract inbound_ports.deerflow).
_DEERFLOW_INBOUND_DEFAULT = "http://127.0.0.1:%d" % _contract.INBOUND_PORTS["deerflow"]


# ══════════════════════════════════════════════════════════════════════
# Registration (register_agent.py verbatim port)
# ══════════════════════════════════════════════════════════════════════
def email_for_agent(agent_id: str, domain: str, system_name: str) -> str:
    """Address derivation (public core email_for_agent; DeerFlow default name default → agent)."""
    return _base.email_for_agent(agent_id, domain, system_name,
                                 default_aliases=("default",))


def discover_deerflow_agents() -> list:
    """List DeerFlow agents: scan skills directory + SOUL.md determination (default lead agent).
    Simplified implementation: returns ["default"] (DeerFlow default lead_agent); subsequent reconcile()
    does full directory reconciliation."""
    return ["default"]


def register_one(client, system_id: str, agent_id: str, email: str,
                 webhook_url: str, webhook_secret: str, manager_address: str,
                 domain: str, system_name: str, gateway_url: str,
                 local_webhook_url: str) -> dict:
    """Register single agent: core chain goes through public register_agent_email, platform part only does local disk assembly.

    webhook_url (registration parameter) three states (see resolve_register_webhook_url); the one persisted is always
    local_webhook_url (local receiving endpoint, single source of truth, for bridge routing).
    """
    reg = _base.register_agent_email(
        client, system_id, email, webhook_url, webhook_secret,
        manager_address,
    )
    api_key = reg.get("api_key", "")
    activation_code = reg.get("activation_code", "")

    cfg = {
        "email": email,
        "gateway_url": gateway_url,
        "domain": domain,
        "system_id": system_id,
        "system_name": system_name,
        "manager_address": manager_address,
        "api_key": api_key,
        # Preserve code when activating pending: next register/reconcile directly connects to activate, no re-registration
        "activation_code": activation_code,
        # agentmail.json webhook_url = local receiving endpoint (single source of truth, for bridge routing)
        "webhook_url": local_webhook_url,
        "webhook_secret": webhook_secret,
        "assistant_id": os.environ.get("DEERFLOW_ASSISTANT_ID", "lead_agent"),
    }
    return cfg


def save_agent_config(agent_id: str, cfg: dict, system_id: str) -> None:
    """Disk address key agentmail.json — delegate to shared atomic implementation
    (aimail_base.save_agent_config:tmp+rename+0600, equivalent to TS config.ts)."""
    path = _core.save_agent_config(agent_id, cfg, system_id)
    print(f"  ✓ saved {path}")


def register_agents(manager: str = "", system_id: str = "", agent: str = "",
                    name: str = "") -> int:
    """Register DeerFlow agent(s) to the aimail system.

    Register chain (register_email → update webhook if exists → manager whitelist → activate_address)
    Use the common core aimail_base.register_agent_email(shared by all platforms) → disk address key
    agentmail.json(systems/{sid}/{cleaned_addr}/agentmail.json)。

    Args:
        agent:     agent id; passing "all" is equivalent to the original --all (discover_deerflow_agents());
                  empty string → SystemExit "need --agent <id> or --all".
        name:      Target address base name (P6/C2, 2026-10-02): non-empty => email derived from this, registrar
                  **Direct** target name —— Retire the intermediate state of "first register with default name then rename" (F13 root: intermediate state
                  leaves orphan rows in the gateway without local binding); empty = derived from agent_id (legacy behavior).
        manager:   manager_address (approval contact); defaults to reading the AIMAIL_MANAGER_ADDRESS environment variable.
        system_id: defaults to reading the AIMAIL_SYSTEM_ID environment variable / detect_system_id().

    Returns:
        0 (original script always returned 0; see output line registered: N/M for counts).

    Example:
      register_agents(manager="admin@x.com", agent="default")
      register_agents(manager="admin@x.com", agent="all")
      register_agents(manager="admin@x.com", agent="work", system_id="SID")
    """
    if not agent:
        raise SystemExit("need --agent <id> or --all")

    system_id = system_id or _base.detect_system_id()
    gw = _base.load_gateway_config(system_id)
    if not gw:
        raise SystemExit(f"gateway config not found (aimail_gateway.json) for {system_id} — run aimail install first")
    manager = _core.resolve_manager_address(manager)
    if not manager:
        raise SystemExit("need --manager <addr> or env "
                         + " / ".join(_core.MANAGER_ENV_VARS)
                         + " (approval contact; three names mutually recognized, owner contract 2026-09-30)")

    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))
    agents = discover_deerflow_agents() if agent == "all" else [agent]
    if not agents:
        agents = ["default"]

    # Local receiving endpoint (in-process preprocessing, DeerFlow local gateway + contract INBOUND_PATH;
    # DEERFLOW_INBOUND_URL can override); registration parameter tri-state determined by resolve_register_webhook_url
    inbound_base = os.environ.get("DEERFLOW_INBOUND_URL", _DEERFLOW_INBOUND_DEFAULT)
    local_webhook_url = inbound_base.rstrip("/") + _contract.INBOUND_PATH
    reg_url = _core.resolve_register_webhook_url(gw, local_webhook_url)

    created = 0
    for agent_id in agents:
        # Local pending activation binding already exists → direct reactivation, no re-registration
        # (re-registration goes through exists branch returning empty code, never gets api_key)
        local = _local_agents(system_id).get(agent_id)
        if local and local.get("activation_code") and not local.get("api_key"):
            if _activate_pending(agent_id, local, system_id, gw):
                created += 1
            continue
        # C2: Direct target name (P6 ruling) —— email derivation uses name (the target base name given in the plan), agent_id
        # only carries platform semantics (binding/session), same shape as the final state after rename
        email = email_for_agent(name or agent_id, gw["domain"],
                                gw.get("system_name", ""))
        # 2026-10-02 E2 (paired with core E1, caller hygiene): existing binding => reuse its secret, only when missing
        # Original implementation minted new random each round making "re-registration" look like rotation: exists branch PUT overwrites gateway with new value,
        # And binding is only persisted when api_key/activation_code is obtained (manage.py:224-235) => two keys
        # (fuse for defect 10; one install registers 3 times —— registry install_steps).
        webhook_secret = (_core.existing_binding_webhook_secret(system_id, email)
                          or _core.new_webhook_secret())
        cfg = register_one(
            client, system_id, agent_id, email,
            reg_url, webhook_secret, manager,
            gw["domain"], gw.get("system_name", ""), gw["gateway_url"],
            local_webhook_url,
        )
        if cfg["api_key"]:
            save_agent_config(agent_id, cfg, system_id)
            created += 1
            print(f"  ✓ {agent_id} → {email} (api_key ok)")
        elif cfg.get("activation_code"):
            # Activate pending: persist code, next registration/reconciliation direct connect to activate_address
            # (no re-registration — the exists branch returns an empty code, so re-registration can never obtain the key)
            save_agent_config(agent_id, cfg, system_id)
            print(f"  ⚠ {agent_id} → {email} activation pending (code saved;"
                  f" re-run register/reconcile to complete activation)")
        else:
            print(f"  ⚠ {agent_id} → {email} registered but no api_key (activation pending)")

    print(f"registered: {created}/{len(agents)}")
    return 0


# ══════════════════════════════════════════════════════════════════════
# Reconciliation (reconcile.py verbatim port)
# ══════════════════════════════════════════════════════════════════════
def _local_agents(system_id: str) -> dict:
    """Read local aimail registry: {agent_id: cfg}."""
    out = {}
    base = os.path.join(_base.aimail_home(), "systems", str(system_id))
    if not os.path.isdir(base):
        return out
    for addr_dir in sorted(os.listdir(base)):
        aj = os.path.join(base, addr_dir, "agentmail.json")
        if not os.path.isfile(aj):
            continue
        try:
            cfg = json.load(open(aj))
            if cfg.get("agent_id"):
                out[cfg["agent_id"]] = cfg
        except Exception:
            pass
    return out


def _activate_pending(agent_id: str, cfg: dict, system_id: str, gw: dict) -> bool:
    """agentmail.json has activation_code but no api_key → activate_address to complete activation
    (aligned with hermes _auto_activate_profile:300s rate limit, failure retains timestamp;
    AUDIT-1 P1-4: previously could never complete activation because code was not persisted)."""
    code = cfg.get("activation_code", "")
    if not code or cfg.get("api_key"):
        return False
    import time as _t
    now = _t.time()
    last = cfg.get("last_activation_attempt", 0)
    if last and (now - last) < 300:
        return False
    try:
        client = _tools._GatewayClient(cfg.get("gateway_url") or gw["gateway_url"], "",
                                       timeout=5)
        res = client.activate_address(code, email_address=cfg.get("email", ""))
    except Exception as e:  # noqa: BLE001
        cfg["last_activation_attempt"] = now
        save_agent_config(agent_id, cfg, system_id)
        print(f"  ⚠ {agent_id}: activate failed: {e}")
        return False
    if res.get("success") and res.get("raw_key"):
        cfg["api_key"] = res["raw_key"]
        cfg.pop("activation_code", None)
        cfg.pop("last_activation_attempt", None)
        save_agent_config(agent_id, cfg, system_id)
        print(f"  ✓ {agent_id}: activated (api_key ok)")
        return True
    cfg["last_activation_attempt"] = now
    save_agent_config(agent_id, cfg, system_id)
    print(f"  ⚠ {agent_id}: activation pending: {res.get('error', res)}")
    return False


def reconcile(system_id: str = "", manager: str = "", dry_run: bool = False) -> int:
    """DeerFlow lifecycle reconciliation (reconcile.py main verbatim port, removed argparse; for cron scheduling).

    DeerFlow has no event bus (no created/deleted callbacks), agents are defined by directories (SOUL.md/
    agents/). Perform idempotent reconciliation with "directory as source of truth":
      1. Scan DeerFlow agents directory (default only recognizes lead agent "default")
      2. Read aimail registry (systems/{sid}/*/agentmail.json)
      3. Diff actions (common chain idempotent):
         with/without → register_agent_email(4-step chain)→ persist to agentmail.json
         none/yes → deregister_agent_email(3-step chain)→ clean up local

    Args:
        system_id: Defaults to reading the AIMAIL_SYSTEM_ID environment variable / detect_system_id().
        manager:   manager_address; defaults to reading AIMAIL_MANAGER_ADDRESS.
        dry_run:   Only prints differences, does not execute.

    Returns:
        0(success; missing system_id / gateway config → 1, stderr output).

    cron example (system crontab, every 30 minutes):
      */30 * * * * python3 .../manage.py reconcile --system-id SID
    """
    system_id = system_id or _base.detect_system_id()
    if not system_id:
        print("need --system-id (or AIMAIL_SYSTEM_ID / pointer)", file=sys.stderr)
        return 1
    gw = _base.load_gateway_config(system_id)
    if not gw:
        print(f"gateway config not found for {system_id}", file=sys.stderr)
        return 1

    # 1. Directory source of truth (DeerFlow lead agent = "default"; extension scanning deferred to later)
    desired = {"default": {"assistant_id": gw.get("assistant_id", "lead_agent")}}

    # 2. Local registry
    local = _local_agents(system_id)

    # Name mutual recognition fallback (AIMAIL_MANAGER_ADDRESS /
    # INTEGRATE_MANAGER_ADDRESS)—— historical bare name AIMAIL_MANAGER has been retired (2026-10-17),
    # an old bug that only recognized the bare name caused reconcile to always get '' and write empty values into the whitelist (forensics P1 main hop).
    # If still empty after resolution, register_agent_email's hard gate raises an error (no silent registration).
    manager = _core.resolve_manager_address(manager)
    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))

    changes = 0
    for agent_id, meta in desired.items():
        if agent_id in local:
            # Registered: complete activation loop (agentmail.json has activation_code but no api_key
            # → activate_address; align hermes _auto_activate_profile, AUDIT-1 P1-4)
            lc = local[agent_id]
            if lc.get("activation_code") and not lc.get("api_key"):
                if dry_run:
                    print(f"  [dry] would activate pending {agent_id}")
                    changes += 1
                elif _activate_pending(agent_id, lc, system_id, gw):
                    changes += 1
            # Routing is not in the SDK (2026-09-28 de-bridging): here we only complete activation/align binding; routing is pushed by the CLI after
            # inbound is truly live (host notification `aimail address -a <addr> --inbound-live`).
            continue
        if dry_run:
            print(f"  [dry] would register {agent_id}")
            changes += 1
            continue
        email = _base.email_for_agent(agent_id, gw["domain"], gw.get("system_name", ""),
                                      default_aliases=("default",))
        # 2026-10-02 E2 (paired with core E1, caller hygiene): existing binding => reuse its secret, only when missing
        # Original implementation minted a new random value each round making "re-registration" look like rotation: the exists branch PUT overwrote the gateway with the new value,
        # And binding only persists to disk once api_key/activation_code is obtained (manage.py:224-235) => two keys
        # (the fuse for Defect 10; a single install registers 3 times -- registry install_steps).
        webhook_secret = (_core.existing_binding_webhook_secret(system_id, email)
                          or _core.new_webhook_secret())
        # Local receiving endpoint (in-process preprocessing, DeerFlow local gateway + contract INBOUND_PATH;
        # DEERFLOW_INBOUND_URL can override, 2026-08-18 refactor)
        inbound_base = os.environ.get("DEERFLOW_INBOUND_URL", _DEERFLOW_INBOUND_DEFAULT)
        local_webhook_url = inbound_base.rstrip("/") + _contract.INBOUND_PATH
        # Registration parameter tri-state: push=bridge public entry / pull=empty / no bridge=local endpoint
        reg_url = _core.resolve_register_webhook_url(gw, local_webhook_url)
        reg = _base.register_agent_email(
            client, system_id, email, reg_url, webhook_secret, manager,
        )
        if reg.get("api_key"):
            cfg = {
                "email": email,
                "gateway_url": gw["gateway_url"],
                "domain": gw["domain"],
                "system_id": system_id,
                "system_name": gw.get("system_name", ""),
                "manager_address": manager,
                "api_key": reg["api_key"],
                # agentmail.json webhook_url = local receiving endpoint (single source of truth, for bridge routing)
                "webhook_url": local_webhook_url,
                "webhook_secret": webhook_secret,
                "assistant_id": meta.get("assistant_id", "lead_agent"),
            }
            # Persistence uses shared thin shell (atomic 0600, AUDIT-1 P1-5: was private direct write without 0600)
            save_agent_config(agent_id, cfg, system_id)
            changes += 1
            print(f"  ✓ registered {agent_id} → {email}")
        else:
            # activation pending: persist activation_code, subsequent reconcile completes activation
            # (previously only print discarded code → could never re-activate; AUDIT-1 P1-4)
            cfg = {
                "email": email,
                "gateway_url": gw["gateway_url"],
                "domain": gw["domain"],
                "system_id": system_id,
                "system_name": gw.get("system_name", ""),
                "manager_address": manager,
                "api_key": "",
                # agentmail.json webhook_url = local receiving endpoint (single source of truth, for bridge routing)
                "webhook_url": local_webhook_url,
                "webhook_secret": webhook_secret,
                "assistant_id": meta.get("assistant_id", "lead_agent"),
                "activation_code": reg.get("activation_code", ""),
            }
            save_agent_config(agent_id, cfg, system_id)
            changes += 1
            print(f"  ⚠ {agent_id} → {email} activation pending (code retained)")

    # 3. Unregister: no longer handled by reconcile (AUDIT-1 P2-4) -- desired currently only contains
    # "default" (directory extension scanning deferred to later), previously registered non-default agents would be mistakenly deregistered.
    # Cleanup of deprecated agents goes through explicit command deregister_agents (deregistration chain common core, idempotent).

    if changes == 0:
        print("  no changes")
    return 0


# ══════════════════════════════════════════════════════════════════════
# Deregistration (verbatim port from deregister_agent.py)
# ══════════════════════════════════════════════════════════════════════
def deregister_agents(agent: str, manager: str = "", system_id: str = "") -> int:
    """Deregister DeerFlow agent.

    Deregistration chain (api-key → domain → whitelist) uses the common core
    aimail_base.deregister_agent_email (shared by all platforms, idempotent), then cleans up local
    agentmail.json。

    Args:
        agent:     agent id (default name default); empty string → SystemExit "need --agent <id>".
        manager:   manager_address; defaults to reading local cfg.manager_address / AIMAIL_MANAGER_ADDRESS.
        system_id: Defaults to reading the AIMAIL_SYSTEM_ID environment variable / detect_system_id().

    Returns:
        0 (success; returns 0 and prompts nothing to do if not registered locally).

    Example:
      deregister_agents(agent="default")
      deregister_agents(agent="default", system_id="SID")
    """
    if not agent:
        raise SystemExit("need --agent <id>")

    system_id = system_id or _base.detect_system_id()
    gw = _base.load_gateway_config(system_id)
    if not gw:
        raise SystemExit(f"gateway config not found for {system_id}")

    cfg = _base.load_agent_config(agent, system_id)
    if not cfg:
        print(f"  agent '{agent}' not registered locally — nothing to do")
        return 0

    email = cfg.get("email", "")
    manager = manager or cfg.get("manager_address", "") or os.environ.get("AIMAIL_MANAGER_ADDRESS", "")

    client = _tools._GatewayClient(gw["gateway_url"], gw.get("admin_key", ""))
    result = _base.deregister_agent_email(client, system_id, email, manager)
    print(f"  deregister {agent} ({email}): {json.dumps(result, ensure_ascii=False)}")

    # Clean up local agentmail.json (directory key shares the same source as _clean_agent_dir_name --
    # local re.sub lacks re.ASCII, non-ASCII characters are preserved by unicode \w causing wrong directory)
    try:
        cleaned = _core._clean_agent_dir_name(email)
    except Exception:
        cleaned = re.sub(r"[^\w.\-]", "_", email, flags=re.ASCII)
    path = os.path.join(_base.aimail_home(), "systems", str(system_id), str(cleaned), "agentmail.json")
    if os.path.isfile(path):
        os.remove(path)
        print(f"  ✓ removed {path}")
    return 0


# ══════════════════════════════════════════════════════════════════════
# app.py patch (complete extraction of the embedded python segment in install-inbound.sh, idempotent)
# ══════════════════════════════════════════════════════════════════════
def _gateway_layout(backend_dir: str) -> tuple[str, str]:
    """Parse DeerFlow gateway directory and app.py path.

    backend_dir may be the DeerFlow repo root (DEER_FLOW_ROOT of install-inbound.sh,
    default ~/deer-flow) or a directory already containing backend (e.g., .../deer-flow/backend); both
    are automatically recognized. Returns (gateway_dir, app_py).
    """
    root = os.path.abspath(os.path.expanduser(backend_dir))
    for g in (os.path.join(root, "backend", "app", "gateway"),
              os.path.join(root, "app", "gateway")):
        if os.path.isfile(os.path.join(g, "app.py")):
            return g, os.path.join(g, "app.py")
    # Default returns based on repository root layout (error message displays expected path)
    g = os.path.join(root, "backend", "app", "gateway")
    return g, os.path.join(g, "app.py")


def patch_backend_app(backend_dir: str) -> bool:
    """patch DeerFlow backend/app/gateway/app.py (idempotent; install-inbound.sh patch segment).

    2a. import line: after "    agents,\\n" insert "    aimail_inbound,\\n" (adjacent alphabetically).
    2b. include_router line: after "    app.include_router(agents.router)\\n" insert
        "    app.include_router(aimail_inbound.router)\\n"。

    Args:
        backend_dir: DeerFlow repository root (or directory containing backend).

    Returns:
        True = modifications applied this time; False = app.py already contains aimail_inbound (idempotent skip).

    Raises:
        SystemExit: app.py does not exist or anchor is missing (message consistent with original script).
    """
    g_dir, app_py = _gateway_layout(backend_dir)
    if not os.path.isfile(app_py):
        raise SystemExit(f"ERROR: app.py not found: {app_py}")
    src = open(app_py, encoding="utf-8").read()
    changed = False

    # 2a-0. First clear old anchor lines inserted by previous forms of this patch (form before module name change).
    # Not doing this step: app.py contains both old and new lines → import old module fails, while idempotency check
    # (check if new name exists) determine "already included" → install reports success but host fails to start.
    for stale in ("    agentmail_inbound,\n",
                  "    app.include_router(agentmail_inbound.router)\n"):
        if stale in src:
            src = src.replace(stale, "", 1)
            changed = True

    # 2a. import line: attached after agents (alphabetically adjacent)
    import_marker = "    agents,\n"
    import_line = "    aimail_inbound,\n"
    if import_line not in src:
        if import_marker in src:
            src = src.replace(import_marker, import_marker + import_line, 1)
            changed = True
        else:
            raise SystemExit("ERROR: import anchor 'agents,' not found in routers import block")

    # 2b. include_router line: attached after agents.router
    route_marker = "    app.include_router(agents.router)\n"
    route_line = "    app.include_router(aimail_inbound.router)\n"
    if route_line not in src:
        if route_marker in src:
            src = src.replace(route_marker, route_marker + route_line, 1)
            changed = True
        else:
            raise SystemExit("ERROR: include_router anchor 'agents.router' not found")

    # 2c. agent-scope scheduled polling entry (install finishing wiring): long-running host starts pull loop upon startup.
    #     Addresses activated via address-level activation codes have no push path (gateway writes webhook_url = NULL for them),
    #     can only pull on its own schedule -- decision/interval/shutdown/failure no-ack in shared core
    #     (aimail_base start_agent_pull_entries), here we only wire up the "startup finishing" step:
    #     attached after include_router (same app assembly section as routes), start/stop follows host lifecycle.
    pull_line = ("    aimail_inbound.start_pull_on_startup(app)"
                 "  # aimail: agent-scope pull entry\n")
    if pull_line not in src:
        if route_line in src:
            src = src.replace(route_line, route_line + pull_line, 1)
            changed = True
        else:
            raise SystemExit("ERROR: include_router line missing — cannot wire the pull entry")

    if changed:
        open(app_py, "w", encoding="utf-8").write(src)
        print("  ✓ app.py patched (import + include_router + pull entry)")
        return True
    print("  ✓ app.py already contains aimail_inbound + pull entry (skipped)")
    return False


def unpatch_backend_app(backend_dir: str) -> bool:
    """Revert the app.py patch (AUDIT-1 P1-4): remove exactly the three lines
    patch_backend_app inserts (import + include_router + pull entry).
    Idempotent — no-op when already clean."""
    g_dir, app_py = _gateway_layout(backend_dir)
    if not os.path.isfile(app_py):
        print("  ⚠ app.py not found (nothing to unpatch)")
        return False
    src = open(app_py, encoding="utf-8").read()
    removed = False
    for line in ("    aimail_inbound.start_pull_on_startup(app)"
                 "  # aimail: agent-scope pull entry\n",
                 "    app.include_router(aimail_inbound.router)\n",
                 "    aimail_inbound,\n"):
        if line in src:
            src = src.replace(line, "", 1)
            removed = True
    if removed:
        open(app_py, "w", encoding="utf-8").write(src)
        print("  ✓ app.py unpatched (aimail_inbound import + include_router + pull entry removed)")
    else:
        print("  ✓ app.py already clean (no aimail_inbound lines)")
    return removed


# ══════════════════════════════════════════════════════════════════════
# Bundled install (simplified port of the runtime_bundle call in the first half of install-inbound.sh)
# ══════════════════════════════════════════════════════════════════════
def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_kind(core_dir: str) -> str:
    """Source type determination (repo pysdk/ vs pip site-packages/aimail/, only affects version stamp)."""
    if os.path.basename(os.path.abspath(core_dir)) == "pysdk":
        return "repo"
    try:
        import aimail  # type: ignore
        if os.path.abspath(os.path.dirname(os.path.abspath(aimail.__file__))) == os.path.abspath(core_dir):
            return "pip"
    except Exception:
        pass
    return "pip"


def _bundle_version(core_dir: str, kind: str) -> str:
    """Bundled source version: pip → aimail.__version__; repo → git describe (dev on failure)."""
    if kind == "pip":
        try:
            import aimail  # type: ignore
            return getattr(aimail, "__version__", "0.0.0")
        except Exception:
            return "0.0.0"
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "-C", core_dir, "describe", "--always", "--dirty"],
            text=True, timeout=10, stderr=subprocess.DEVNULL).strip()
        return out or "dev"
    except Exception:
        return "dev"


def install_bundle(backend_dir: str, source_root: str = "", force: bool = False) -> int:
    """Install deer-flow runtime bundle to host routers/ (flat, idempotent, md5 drift detectable).

    Source (pysdk single source of truth, 8 files) → target <gateway>/routers/:
      deer-flow/{aimail_inbound.py, aimail_deerflow.py}
      {aimail_base.py, aimail_tools.py, aimail_board.py, aimail_contract.py,
       gateway_api.py, _aimail_bootstrap.py}
    Writes .aimail-runtime.json version stamp (bundle/version/source/installed_at/
    min_version/files md5), isomorphic to the original runtime_bundle stamp.

    Args:
        backend_dir: DeerFlow repository root (or directory containing backend).
        source_root: Explicitly specify source root (must contain aimail_base.py); default uses the core directory
                     bootstrapped by this module (repo pysdk/ or pip aimail/ package directory).
        force:       Force overwrite (ignore md5 match skip).

    Returns:
        0 = completed (all consistent or updated); 1 = source file missing.
    """
    if source_root:
        root = os.path.abspath(os.path.expanduser(source_root))
        if not os.path.isfile(os.path.join(root, "aimail_base.py")):
            raise SystemExit(f"ERROR: --source-root invalid (no aimail_base.py): {root}")
        kind = _source_kind(root)
    else:
        root, kind = _CORE_DIR, _source_kind(_CORE_DIR)
    version = _bundle_version(root, kind)

    g_dir, _app_py = _gateway_layout(backend_dir)
    # Flatten into routers/: app.py imports via `from .routers import aimail_inbound`,
    # router and core must be in the same directory (consistent with the deer-flow spec in cli/runtime_bundle.py).
    dst_dir = os.path.join(g_dir, "routers")

    changed, missing_src = [], []
    for src_rel, dst_name in _BUNDLE_FILES:
        src = os.path.join(root, src_rel)
        dst = os.path.join(dst_dir, dst_name)
        if not os.path.isfile(src):
            missing_src.append(src_rel)
            continue
        need = force or not os.path.isfile(dst) or _md5(src) != _md5(dst)
        if need:
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copyfile(src, dst)
            changed.append(dst_name)

    if missing_src:
        print(f"  ✗ deer-flow: source missing {missing_src} (source root {root})")
        return 1

    _keep_names = {name for _rel, name in _BUNDLE_FILES}
    # Clean up old files from the previous ledger that are no longer distributed with the package (renamed/retired leftovers)
    pruned = []
    _stamp_file = os.path.join(dst_dir, STAMP_NAME)
    if os.path.isfile(_stamp_file):
        try:
            with open(_stamp_file, encoding="utf-8") as f:
                _old_files = json.load(f).get("files", {})
        except (OSError, ValueError):
            _old_files = {}
        for _stale in sorted(set(_old_files) - _keep_names):
            _p = os.path.join(dst_dir, _stale)
            if os.path.dirname(os.path.abspath(_p)) != os.path.abspath(dst_dir):
                continue                    # Only delete files within this directory (prevent path traversal)
            if os.path.isfile(_p):
                os.remove(_p)
                pruned.append(_stale)

    stamp = {
        "bundle": "deer-flow",
        "version": version,
        "source": kind,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_version": MIN_PAYLOAD_VERSION,
        "files": {name: _md5(os.path.join(dst_dir, name))
                  for _rel, name in _BUNDLE_FILES
                  if os.path.isfile(os.path.join(dst_dir, name))},
    }
    os.makedirs(dst_dir, exist_ok=True)
    tmp = os.path.join(dst_dir, STAMP_NAME) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stamp, f, indent=2, ensure_ascii=False)
    os.replace(tmp, os.path.join(dst_dir, STAMP_NAME))

    if pruned:
        print(f"  ✓ deer-flow: Cleaned up old files {pruned} → {dst_dir}")
    if changed:
        print(f"  ✓ deer-flow: Updated {len(changed)} files → {dst_dir} (v{version}, {kind})")
    else:
        print(f"  ✓ deer-flow: Already consistent (skipped) → {dst_dir} (v{version}, {kind})")
    return 0


# ══════════════════════════════════════════════════════════════════════
# CLI subcommand dispatch (compatible with original script usage)
# ══════════════════════════════════════════════════════════════════════
def main(argv: list | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="DeerFlow aimail management (register/reconcile/deregister/install inbound)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # Same parameters as register_agent.py
    p = sub.add_parser("register", description="Register DeerFlow agent to aimail")
    p.add_argument("--agent", default="")
    p.add_argument("--name", default="",
                   help="Target address base name (P6/C2: registrar **directly** uses this name, no longer registers default name then renames);"
                        " empty = derived from --agent (legacy behavior)")
    p.add_argument("--all", action="store_true", help="Register all DeerFlow agents")
    p.add_argument("--manager", default="", help="manager_address (approval contact); defaults to reading AIMAIL_MANAGER_ADDRESS env var")
    p.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    p.set_defaults(func=lambda a: _cmd_register(a))

    # Same parameters as reconcile.py
    p = sub.add_parser("reconcile", description="DeerFlow lifecycle reconciliation")
    p.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    p.add_argument("--manager", default="", help="manager_address; defaults to reading AIMAIL_MANAGER_ADDRESS")
    p.add_argument("--dry-run", action="store_true", help="Only print differences, do not execute")
    p.set_defaults(func=lambda a: reconcile(system_id=a.system_id, manager=a.manager,
                                            dry_run=a.dry_run))

    # Same parameters as deregister_agent.py
    p = sub.add_parser("deregister", description="Deregister DeerFlow agent from aimail")
    p.add_argument("--agent", required=True, help="agent id (default name default)")
    p.add_argument("--manager", default="", help="manager_address; defaults to reading AIMAIL_MANAGER_ADDRESS")
    p.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    p.set_defaults(func=lambda a: deregister_agents(agent=a.agent, manager=a.manager,
                                                    system_id=a.system_id))

    # install-inbound.sh: patch section (app.py only)
    p = sub.add_parser("patch", description="patch DeerFlow backend app.py (idempotent; import + include_router)")
    p.add_argument("deerflow_root", nargs="?", default="~/deer-flow", help="DeerFlow repo root (default ~/deer-flow)")
    p.set_defaults(func=lambda a: _cmd_patch(a))

    # install-inbound.sh: full flow (bundled install + patch + verification)
    p = sub.add_parser("install", description="Install deer-flow inbound bundle + patch app.py + verify")
    p.add_argument("deerflow_root", nargs="?", default="~/deer-flow", help="DeerFlow repo root (default ~/deer-flow)")
    p.add_argument("--source-root", default="", help="Bundle source root (default = pysdk/pip aimail package directory)")
    p.add_argument("--force", action="store_true", help="Force overwrite (ignore md5 match skip)")
    p.set_defaults(func=lambda a: _cmd_install(a))

    args = ap.parse_args(argv)
    return args.func(args)


def _cmd_register(a) -> int:
    """register subcommand: --agent/--all mutual exclusion check (original argparse text) then convert to function."""
    if not a.agent and not a.all:
        raise SystemExit("need --agent <id> or --all")
    if a.agent and a.all:
        raise SystemExit("--agent and --all are mutually exclusive")
    agent = a.agent or ("all" if a.all else "")
    # --name only applies to single agent (--all enumerates each agent to derive address, does not accept unified name)
    name = a.name if a.agent else ""
    return register_agents(manager=a.manager, system_id=a.system_id, agent=agent,
                           name=name)


def _cmd_patch(a) -> int:
    """patch subcommand: only patch app.py + anchor confirmation."""
    patch_backend_app(a.deerflow_root)
    # Anchor confirmation (original script step-3 grep; patch success implies anchor exists, re-verify here)
    _g, app_py = _gateway_layout(a.deerflow_root)
    if os.path.isfile(app_py) and "aimail_inbound" in open(app_py, encoding="utf-8").read():
        print("  ✓ Anchor confirmed (app.py contains aimail_inbound)")
    return 0


def _cmd_install(a) -> int:
    """install subcommand: replicate install-inbound.sh full flow (bundle → patch → verify)."""
    rc = install_bundle(a.deerflow_root, source_root=a.source_root, force=a.force)
    if rc != 0:
        return rc
    try:
        patch_backend_app(a.deerflow_root)
    except SystemExit as e:
        print(e)
        return 1

    # step-3 verification: syntax (if host venv exists) + anchor; skip syntax compile if venv missing
    g_dir, app_py = _gateway_layout(a.deerflow_root)
    venv_py = os.path.join(os.path.dirname(g_dir), "..", ".venv", "bin", "python")
    venv_py = os.path.abspath(venv_py)
    ok = True
    if os.path.isfile(venv_py):
        # Verify all files distributed with the package (flattened into routers/) + app.py
        targets = [os.path.join(g_dir, "routers", dst_name) for _rel, dst_name in _BUNDLE_FILES]
        targets.append(app_py)
        targets = [t for t in targets if os.path.isfile(t)]
        r = os.system(f'"{venv_py}" -m py_compile {" ".join(targets)}')
        if r != 0:
            ok = False
    else:
        print("  (Host .venv not found, skipping py_compile syntax verification)")
    if "aimail_inbound" not in open(app_py, encoding="utf-8").read():
        print("ERROR: app.py anchor missing")
        ok = False
    if ok:
        print("  ✓ Syntax verification passed + anchor confirmed")
    root = os.path.expanduser(a.deerflow_root)
    print("")
    print("Done. Restart DeerFlow gateway(8001) to take effect:")
    print(f"  kill <uvicorn-pid> && cd {root}/backend && DEER_FLOW_AUTH_DISABLED=1 \\")
    print("    PYTHONPATH=. PYTHONIOENCODING=utf-8 .venv/bin/uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

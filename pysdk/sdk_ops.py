#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aimail.sdk_ops — SDK-side **executable gate** (process command contract).

Architecture (same principle as `install.py`, added 2026-10-04):

    Each SDK has its own **spawnable entry point**; the caller (host ops program or human) only executes it and does not
    hold the SDK's algorithms itself. The TS-side platform packages have long been in this form (`dist/register-cli.js` /
    `dist/register-cli.js`, registry `register.kind=node_entry`); this module is the Python-side
    **parity gate**: expose the few "thin entry points" the CLI side needs as same-shaped process contracts, so any language
    (Rust CLI / future Python CLI / host scripts) depend only on **one protocol**,
    rather than on the internal structure of a specific language's runtime.

    · The gate is on the SDK side because **algorithms belong to the SDK** (webhook secret generation, registration chain, `webhook_host`
      three-state derivation): what is language-neutral is **data** (binding file format, gateway HTTP), not algorithms.
      ⇒ Callers can only "call", they are not allowed to copy an implementation.
    · This module **owns judgment and orchestration** (2026-10-06 owner contract v1.0 §4.1): isomorphic to the TS-side
      `register-cli.js` is isomorphic —— the determination of naming/derivation/registration/renaming/binding landing/controlled changes/deregistration and
      completed on the SDK side; the caller (CLI) only does **transport dispatch + parameter passing + receiving single-line JSON**,
      no longer assembling the register argv itself, no longer holding SDK algorithms (the previous stance of "judgment stays with the caller" has been abolished).

Process command contract (ABI):

    python -m aimail.sdk_ops <op> [--args '<json-object>']

    stdout  Exactly one line of JSON (**result only on stdout, human-readable text always goes to stderr**):
              Success: {"ok": true, "result": <any>}
              Failure: {"ok": false, "kind": "usage|import|call", "exc": "<exception class name>", "error": "<text>"}
    exit    0 = success · 1 = runtime failure (import/call) · 2 = usage error (usage)
    stderr  Usage hints / exception traceback / SDK's own output (redirected here to ensure single-line stdout)
    --args  Default `{}`; must be a JSON **object**, keys = parameter names for each op

op table (op → SDK thin entry; all idempotent or read-only):

    iter_bindings                  system_id                → aimail_base.iter_agentmail_configs
    ensure_webhook_secret          binding                  → aimail_base.ensure_binding_webhook_secret
    resolve_register_webhook_url   gw, local_webhook_url    → aimail_base.resolve_register_webhook_url
    register_agent_email           gw, system_id, email,    → aimail_base.register_agent_email
                                   webhook_url?, webhook_secret?, manager_address?
    backfill_binding               binding, system_id       → aimail_base.backfill_binding

    · `binding` = the element returned by `iter_bindings` **passed through as-is** (including the SDK-injected `_config_path`,
      it is the sole basis for "where this binding lands" —— assembling paths yourself would bypass SDK's persistence semantics).
    · `gw` = system-level gateway config object (`gateway_url` / `admin_key`): this module uses it to build the gateway client,
      does not read disk itself ⇒ caller remains the "environment controller".
    · `register_agent_email` manager hard-gate semantics unchanged: when manager is missing, SDK raises
      `ManagerRequiredError`, reported **as-is** here (kind=call, exc=ManagerRequiredError),
      never downgraded to empty-value registration.

Signature/correctness single source of truth remains in `aimail_base`; this module only does parameter validation and result wrapping.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess

# Registrar execution timeout limit (seconds)
REGISTRAR_TIMEOUT = 120
import sys
from pathlib import Path

# ── Dual-form bootstrap: this file is in core directory (pysdk/ or site-packages/aimail/) ──
# flat core (aimail_base.py etc.) is in this directory; in package form `python -m aimail.sdk_ops`
# will first execute package __init__ to complete the same bootstrap, this is the fallback for repo form and direct execution.
_CORE = os.path.dirname(os.path.abspath(__file__))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
# owner 2026-10-07: adapter subdirectory has no __init__.py ⇒ must be added to sys.path ✓ (same convention as pysdk/install.py ✓)
# 2026-10-21: old hardcoded dsh dead entry cleared (dsh is node host, pysdk has no dsh/ directory).
# This does **not** import hosts registry: sdk_ops is spawn entry (python -m aimail.sdk_ops),
# bootstrap keeps minimal dependency surface; consistency between directory set and registry adapter_dir is
# guarded by tests/sdk/test_hosts_layer.py::test_sdk_ops_subdir_list_matches_registry.
for _sub in ("hermes", "deer-flow"):
    _p = os.path.join(_CORE, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


class UsageError(Exception):
    """Usage/parameter error (exit 2)."""


def _require(args: dict, key: str) -> object:
    if key not in args:
        raise UsageError(f"missing required argument: {key}")
    return args[key]


def _require_str(args: dict, key: str, *, allow_empty: bool = True) -> str:
    val = _require(args, key)
    if not isinstance(val, str):
        raise UsageError(f"argument {key} must be a string")
    if not allow_empty and not val.strip():
        raise UsageError(f"argument {key} must not be empty")
    return val


def _require_obj(args: dict, key: str) -> dict:
    val = _require(args, key)
    if not isinstance(val, dict):
        raise UsageError(f"argument {key} must be a JSON object")
    return val


# ── Import of SDK thin entries (deferred to call time, to report import failures as kind=import) ──────


def _sdk():
    import aimail_base  # noqa: PLC0415 — deferred import: import failure should be reported as import type not traceback

    return aimail_base


def _gateway_client(gw: dict):
    """Build gateway client based on `gateway_url` / `admin_key` (full set of `aimail_tools._GatewayClient`)."""
    from aimail_tools import _GatewayClient  # noqa: PLC0415

    return _GatewayClient(gw.get("gateway_url", ""), gw.get("admin_key", ""))


# ── ops ─────────────────────────────────────────────────────────────────────


def _op_iter_bindings(args: dict):
    return _sdk().iter_agentmail_configs(_require_str(args, "system_id") if "system_id" in args else "")


def _op_ensure_webhook_secret(args: dict):
    return _sdk().ensure_binding_webhook_secret(_require_obj(args, "binding"))


def _op_resolve_register_webhook_url(args: dict):
    return _sdk().resolve_register_webhook_url(
        _require_obj(args, "gw"), _require_str(args, "local_webhook_url")
    )


def _op_register_agent_email(args: dict):
    client = _gateway_client(_require_obj(args, "gw"))
    return _sdk().register_agent_email(
        client,
        _require_str(args, "system_id", allow_empty=False),
        _require_str(args, "email", allow_empty=False),
        webhook_url=_require_str(args, "webhook_url") if "webhook_url" in args else "",
        webhook_secret=_require_str(args, "webhook_secret") if "webhook_secret" in args else "",
        manager_address=_require_str(args, "manager_address") if "manager_address" in args else "",
    )


def _op_backfill_binding(args: dict):
    path = _sdk().backfill_binding(_require_obj(args, "binding"), _require_str(args, "system_id"))
    return {"path": str(path or "")}


def _op_version(_args: dict) -> dict:
    """SDK capability self-description: package version + the op names this SDK exposes.

    Caller (CLI/host) can use it to verify capabilities **before use**, avoiding old snapshots/old packages giving "seemingly successful
    but actually outdated" conclusions (e.g., old prompt rule logic false positives). No parameters, read-only, idempotent.
    """
    ver = ""
    try:
        from importlib.metadata import version as _v

        ver = _v("agent-aimail")
    except Exception:
        try:
            import aimail  # type: ignore

            ver = str(getattr(aimail, "__version__", ""))
        except Exception:
            ver = ""
    return {"version": ver, "ops": sorted(OPS)}


# ── Self-contained action ops (contract v1.0 §4.1) ─────────────────────────────────────────────
# Same shape as TS side register-cli.js: judgment and orchestration in SDK; caller only passes params, only receives single-line JSON.
# Reuses existing aimail_base thin entries (no new logic); filters kwargs by target function signature, missing params fail loudly.


def _call(fn, **kw):
    """Call after mapping parameters to target function's real signature (unknown kwargs / missing required ⇒ loud failure)."""
    import inspect as _inspect

    sig = _inspect.signature(fn)
    accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    pos_args, call_kw, missing = [], {}, []
    if accepts_kwargs:
        call_kw.update({k: v for k, v in kw.items() if v is not None})
    for name, prm in sig.parameters.items():
        if prm.kind in (prm.VAR_POSITIONAL, prm.VAR_KEYWORD):
            continue
        if name in kw and kw[name] is not None:
            if prm.kind == prm.POSITIONAL_ONLY:
                pos_args.append(kw[name])
            else:
                call_kw[name] = kw[name]
        elif prm.default is prm.empty:
            missing.append(name)
    if not accepts_kwargs:
        allowed = {n for n, prm in sig.parameters.items()
                   if prm.kind in (prm.POSITIONAL_OR_KEYWORD, prm.KEYWORD_ONLY,
                                   prm.POSITIONAL_ONLY)}
        unknown = sorted(set(kw) - allowed)
        if unknown:
            raise RuntimeError(f"{getattr(fn, '__name__', fn)}: unsupported args {unknown}")
    if missing:
        raise RuntimeError(f"{getattr(fn, '__name__', fn)}: missing required parameter {missing}")
    return fn(*pos_args, **call_kw)

def _import_base():
    """Get aimail_base (dual form: flat core directory direct execution / package form aimail.aimail_base)."""
    try:
        import aimail_base  # flat core: directly importable inside pysdk/ (or site-packages/aimail/)
        return aimail_base
    except Exception:  # noqa: BLE001
        from aimail import aimail_base
        return aimail_base


def _sdk_version():
    """SDK version (for handshake): prefer package metadata, then module attribute."""
    try:
        from importlib.metadata import version as _v
        return _v("agent-aimail")
    except Exception:
        pass
    try:
        import aimail
        return getattr(aimail, "__version__", "") or ""
    except Exception:
        return ""


def _load_system(system_id, override=None):
    """System-level config (CLI writes, SDK read-only) + gateway client.

    Default self-locating (reads system-level config written by CLI); only when caller **explicitly** provides gw/admin_key
    (fixture/test scenario) use it to override —— no new ops, no boundary changes.
    """
    base = _import_base()
    cfg = None
    loader = getattr(base, "_load_gateway_config", None)
    if loader:
        try:
            cfg = loader(system_id) or None
        except Exception:
            cfg = None
    ov = override or {}
    # Explicit system_cfg (passed when CLI already holds this system-level config; default still uses self-locating, behavior unchanged)
    given = ov.get("system_cfg")
    if not cfg and isinstance(given, dict) and given:
        cfg = given
    if not cfg and ov.get("gw"):
        cfg = {"gateway_url": ov.get("gw"), "admin_key": ov.get("admin_key") or "",
               "system_id": system_id}
    if not cfg:
        raise UsageError(f"System-level config not found system_id={system_id!r} (must run aimail install first)")
    if not (cfg.get("gateway_url") and cfg.get("admin_key")):
        raise UsageError(
            "System-level config missing gateway_url/admin_key"
            f"(system_id={system_id!r}) —— run aimail install first")
    try:
        from aimail_tools import _GatewayClient
        client = _GatewayClient(cfg.get("gateway_url"), cfg.get("admin_key"))
    except Exception as e:
        raise RuntimeError(f"Failed to construct gateway client: {e}")
    return base, cfg, client


def _agent_binding(base, agent_id, system_id, email=""):
    """Fetch the binding element (same shape as the CLI's current iter_bindings source)."""
    f = getattr(base, "iter_agentmail_configs", None)
    if f:
        try:
            for el in f(system_id) or []:
                if not isinstance(el, dict):
                    continue
                if email and el.get("email") == email:
                    return el
                if agent_id and (el.get("agent_id") == agent_id or el.get("name") == agent_id):
                    return el
        except Exception:
            pass
    g = getattr(base, "load_agent_config", None)
    if g and agent_id:
        try:
            return g(agent_id, system_id) or {}
        except Exception:
            return {}
    return {}


def _expand(tmpl, values):
    """Expand {name}/{agent}/{sid}/{system_id}/{manager}/{home}/{email}/{domain} (pure template, no business logic)."""
    out = []
    for x in tmpl or []:
        x = str(x)
        for k, v in (values or {}).items():
            x = x.replace("{" + k + "}", str(v if v is not None else ""))
        out.append(x)
    return out


def _run_registrar(spec, values=None):
    """Run platform registrar based on spec.kind; supports args_template expansion; unknown kind ⇒ loud failure."""
    kind = str((spec or {}).get("kind") or "")
    raw = spec.get("argv") or spec.get("args_template") or spec.get("args") or []
    argv = _expand(raw, values or {})
    env = dict(os.environ)
    for k, v in (spec.get("env") or {}).items():
        env[str(k)] = str(v)
    py = str(spec.get("python") or "python3")
    if kind == "python_module":
        # Same semantics as current CLI behavior: call the **function** in the module (not `python -m`) ——
        # Direct in-process import call; parameters are mapped by signature using value bags.
        mod_name = str(spec.get("module") or "")
        fn_name = str(spec.get("fn") or "")
        if not (mod_name and fn_name):
            raise UsageError("register_spec.kind=python_module requires module and fn")
        import importlib
        import sys as _sys
        core = str(spec.get("core_dir") or "")
        if core and core not in _sys.path:
            _sys.path.insert(0, core)
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            raise RuntimeError(f"register_spec: failed to import {mod_name}: {e}")
        fn = getattr(mod, fn_name, None)
        if not callable(fn):
            raise RuntimeError(f"register_spec: {mod_name}.{fn_name} is not callable")
        v = values or {}
        import inspect as _ins
        _params = set(_ins.signature(fn).parameters)
        if "config" in _params and "profile_dir" in _params:
            # owner 2026-10-07: adapter new signature (name, profile_dir, config) ⇒ only provide these three ✓ (no longer stuffing aliases ✗)
            bag = {"name": v.get("name"), "profile_dir": v.get("profile_dir") or v.get("home"),
                   "config": v.get("config") or {}}
        else:
            bag = {"name": v.get("name"), "agent": v.get("agent"), "email": v.get("email"),
                   "system_id": v.get("sid"), "sid": v.get("sid"), "manager": v.get("manager"),
                   "home": v.get("home"), "domain": v.get("domain"),
                   "manager_address": v.get("manager"), "profile_dir": v.get("home")}
        sub = list(argv)
        if sub:
            res = fn(*sub)
        else:
            res = _call(fn, **bag)
        return {"rc": 0, "stdout_tail": str(res)[:400], "mode": "inproc"}
    if kind == "node_entry":
        cmd = ["node", str(spec.get("node_path") or "")] + argv
    elif kind == "python_script":
        cmd = [py, str(spec.get("script") or "")] + argv
    elif kind == "python_module":
        cmd = [py, "-m", str(spec.get("module") or "")] + argv
    elif kind == "host_command":
        cmd = [str(spec.get("command") or "")] + argv
    else:
        raise UsageError(f"Unknown register_spec.kind={kind!r} (refusing to silently skip)")
    if not cmd[0] or (len(cmd) > 1 and not cmd[1]):
        raise UsageError("register_spec missing executable body (refusing to be silent)")
    pr = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=REGISTRAR_TIMEOUT)
    if pr.returncode != 0:
        raise RuntimeError(
            f"Registrar failed rc={pr.returncode}: {' '.join(cmd[:2])}: {(pr.stderr or pr.stdout or '')[-400:]}"
        )
    return {"rc": pr.returncode, "stdout_tail": (pr.stdout or "")[-400:]}


def _platform_aliases(args) -> tuple:
    """Platform private root name normalization table (host self-report → SDK internal; owner 2026-10-11 three-source arbitration).

    P3-C(2026-10-17): CLI no longer passes `aliases` in the op envelope (registry `aliases` entries removed).
    The sentinel for the main-agent path is `default` (hermes' root profile id), provided by hermes
    adapter module to the address base name `agent`; platforms without aliases (deerflow, etc.) return an empty table, behavior unchanged.
    """
    spec = args.get("register_spec") or {}
    return ("default",) if str(spec.get("module") or "") == "aimail_hermes" else ()


def _op_assemble(args):
    """Assembly: self-locating config → naming → run platform registrar → rename if necessary → register → persist binding. Only returns the final result."""
    system_id = args.get("system_id") or ""
    email = args.get("email") or ""
    if not system_id:
        raise UsageError("assemble: system_id is required")
    base, syscfg, client = _load_system(system_id, args)
    # agent_id = address name (atext, no dot/space/@); email = target address (can be produced by plan)
    agent_id = args.get("agent_id") or args.get("name") or args.get("requested_name") or ""
    binding_cfg = _agent_binding(base, agent_id, system_id, email)
    # Normalization of the main agent's address name: **do not use the name given by the caller as the base name source**
    # (platform private names pi/main/default are all excluded from naming); only use its identifier when explicitly marked non_main
    _non_main = bool(args.get("non_main"))
    _aid = (args.get("agent_id") or "") if _non_main else "default"
    _req = (args.get("requested_name") or "") if _non_main else "agent"
    plan = base.plan_address_name(
        _req,
        agent_id=_aid,
        domain=args.get("domain") or syscfg.get("domain") or "",
        system_name=args.get("system_name") or syscfg.get("system_name") or "",
        aliases=_platform_aliases(args),
        register_argv=tuple(args.get("register_argv") or ()),
    )
    target_email_pre = email or plan.get("email") or ""
    reg_run = _run_registrar(args.get("register_spec") or {}, {
        "name": (plan.get("reg_as") if _non_main else "agent"),
        "profile_dir": args.get("profile_dir") or args.get("home") or "",
        "config": {
            "gateway_url": args.get("gateway_url") or syscfg.get("gateway_url") or "",
            "admin_key": args.get("admin_key") or syscfg.get("admin_key") or "",
            "domain": args.get("domain") or syscfg.get("domain") or "",
            "system_id": system_id,
        },
    })
    renamed = None
    _old = (plan.get("old_email") or "").strip()
    if plan.get("needs_rename") and not _old:
        sys.stderr.write("assemble: current address unknown ⇒ skip rename (binding not yet established)\n")
    if plan.get("needs_rename") and _old and getattr(base, "rename_address", None):
        renamed = base.rename_address(
            system_id, plan.get("old_email") or "",
            plan.get("new_name") or plan.get("reg_as") or email, binding_cfg)
    webhook_url = args.get("local_webhook_url") or args.get("webhook_url") or ""
    rw = getattr(base, "resolve_register_webhook_url", None)
    if webhook_url and rw:
        webhook_url = rw({"gateway_url": syscfg.get("gateway_url"),
                          "admin_key": syscfg.get("admin_key")}, webhook_url) or webhook_url
    target_email = email or plan.get("email") or ""
    if not target_email:
        raise UsageError("assemble: neither email provided nor target address produced by plan")
    # Double registration avoidance: register_spec already executed ⇒ do not call SDK registration by default (idempotent but hits gateway one extra time);
    # only call when explicitly sdk_register=true or registrar is missing.
    reg = None
    if bool(args.get("sdk_register")) or not (args.get("register_spec") or {}).get("kind"):
        reg = base.register_agent_email(
            client, system_id, target_email, webhook_url=webhook_url or "",
            webhook_secret=args.get("webhook_secret") or "",
            manager_address=args.get("manager_address") or "")
    updates = {k: v for k, v in (("manager_address", args.get("manager_address")),
                                 ("prompt_rules", args.get("prompt_rules")),
                                 ("persona", args.get("persona"))) if v is not None}
    binding_path = None
    ub = getattr(base, "update_binding", None)
    if updates and ub:
        binding_path = str(ub(system_id, binding_cfg, updates))
    return {"ok": True, "email": target_email, "plan": plan, "registrar": reg_run,
            "renamed": renamed, "registered": reg, "binding_path": binding_path,
            "sdk_version": _sdk_version()}


def _op_update(args):
    """Controlled change: action = rename | set-manager | prompt | persona | webhook-secret | backfill | repair."""
    system_id = args.get("system_id") or ""
    if not system_id:
        raise UsageError("update: system_id is required")
    base, syscfg, client = _load_system(system_id, args)
    action = (args.get("action") or "").strip()
    email = args.get("email") or args.get("old_email") or ""
    # repair is a **system-level** action: no target given ⇒ iterate all bindings of that system (same semantics as old CLI's _repair_agentmail_json(sid))
    if action == "repair" and not (args.get("email") or args.get("agent_id")):
        filled_flat = []
        for _el in (base.iter_agentmail_configs(system_id) or []):
            _em = str((_el or {}).get("email") or "")
            _r = _op_update({**args, "action": "repair", "email": _em, "agent_id": _em})
            _f = (_r or {}).get("filled") or ([(k, v) for k, v in (_r or {}).get("result", {}).items()] if False else [])
            filled_flat.extend([f"{_em}:{_x}" for _x in _f])
        return {"ok": True, "action": "repair", "sdk_version": _sdk_version(),
                "filled": filled_flat, "count": len(filled_flat)}
    cfg = _agent_binding(base, args.get("agent_id") or "", system_id, email)
    if action == "rename":
        r = base.rename_address(system_id, args.get("old_email") or email,
                                args.get("new_name") or args.get("name") or "", cfg)
    elif action == "set-manager":
        manager = args.get("manager_address") or base.resolve_manager_address("")
        r = base.set_agent_manager(system_id, email, manager, syscfg, cfg) or {"ok": True}
    elif action == "backfill":
        r = {"binding_path": str(base.backfill_binding(cfg, system_id))}
    elif action == "repair":
        # Repair judgment is contained within the SDK (CLI only triggers). **Idempotent**: only record "fields truly filled in",
        # do not write back if no changes (api_key requires gateway ⇒ not treated as a local gap, avoiding permanent "has changes").
        out = {}
        filled = []
        out = {}
        filled = []
        # ① Reconstructible fields from system-level gateway config (binding missing ⇒ fill empty; gateway config is authoritative)
        for _k in ("gateway_url", "domain", "system_name"):
            if not str(cfg.get(_k) or "").strip():
                _v = str((syscfg or {}).get(_k) or "").strip()
                if _v:
                    cfg[_k] = _v
                    filled.append(_k)
        if not str(cfg.get("manager_address") or "").strip():
            _fn = getattr(base, "resolve_manager_address", None)
            if callable(_fn):
                try:
                    _v = _fn(system_id, cfg.get("email") or "")
                    if _v:
                        cfg["manager_address"] = _v
                        filled.append("manager_address")
                except Exception:
                    pass
        # ② Align webhook_url with **live routing** (replace if not equal; do nothing if equal ⇒ idempotent)
        _local = (args.get("routes") or {}).get(cfg.get("email") or "") or cfg.get("local_webhook_url") or args.get("local_webhook_url") or ""
        _fn = getattr(base, "resolve_register_webhook_url", None)
        if _local and callable(_fn):
            try:
                _want = _fn(syscfg, _local)
                if _want and str(cfg.get("webhook_url") or "").strip() != str(_want).strip():
                    cfg["webhook_url"] = _want
                    filled.append("webhook_url")
            except Exception:
                pass
        try:
            out["secret"] = base.ensure_binding_webhook_secret(cfg)
        except Exception as e:
            out["secret"] = {"error": str(e)}
        if filled:
            try:
                out["backfill"] = str(base.backfill_binding(cfg, system_id))
            except Exception as e:
                out["backfill"] = {"error": str(e)}
        out["filled"] = filled
        r = out
    elif action in ("prompt", "persona", "webhook-secret"):
        # Controlled field value: top-level key > nested `updates`/`fields` (CLI prompt.rs passes updates+fields;
        # TS gate opUpdate has the same shape). Missing value ⇒ loud failure (consistent with TS gate; webhook-secret's valueless
        # provision call is a gap in **both gates**, not downgraded here).
        def _pick(key):
            v = args.get(key)
            if v is not None:
                return v
            for _k in ("updates", "fields"):
                nest = args.get(_k)
                if isinstance(nest, dict) and key in nest:
                    return nest[key]
            return None

        if action == "prompt":
            _rules = _pick("prompt_rules")
            # prompt_rules = list of rule dicts; name validation takes each item's name (same shape as TS gate opUpdate)
            if isinstance(_rules, list):
                _ok = getattr(base, "prompt_rule_name_ok", None)
                for _r in _rules:
                    _name = _r.get("name") if isinstance(_r, dict) else _r
                    if callable(_ok) and not _ok(str(_name or "")):
                        raise UsageError(f"update action=prompt: invalid rule name {_name!r}")
            _val = _rules
        elif action == "persona":
            _val = _pick("persona")
        else:
            _val = _pick("webhook_secret")
        if _val is None:
            raise UsageError(f"update action={action}: missing field to update")
        updates = {"prompt_rules": _val} if action == "prompt" else (
            {"persona": _val} if action == "persona" else {"webhook_secret": _val})
        r = {"binding_path": str(base.update_binding(system_id, cfg, updates))}
    else:
        raise UsageError(f"update: unknown action={action!r} (rename|set-manager|prompt|persona|webhook-secret|backfill|repair)")
    return {"ok": True, "action": action, "sdk_version": _sdk_version(), **(r or {})}


def _op_teardown(args):
    """Teardown: deregister → whitelist cleanup → binding backfill (fixed order; missing target ⇒ loud failure)."""
    system_id = args.get("system_id") or ""
    email = args.get("email") or ""
    mode = args.get("mode") or {}
    wants = mode.get("unregister", True) or mode.get("whitelist") or mode.get("backfill")
    if not wants:
        # Explicitly closing all actions ⇒ idempotent no-op (no network, no target required)
        return {"ok": True, "actions": [], "sdk_version": _sdk_version()}
    if not system_id or not email:
        raise UsageError("teardown: system_id and email are required (missing target when action is present ⇒ loud failure)")
    base, syscfg, client = _load_system(system_id, args)
    actions = []
    if mode.get("unregister", True):
        actions.append({"deregister_agent_email": base.deregister_agent_email(
            client, system_id, email, manager_address=args.get("manager_address") or "")})
    if mode.get("whitelist"):
        actions.append({"cleanup_system_whitelists": base.cleanup_system_whitelists(
            client, system_id, mode.get("addresses") or [email], mode.get("domains") or [],
            mode.get("deregistered") or [email])})
    if mode.get("backfill"):
        cfg = _agent_binding(base, args.get("agent_id") or "", system_id, email)
        actions.append({"backfill_binding": str(base.backfill_binding(cfg, system_id))})
    return {"ok": True, "actions": actions, "sdk_version": _sdk_version()}


def _op_prompt_test(args: dict):
    """Read-only op (contract §4.1 three op additions, owner 2026-10-10 (a) ruling): L5 filtering + matching + name validation.

    Same shape as TS side `opPromptTest` (contract §4.1(2) two-path semantics/JSON consistent):
      Read binding → filter (dict only · name passes predicate · file non-empty · skip if enabled is False · at least one non-empty field, sorted by name)
      → per-rule `prompt_rule_matches` (OR within fields / AND between fields). **No network, no disk writes**.
    CLI `prompt test` L5 surface now calls this op (replacing CLI-side filter mirror + per-rule python shim);
    CLI `prompt add` / `create-file` name validity now reads this op's `name_ok`.

    In: {system_id, email|agent_id, subject, body, sender, recipient, name?}
    Out: {ok, rules:[{name,file,match}], name_ok?, sdk_version}
    """
    base = _import_base()
    system_id = args.get("system_id") or ""
    if not system_id:
        raise UsageError("prompt-test: system_id is required")
    email = str(args.get("email") or "")
    agent_id = str(args.get("agent_id") or "")
    subject = str(args.get("subject") or "")
    body = str(args.get("body") or "")
    sender = str(args.get("sender") or "")
    recipient = str(args.get("recipient") or "")
    # Binding location (email exact > agent_id scan all); read-only, missing binding ⇒ empty rules (CLI side guarantees binding exists).
    cfg = _agent_binding(base, agent_id, system_id, email)
    raw = (cfg or {}).get("prompt_rules")
    kept = []
    if isinstance(raw, list):
        ok_name = getattr(base, "prompt_rule_name_ok", None)
        has_field = getattr(base, "_prompt_rule_has_any_field", None)
        for r in raw:
            if not isinstance(r, dict):
                continue
            name = str(r.get("name", ""))
            if callable(ok_name) and not ok_name(name):
                continue
            if r.get("file") is None or not str(r.get("file")).strip():
                continue
            if r.get("enabled") is False:
                continue
            if callable(has_field) and not has_field(r):
                continue
            kept.append(r)
    kept.sort(key=lambda r: str(r.get("name", "")))
    matches = getattr(base, "prompt_rule_matches", None)
    rules = [
        {
            "name": str(r.get("name", "")),
            "file": str(r.get("file", "")),
            "match": bool(matches(r, subject, body, sender, recipient)) if callable(matches) else False,
        }
        for r in kept
    ]
    out = {"ok": True, "rules": rules, "sdk_version": _sdk_version()}
    name = str(args.get("name") or "")
    if name:
        ok_name = getattr(base, "prompt_rule_name_ok", None)
        out["name_ok"] = bool(ok_name(name)) if callable(ok_name) else False
    return out


OPS = {
    "version": _op_version,
    "iter_bindings": _op_iter_bindings,
    "ensure_webhook_secret": _op_ensure_webhook_secret,
    "resolve_register_webhook_url": _op_resolve_register_webhook_url,
    "register_agent_email": _op_register_agent_email,
    "backfill_binding": _op_backfill_binding,
    # Self-contained action op (contract v1.0 §4.1; caller only does transport dispatch and argument passing)
    "assemble": _op_assemble,
    "update": _op_update,
    "teardown": _op_teardown,
    # Read-only op (contract §4.1 three op additions, owner 2026-10-10 (a) ruling)
    "prompt-test": _op_prompt_test,
}


# ── ABI Shell ────────────────────────────────────────────────────────────────


def _emit(obj: dict) -> None:
    """Write envelope to **real** stdout (exactly one line)."""
    out = sys.__stdout__ or sys.stdout
    out.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    out.flush()


def _parse_args_argv(argv: list) -> dict:
    parser = argparse.ArgumentParser(
        prog="python -m aimail.sdk_ops",
        description="SDK-side executable ops entry (one-line JSON ABI).",
    )
    parser.add_argument("op", choices=sorted(OPS), help="operation name")
    parser.add_argument("--args", default="{}", help="JSON object with the op's arguments")
    ns = parser.parse_args(argv)  # Usage error → argparse own stderr + exit 2
    try:
        parsed = json.loads(ns.args)
    except Exception as e:  # noqa: BLE001
        raise UsageError(f"--args is not valid JSON: {e}") from None
    if not isinstance(parsed, dict):
        raise UsageError("--args must be a JSON object")
    return {"op": ns.op, "args": parsed}


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        parsed = _parse_args_argv(argv)
    except SystemExit as e:  # argparse already printed usage/help, reuse its exit code (--help=0, usage error=2)
        # Note that `e.code or DEFAULT` will swallow 0 into DEFAULT (--help should exit 0) — must check for None.
        return int(e.code) if e.code is not None else EXIT_USAGE
    except UsageError as e:
        print(f"sdk_ops: {e}", file=sys.stderr)
        _emit({"ok": False, "kind": "usage", "exc": "UsageError", "error": str(e)})
        return EXIT_USAGE

    op, args = parsed["op"], parsed["args"]
    # During op execution, redirect stdout to stderr: any prints from the SDK/dependencies themselves must not pollute the single-line JSON.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        try:
            result = OPS[op](args)
        except UsageError as e:
            _emit({"ok": False, "kind": "usage", "exc": "UsageError", "error": str(e)})
            return EXIT_USAGE
        except ImportError as e:
            msg = f"{type(e).__name__}: {e}"
            _emit({"ok": False, "kind": "import", "exc": type(e).__name__, "error": msg})
            return EXIT_ERROR
        except Exception as e:  # noqa: BLE001 — all call-time exceptions reported per ABI, no traceback leakage to stdout
            print(f"sdk_ops: {op} failed: {type(e).__name__}: {e}", file=sys.stderr)
            _emit({"ok": False, "kind": "call", "exc": type(e).__name__, "error": f"{type(e).__name__}: {e}"})
            return EXIT_ERROR
    finally:
        sys.stdout = real_stdout
    _emit({"ok": True, "result": result})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

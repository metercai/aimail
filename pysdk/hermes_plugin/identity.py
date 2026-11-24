# -*- coding: utf-8 -*-
"""Profile-scoped identity for the Hermes plugin (contract §5, §11.2).

Hermes' isolation boundary is the *profile*: its home, session namespace, credentials
and skills belong to it, and a multiplexed gateway serves many of them in one process.
Two consequences this module encodes:

  * ``agent_system_id`` is derived from the PROFILE home (one id per profile), never
    from the OS user's home — otherwise two profiles aggregate each other's mail;
  * the binding/workspace space is the profile home, so ``workspace_path`` in a binding
    is that directory (contract §7.1).

Resolution order is deliberate: the plugin context first (the host's own answer),
environment second (single-profile launches and fixtures), OS home last.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from v2 import agent_system_id as _asid

#: Env fallbacks. Called only when the plugin context cannot answer.
ENV_PROFILE_HOME = ("HERMES_PROFILE_DIR", "HERMES_HOME")


def _ctx_manager_home(ctx: Any) -> str:
    """The plugin manager's immutable home (the CALLING profile), if the host exposes it.

    Read defensively: a host upgrade that renames this private seam must degrade to the
    env/home fallbacks instead of breaking plugin load.
    """
    manager = getattr(ctx, "_manager", None)
    home = getattr(manager, "home_path", None)
    return str(home or "").strip()


def resolve_agent_home(ctx: Any = None, explicit: str = "") -> str:
    """Profile home for this plugin instance, in host-first order."""
    for candidate in (explicit, _ctx_manager_home(ctx)):
        if candidate:
            return candidate
    for name in ENV_PROFILE_HOME:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return os.path.expanduser("~")


def agent_system_id(ctx: Any = None, explicit_home: str = "") -> str:
    """AgentSystemId of THIS profile (contract §5)."""
    home = resolve_agent_home(ctx, explicit_home)
    # Install the resolver so every later call inside this process agrees, even if a
    # host code path asks without passing the context.
    _asid.set_agent_home_resolver(lambda: home)
    return _asid.get_agent_system_id(home)


def workspace_path(ctx: Any = None, explicit_home: str = "") -> str:
    """The space a binding routes into — the profile home (contract §11.2)."""
    return resolve_agent_home(ctx, explicit_home)


def profile_name(ctx: Any = None) -> str:
    """Best-effort profile label for logs only (never a routing key)."""
    name = str(getattr(ctx, "profile_name", "") or "").strip()
    if name:
        return name
    return os.path.basename(resolve_agent_home(ctx)) or "default"


def plugin_id(ctx: Any = None) -> str:
    """The host's plugin identity (used in audit lines, never for authorization)."""
    return str(getattr(ctx, "plugin_id", "") or "aimail").strip() or "aimail"


def get_config(ctx: Any, key: str, default: Optional[Any] = None) -> Any:
    """Read a plugin setting through the host, never from ``os.environ``.

    Under multiplexing ``os.environ`` belongs to the launch profile, so a raw read would
    hand profile A's setting to profile B (R3 in the refactor plan).
    """
    getter = getattr(ctx, "get_config", None)
    if callable(getter):
        try:
            value = getter(key, default)
            if value is not None:
                return value
        except Exception:  # noqa: BLE001 — a broken config read must not break loading
            pass
    return default

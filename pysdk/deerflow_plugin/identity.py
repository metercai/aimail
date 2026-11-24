# -*- coding: utf-8 -*-
"""Identity for the DeerFlow extension (contract §5, §11.1).

DeerFlow is a multi-address host: one gateway process serves many assistants and threads,
so the AgentSystemId belongs to the INSTANCE (the gateway home), while the routing space is
per binding (a thread carries its own workspace). That is the opposite of Hermes, where the
profile is both.

Home resolution order: explicit config → ``DEERFLOW_HOME`` → ``/app`` (the image layout) →
the OS home. Never ``os.getcwd()``: the gateway's cwd is not an identity.
"""
from __future__ import annotations

import os
from typing import Any

from v2 import agent_system_id as _asid

#: Where the image keeps the host tree; also the default binding home parent.
DEFAULT_HOME = "/app"


def resolve_home(config: Any = None, explicit: str = "") -> str:
    cfg = dict(config or {})
    for candidate in (explicit, str(cfg.get("home") or ""), str(os.environ.get("DEERFLOW_HOME") or ""),
                      DEFAULT_HOME):
        if candidate and os.path.isdir(candidate):
            return candidate
    return os.path.expanduser("~")


def agent_system_id(config: Any = None, explicit_home: str = "") -> str:
    """AgentSystemId of THIS gateway instance."""
    home = resolve_home(config, explicit_home)
    _asid.set_agent_home_resolver(lambda: home)
    return _asid.get_agent_system_id(home)


def address_model() -> str:
    """DeerFlow serves many addresses (contract §11.1) — never refused locally."""
    return "multi"


def workspace_path(config: Any = None, explicit: str = "") -> str:
    """Fallback routing space when a binding carries none: the host tree."""
    return explicit or resolve_home(config)

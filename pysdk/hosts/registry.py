# -*- coding: utf-8 -*-
"""Host registry — the single source of truth for Python hosts (the Python counterpart of tssdk hosts/registry.ts).

Add a new python host = add one entry here + one `<adapter_dir>/` adapter directory
(isomorphic to tssdk "add one entry here + one src/{host}/ entry dir").

Field discipline:
  · `home_dir` / `marker` consistent field-by-field with CLI host table `cli/src/hosts/{hermes,deerflow}.rs`
    `home_dir` / `detect.markers` (changes on both sides must be synced).
  · `adapter_dir` = adapter directory name within SDK (note deerflow directory name has hyphen,
    Cannot be used as a package name ⇒ adapter modules use flat sys.path imports, do not import this directory).
"""
from __future__ import annotations

from typing import Dict

HostSpec = Dict[str, str]

HOSTS: Dict[str, HostSpec] = {
    "hermes": {
        # CLI side: cli/src/hosts/hermes.rs — markers=["hermes-agent"], home_dir=".hermes"
        "label": "Hermes",
        "home_dir": ".hermes",
        "marker": "hermes-agent",
        "adapter_dir": "hermes",
        # Registration entry (consistent with CLI Register::PythonModule)
        "register_module": "aimail_hermes",
        "register_fn": "_auto_register_email",
        "install_fn": "install_hermes",
    },
    "deerflow": {
        # CLI side: cli/src/hosts/deerflow.rs — markers=["backend/app/gateway"], home_dir=".deer-flow"
        "label": "DeerFlow",
        "home_dir": ".deer-flow",
        "marker": "backend/app/gateway",
        "adapter_dir": "deer-flow",
        "register_module": "manage",
        "register_fn": "register_agents",
        "install_fn": "install_deerflow",
    },
}


def host_ids() -> list:
    """All known python host ids (registry keys, stable order)."""
    return sorted(HOSTS)


def get_host(host_id: str) -> HostSpec | None:
    """Get host spec by id; unknown id ⇒ None (caller fails loudly, no silent fallback)."""
    return HOSTS.get(str(host_id or "").strip())

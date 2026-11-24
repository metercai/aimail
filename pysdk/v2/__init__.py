# -*- coding: utf-8 -*-
"""aimail.v2 — the shared SSE-V2 transport/routing core (pysdk side).

One contract, two hosts: Hermes (plugin platform adapter) and DeerFlow (Python
extension) both speak this layer, so the second host is an adapter, not a second
implementation of the pull protocol.

Modules
-------
agent_system_id.py  local client identity (contract §5) — stable per agent home
binding.py          which bindings may arm, how they group, and the fresh delivery lookup
pull_client.py      the ONE constructor for an AgentSystemId aggregate pull client
system_pull.py      one SSE/pull loop per (AgentSystemId, Gateway) + accepted/skip stats

Parity: tssdk `src/core/{agent-system-id,poll-entry,system-pull}.ts` and
`src/runtime/{session-router,stream/plan}.ts`. Field names, endpoints, header, log
lines, backoff numbers and skip semantics are kept identical on purpose — the
cross-language test asserts them item by item.

Import form: this subpackage is imported BOTH bare (`import v2`, repo/pysdk layout)
and as part of the installed package (`from aimail.v2 import ...`), exactly like
`pysdk/hosts`. The bootstrap below makes the flat core modules importable in either
layout without duplicating them.
"""
from __future__ import annotations

import os
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isfile(os.path.join(_CORE_DIR, "aimail_base.py")):
    # repo / bundle layout: pysdk/ (or aimail/) holds the flat core modules
    if _CORE_DIR not in sys.path:
        sys.path.insert(0, _CORE_DIR)

from . import agent_system_id, binding, pull_client, system_pull  # noqa: E402,F401

__all__ = ["agent_system_id", "binding", "pull_client", "system_pull"]

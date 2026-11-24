# -*- coding: utf-8 -*-
"""DeerFlow extension: AIMail over one aggregate SSE stream (contract §6, §7, §9).

What the host gets from this package:

  * a **Python extension** (`deerflow.extensions` entry point →
    ``install(registry, config)``) that registers the mail tools through the host's public
    ``PluginContribution`` API — the host owns the model surface, we never patch it;
  * one aggregate stream per ``(AgentSystemId, Gateway)`` started from that install hook;
  * inbound delivery as a host run: ``POST /api/threads`` (idempotent) then
    ``POST /api/threads/{thread_id}/runs`` with an ``Idempotency-Key`` — the host's own
    idempotency is what makes a redelivery safe, and a run record is the "accepted" proof
    the ack waits for (contract §8.3).

Tool naming caveat (measured 2026-11-23): the host hashes extension tool names
(``ext_<namespace>_<name>_<sha256[:12]>``), so the model does NOT see the bare contract
name ``aimail_address``. The tools therefore carry a description that names the intent
explicitly, and the activation SKILL keeps working by description. R15 in
docs/pysdk-v2-sse-refactor-plan.md tracks the product decision (MCP-exposed bare names vs
hashed names).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["install", "PLUGIN_ID"]

#: Stable extension id (the host records it as the `plugins:` entry name).
PLUGIN_ID = "aimail"

_RUNTIME: list = []


def install(registry: Any, config: Any = None) -> None:
    """DeerFlow extension entry point (``module:install``)."""
    from . import tools as _tools
    from .runtime import DeerFlowRuntime

    log = lambda line: logger.info("%s", line)  # noqa: E731
    cfg = dict(config or {})

    # 1) tool surface, from the shared spec (never a second hand-written list)
    names = _tools.register_tools(registry, cfg, log=log)

    # 2) inbound runtime: one aggregate stream per (AgentSystemId, Gateway).
    #    Started here (not on a request) because inbound must work with no user action;
    #    a daemon thread keeps it off the ASGI loop and dies with the host process.
    if cfg.get("inbound", True) is not False:
        rt = DeerFlowRuntime(cfg, log=log)
        _RUNTIME.append(rt)
        rt.start()
    log("[aimail-deerflow] installed (tools=%d)" % len(names))

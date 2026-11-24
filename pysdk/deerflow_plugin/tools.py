# -*- coding: utf-8 -*-
"""Tool registration for the DeerFlow extension (contract §14).

Two host facts shape this file (both measured 2026-11-23 on `aimail-host-deerflow`):

1. ``PluginContribution`` needs ``namespace``/``title``/``enabled=True`` — a contribution
   without them is silently dropped by ``build_plugin_tools`` and the model then reports
   "tool not registered" while ``install()`` had already run.
2. The host **hashes** the model-facing tool name (``ext_<ns>_<name>_<sha256[:12]>``), so
   the bare contract name never reaches the model. Tool descriptions therefore carry the
   intent in plain words ("bind / pause / unbind an AIMail mailbox …") and the activation
   SKILL triggers on that wording. R15 tracks the product decision.

Handlers are the shared MCP-shaped functions; results are returned as-is (the host encodes
them), and the tool group is our own so an operator can disable the surface per agent.
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

#: Group label the host exposes in its tool list (documented, not an internal name).
TOOL_GROUP = "aimail"

#: Intent wording the model sees. The host hashes the tool NAME, so the description is the
#: only stable trigger the SKILL and prompts can rely on.
_INTENT = {
    "aimail_address": ("bind, pause, resume or unbind YOUR AIMail mailbox address "
                       "(use this when the user pastes an AIMail address activation prompt)"),
    "send_mail": "send an email from your AIMail mailbox (reply to a message, write a new one)",
    "search_mail": "search your own mailbox (offline)",
}


def _contribution_class():
    from deerflow_extension_api.plugins import ModelTool, PluginContribution  # host API

    return ModelTool, PluginContribution


def tool_specs() -> List[dict]:
    """Normalised specs from the shared tool-spec (single source, 15 tools)."""
    import aimail_agent as _agent

    out: List[dict] = []
    for tool in _agent.agent_toolset():
        name = str(tool.get("name") or "")
        handler = tool.get("handler")
        if not name or not callable(handler):
            continue
        schema = tool.get("inputSchema") or tool.get("schema") or {"type": "object"}
        out.append({
            "name": name,
            "description": _INTENT.get(name) or str(tool.get("description") or ""),
            "schema": schema,
            "handler": handler,
        })
    return out


async def _invoke(handler: Callable[..., Any], args: Any, _ctx: Any = None) -> Any:
    """Host calls ``handler(args, ToolContext)``; the shared handlers take one dict."""
    payload = dict(args or {}) if not isinstance(args, dict) else dict(args)
    return handler(payload)


def register_tools(registry: Any, config: Any = None, *, log: Optional[Callable[[str], None]] = None,
                   namespace: str = "aimail") -> List[str]:
    """Register the mail toolset through ``registry.plugin(PluginContribution(...))``."""
    log = log or (lambda line: None)
    ModelTool, PluginContribution = _contribution_class()

    specs = tool_specs()
    tools = []
    for spec in specs:
        handler = spec["handler"]

        async def call(args: Any, _ctx: Any = None, _h: Callable[..., Any] = handler) -> Any:
            return await _invoke(_h, args, _ctx)

        tools.append(ModelTool(
            name=spec["name"],
            description=spec["description"],
            input_schema=spec["schema"],
            handler=call,
            group=TOOL_GROUP,
        ))

    contribution = PluginContribution(
        namespace=namespace,
        title="AIMail",
        description="AIMail mailbox tools (bind/pause/unbind, send, search, board).",
        enabled=True,
        tools=tuple(tools),
    )
    accepted = registry.plugin(contribution)
    names = [s["name"] for s in specs]
    if not accepted:
        log("[aimail-deerflow] contribution REJECTED by the host (tools will not reach the model)")
        return []
    log("[aimail-deerflow] mail tools: %d (namespace=%s)" % (len(names), namespace))
    return names

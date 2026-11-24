# -*- coding: utf-8 -*-
"""Tool registration for the Hermes plugin (contract §14).

The tool surface is the shared JSON spec (``pysdk/tool-spec.json`` via
``aimail_agent.agent_toolset()``) — never a second hand-written list, so the MCP surface,
the hermes schemas and this registry cannot drift.

Host call convention: Hermes dispatches ``handler(**arguments)``. The shared handlers take
one ``dict`` (they are the same functions the MCP server exposes), so this module adapts
the two shapes in one place instead of forking the implementations.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Dict, List, Optional

#: The toolset name comes from the contract constant — a literal here would be a second
#: source of truth for a name the host looks up by string.
def _toolset_name() -> str:
    import aimail_contract as _contract

    return str(getattr(_contract, "AGENT_TOOLSET_NAME", "") or "")


def _emit(result: Any) -> Any:
    """Return what the host accepts: a JSON string (measured: dicts are rejected).

    Hermes answers a plugin tool whose handler returns a plain dict with
    ``tool_result_contract / unsupported result type: dict``. The shared handlers all
    return dicts (MCP shape), so the projection to text happens here — one place, every
    tool, and the model still sees the same JSON it would over MCP.
    """
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 — never lose the call over serialization
        return str(result)


def _adapt(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Bridge the host call convention to the shared ``handler(args_dict)`` shape.

    Measured 2026-11-23: Hermes dispatches a plugin tool as ``handler(args_dict)``
    POSITIONALLY (a keyword-only wrapper raised "takes 0 positional arguments but 1 was
    given" and every tool call failed). Accept both shapes — one positional mapping, a
    single ``args``/``arguments``/``input`` keyword, or plain keywords — so the same
    registration works on hosts that pass either way.
    """

    def call(*args: Any, **kwargs: Any) -> Any:
        if len(args) == 1 and isinstance(args[0], dict) and not kwargs:
            return _emit(handler(args[0]))
        if not args and len(kwargs) == 1:
            key = next(iter(kwargs))
            if key in ("args", "arguments", "input") and isinstance(kwargs[key], dict):
                return _emit(handler(kwargs[key]))
        if args:
            payload = args[0] if isinstance(args[0], dict) else {"args": list(args)}
            return _emit(handler(payload))
        return _emit(handler(kwargs))

    call.__name__ = getattr(handler, "__name__", "aimail_tool")
    call.__doc__ = getattr(handler, "__doc__", None)
    return call


def tool_specs() -> List[Dict[str, Any]]:
    """Normalised specs for the host: ``{name, description, schema, handler}``."""
    import aimail_agent as _agent

    out: List[Dict[str, Any]] = []
    for tool in _agent.agent_toolset():
        name = str(tool.get("name") or "")
        handler = tool.get("handler")
        if not name or not callable(handler):
            continue
        schema = tool.get("inputSchema") or tool.get("schema") or {"type": "object"}
        out.append({
            "name": name,
            "description": str(tool.get("description") or ""),
            "schema": {"name": name, "description": str(tool.get("description") or ""),
                       "parameters": schema},
            "handler": _adapt(handler),
            "is_async": inspect.iscoroutinefunction(handler),
        })
    return out


def register_tools(ctx: Any, *, toolset: Optional[str] = None,
                   log: Optional[Callable[[str], None]] = None) -> List[str]:
    """Register every shared tool through the host's public API. Returns the names."""
    log = log or (lambda line: None)
    group = toolset or _toolset_name()
    registered: List[str] = []
    for spec in tool_specs():
        try:
            ctx.register_tool(
                name=spec["name"],
                toolset=group,
                schema=spec["schema"],
                handler=spec["handler"],
                description=spec["description"],
                is_async=spec["is_async"],
            )
            registered.append(spec["name"])
        except Exception as e:  # noqa: BLE001 — one tool must not stop the rest
            log("[aimail-hermes] tool %s not registered: %s: %s"
                % (spec["name"], type(e).__name__, e))
    log("[aimail-hermes] mail tools: %d (toolset=%s)" % (len(registered), group))
    return registered

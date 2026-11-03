"""tool_spec — SSOT projection of the shared AIMail tool spec.

Single source of truth for the 15 mail-tool semantic definitions (names,
descriptions, parameter schemas, required arrays) = ``tool-spec.json``,
shipped alongside this module (repo: ``pysdk/tool-spec.json``; pip wheel:
``aimail/tool-spec.json`` via force-include).

This module is the ONLY place that reads the spec file. Every registration
surface projects from here:
  - MCP fallback service: ``aimail_mcp_server.TOOLS`` (JSON-RPC ``inputSchema``)
  - hermes adapter:       ``hermes/aimail_hermes`` registration schemas
  - TS SDK:               codegen'd ``src/generated/tool-spec.ts`` (build step,
    same JSON — parity-tested both directions)
  - parity gates:         assert each projection == the spec file (drift is
    structurally impossible: one text, three projections)

The spec shape IS the JSON Schema form: ``parameters`` =
``{"type": "object", "properties": {...}, "required": [...]}``. Hosts that
need per-parameter ``required?: true`` (dsh) convert at their adapter layer
(``toDshParam``), same as the JSON-Schema ``required`` array → TypeBox
conversion the openclaw/pi adapters already do.

Deliberately zero side effects (no imports of the mail core, no env reads):
it must load in the parity-gate subprocess before any SDK module exists.
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
SPEC_PATH = os.path.join(_HERE, "tool-spec.json")


def load_tool_spec() -> list:
    """Read the shared tool spec (list of 15 tool entries, in order)."""
    with open(SPEC_PATH, encoding="utf-8") as f:
        spec = json.load(f)
    if not isinstance(spec, list) or not spec:
        raise RuntimeError(f"tool spec malformed (not a non-empty list): {SPEC_PATH}")
    for t in spec:
        if not t.get("name") or not t.get("description"):
            raise RuntimeError(f"tool spec entry missing name/description: {t!r}")
        if t.get("parameters", {}).get("type") != "object":
            raise RuntimeError(f"tool spec entry parameters must be JSON Schema object: {t.get('name')}")
    return spec


def mcp_tools() -> list:
    """MCP ``tools/list`` shape: {name, description, inputSchema}.

    inputSchema is the spec's ``parameters`` verbatim (JSON Schema object) —
    the MCP protocol speaks JSON Schema natively, no conversion.
    """
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["parameters"],
        }
        for t in load_tool_spec()
    ]


def hermes_schemas() -> dict:
    """Hermes ``registry.register(schema=...)`` shape, keyed by tool name.

    The host overwrites ``schema["name"]`` with the registered bare name
    (tools/registry.py), so no ``name`` key is declared here — the dead
    ``a2a_*`` values are gone structurally.
    """
    return {
        t["name"]: {
            "description": t["description"],
            "parameters": t["parameters"],
        }
        for t in load_tool_spec()
    }


def tool_names() -> list:
    """The 15 bare tool names, in spec order."""
    return [t["name"] for t in load_tool_spec()]

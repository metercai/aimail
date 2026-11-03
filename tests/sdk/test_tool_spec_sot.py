"""SSOT parity gate (Python side) — the Py projections must equal the spec.

2026-11-03: the 15 mail-tool semantic definitions have a single source,
``pysdk/tool-spec.json``. Every Py registration surface projects from it:

  - MCP fallback:  ``aimail_mcp_server.TOOLS`` == ``tool_spec.mcp_tools()``
  - hermes:        ``tool_spec.hermes_schemas()`` (consumed by the adapter's
                   registration loop)

This test asserts both projections against the JSON file, so any drift
between the spec and what the agents actually see turns the L0 gate red.
The TS side asserts its codegen + projection against the same file in
tssdk .../test/core/tool-registry.test.ts; together the three projections
are pinned to one text.

Deliberately imports ``tool_spec`` (zero side effects) and
``aimail_mcp_server`` (module-level TOOLS projection) only — no host.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "pysdk"))

import tool_spec  # noqa: E402
from tool_spec import load_tool_spec, mcp_tools, hermes_schemas, tool_names  # noqa: E402

import aimail_mcp_server  # noqa: E402

SPEC = load_tool_spec()
SPEC_PATH = REPO_ROOT / "pysdk" / "tool-spec.json"


def _raw_spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def test_spec_file_is_the_source_and_well_formed():
    raw = _raw_spec()
    assert isinstance(raw, list) and len(raw) == 15
    for t in raw:
        assert t["name"] and t["description"]
        assert t["parameters"]["type"] == "object"
        assert isinstance(t["parameters"].get("required", []), list)
    # the loader sees the same file it asserts against
    assert [t["name"] for t in SPEC] == [t["name"] for t in raw]


def test_mcp_tools_project_the_spec_verbatim():
    proj = mcp_tools()
    assert len(proj) == 15
    for t, p in zip(SPEC, proj):
        assert p["name"] == t["name"]
        assert p["description"] == t["description"]
        assert p["inputSchema"] == t["parameters"]


def test_mcp_server_tools_is_the_projection():
    # the server module's TOOLS table is the projection itself (not a copy)
    assert len(aimail_mcp_server.TOOLS) == 15
    for t, p in zip(SPEC, aimail_mcp_server.TOOLS):
        assert p["name"] == t["name"]
        assert p["description"] == t["description"]
        assert p["inputSchema"] == t["parameters"]


def test_hermes_schemas_project_the_spec_verbatim():
    schemas = hermes_schemas()
    assert sorted(schemas) == sorted(t["name"] for t in SPEC)
    for t in SPEC:
        s = schemas[t["name"]]
        assert s["description"] == t["description"]
        assert s["parameters"] == t["parameters"]
        # no dead schema.name key (the host overwrites it with the bare name)
        assert "name" not in s


def test_tool_names_match_the_spec_order():
    assert tool_names() == [t["name"] for t in SPEC]


def test_b1_board_tools_take_the_address_not_an_id():
    for t in SPEC:
        if t["name"] in {"board_status", "board_task_list", "board_task_show",
                         "board_heartbeat", "board_members", "board_roles"}:
            params = t["parameters"]
            assert params.get("required", []) and "board" in params["required"], t["name"]
            desc = params["properties"]["board"]["description"].lower()
            assert ".a2a@" in desc, f"{t['name']} board param must name the address form"
        if t["name"] in {"board_task_show", "board_heartbeat"}:
            assert "task_id" in t["parameters"]["required"], t["name"]


def test_role_calibrator_registered_and_argless():
    rc = next(t for t in SPEC if t["name"] == "role_calibrator")
    assert rc["parameters"]["properties"] == {}
    assert rc["parameters"]["required"] == []
    assert "manage_contacts" in [t["name"] for t in SPEC]
    mc = next(t for t in SPEC if t["name"] == "manage_contacts")
    assert "update" in mc["parameters"]["properties"]["action"]["enum"]


def test_email_summary_found_semantics():
    es = next(t for t in SPEC if t["name"] == "email_summary")
    assert "found" in es["description"]

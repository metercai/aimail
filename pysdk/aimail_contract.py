# -*- coding: utf-8 -*-
"""aimail_contract — AIMail agent-side contract constants (sole copy on the Python side).

⚠ Values must equal repo root ``contract/aimail-contract.json`` (single source of truth).
This file is in **hardcoded constants + gate assertions** form (not runtime file reading): wheel (hatchling
force-include)/runtime bundling are self-contained, release artifacts do not have repo root contract/ directory,
Reading the manifest at runtime will crash directly under pip/bundled form —— so the true source is written to disk, constants are hardcoded, by
``tests/contract/check-contract-single-source.py`` asserts item by item in the L0 gate that the two are equal
(drift turns red and provides file:line), ``verify_against_manifest()`` is the library form of the same assertion.

Boundary (owner ruling 2026-09-27): the legitimate ``aimail`` surface = product/repo/framework/CLI names +
``~/.aimail`` + ``AIMAIL_*`` environment variables —— these are **not** internal agent contract names, do not
judge violations based on this module. This module only constrains the agent internal contract surface: skill/toolset registration names, binding
filename, pointer filename, inbound path.
"""

from __future__ import annotations

import json
import os

# ── Contract values (must equal contract/aimail-contract.json)─────────────────────────

#: Fixed inbound paths for the four platforms (openclaw / dsh / pi / deer-flow).
INBOUND_PATH = "/aimail/inbound"

#: hermes is the only exception: inbound is hooked onto the hermes gateway's own webhook routing.
HERMES_INBOUND_PATH = "/webhooks/aimail-inbound"

#: hermes gateway route name (route key in webhook_subscriptions.json).
HERMES_ROUTE_NAME = "aimail-inbound"

#: Internal agent skill registration name == SKILL.md frontmatter ``name:`` (directory name is the same).
AGENT_SKILL_NAME = "agentmail"

#: agent internal toolset registration name == SKILL.md frontmatter ``toolset:`` / hermes
#: ``platform_toolsets`` key name.
AGENT_TOOLSET_NAME = "agentmail"

#: Binding file name for each address (layout systems/{sid}/{cleaned_addr}/agentmail.json).
BINDING_FILE = "agentmail.json"

#: System pointer file name (e.g., ~/.hermes/.agentmail, ~/.deer-flow/.agentmail).
POINTER_FILE = ".agentmail"

#: Default value for inbound listening port —— **port is configurable, path is immutable**.
INBOUND_PORTS = {"dsh": 9099, "pi": 9101, "deerflow": 8001}

# Note: The former BRIDGE_DEFAULT_PATH constant was retired with the SDK de-bridging (owner decision 2026-09-28)—— SDK
# is bridge-agnostic, routing is self-held by CLI (cli/bridge_wire.py); contract true source no longer contains bridge keys.

#: Manifest path relative to repo root (for gate/library validation).
MANIFEST_REL_PATH = os.path.join("contract", "aimail-contract.json")

# Manifest key → module attribute (for constant consistency assertion; Rust side not covered in this batch = GAP).
MANIFEST_MAP = {
    "inbound_path": "INBOUND_PATH",
    "hermes_inbound_path": "HERMES_INBOUND_PATH",
    "hermes_route_name": "HERMES_ROUTE_NAME",
    "agent_skill_name": "AGENT_SKILL_NAME",
    "agent_toolset_name": "AGENT_TOOLSET_NAME",
    "binding_file": "BINDING_FILE",
    "pointer_file": "POINTER_FILE",
    "inbound_ports": "INBOUND_PORTS",
}


def inbound_url(port: int, host: str = "127.0.0.1") -> str:
    """Build local inbound receiving endpoint (non-hermes platforms)."""
    return "http://%s:%d%s" % (host, int(port), INBOUND_PATH)


def hermes_inbound_url(port: int, host: str = "127.0.0.1") -> str:
    """Build hermes in-process inbound receiving endpoint."""
    return "http://%s:%d%s" % (host, int(port), HERMES_INBOUND_PATH)


def load_manifest(path: str | None = None) -> dict:
    """Read manifest. path default = derive repo root from this file's location (set AIMAIL_CONTRACT_MANIFEST
    Overridable —— three-state drill/negative case for access control). Throw an exception when the file is missing/unparseable, caller decides
    exit code (gate judges rc=2 = cannot judge, do not pretend to pass)."""
    p = path or os.environ.get("AIMAIL_CONTRACT_MANIFEST", "")
    if not p:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", MANIFEST_REL_PATH)
    with open(os.path.abspath(p), "r", encoding="utf-8") as f:
        return json.load(f)


def verify_against_manifest(path: str | None = None) -> list:
    """Assert item-by-item that module constants == manifest. Returns list of mismatch descriptions (empty = consistent)."""
    m = load_manifest(path)
    bad = []
    for key, attr in MANIFEST_MAP.items():
        if key not in m:
            bad.append("manifest missing key: %s" % key)
            continue
        want, got = m[key], globals()[attr]
        if want != got:
            bad.append("%s: constant %r != manifest %r" % (attr, got, want))
    return bad


if __name__ == "__main__":  # pragma: no cover - manual self-check
    _bad = verify_against_manifest()
    print("aimail_contract vs manifest: %s" % ("OK" if not _bad else "; ".join(_bad)))
    raise SystemExit(1 if _bad else 0)

#!/usr/bin/env python3
"""No-local-endpoint gate — the naming contract carries NAMES, never an inbound endpoint.

History (all 2026-11-25):
  * the v1 hermes webhook path/route (`hermes_inbound_path` / `hermes_route_name`) left with the
    source-patch adapter;
  * then the loopback listener itself left: `inbound_path` / `inbound_port`, `core/listen.ts`,
    pi's listener, dsh's HTTP handler and openclaw's in-gateway route are gone, because SSE-V2
    delivers mail by pulling the gateway stream.

Why a gate: a local endpoint is seductive — it makes a host *look* deliverable ("we answer on
:9099"), it needs port-collision logic, and it forces every adapter to maintain a second delivery
path that must stay behaviour-identical to the pull path (that duplication is exactly what hid the
missing E1 edge on dsh's shipped hub). So the absence is pinned here:

  1. the manifest declares no endpoint key;
  2. neither mirror (`pysdk/aimail_contract.py`, `tssdk/.../core/contract.ts`) defines one;
  3. no source file references a listener symbol, an inbound path/port or the retired notify channel;
  4. `pysdk/hermes/` (the v1 adapter) does not come back.

Exit codes: 0 = clean, 1 = regression, 2 = cannot judge (manifest unreadable).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "contract" / "aimail-contract.json"

#: Keys that must never re-enter the naming contract.
FORBIDDEN_KEYS = ("inbound_path", "inbound_port", "hermes_inbound_path", "hermes_route_name")

MIRRORS = (
    "pysdk/aimail_contract.py",
    "tssdk/packages/agent-aimail/src/core/contract.ts",
)
FORBIDDEN_CONSTS = ("INBOUND_PATH", "INBOUND_PORT", "HERMES_INBOUND_PATH", "HERMES_ROUTE_NAME")

#: Listener artifacts and retired pieces of the push surface.
FORBIDDEN_SYMBOLS = (
    "listenWithRetry",
    "createInboundHandler",
    "notifyInboundForSystem",
    "setInboundEndpoint",
    "inboundWebhookUrl",
    "openclawWebhookUrl",
)
FORBIDDEN_LITERALS = ("/aimail/inbound", "/webhooks/aimail-inbound")
#: Whole v1 push/CLI surface that must not come back (2026-11-25 "keep only SSE-V2" ruling).
V1_PATHS = (
    "pysdk/hermes",
    "pysdk/deer-flow",
    "pysdk/hosts",
    "pysdk/install.py",
    "pysdk/sdk_ops.py",
)

SCAN_DIRS = ("pysdk", "tssdk/packages/agent-aimail/src", "cli", "tests")
#: The endpoint LITERAL is only forbidden on the v2 face: the still-shipped v1 deer-flow adapter owns
#: its in-process route string until that adapter retires (its own retirement task, not this gate).
LITERAL_SCOPE = (
    "pysdk/v2",
    "pysdk/hermes_plugin",
    "pysdk/deerflow_plugin",
    "tssdk/packages/agent-aimail/src",
)
SCAN_SUFFIXES = {".py", ".ts", ".mjs", ".js", ".sh"}
#: Files allowed to name the retired things: this gate, the wheel/release absence lists and the
#: regression pins that assert the retirement (they are supposed to spell the old names out).
ALLOW = {
    "tests/contract/check-legacy-push-scope.py",
    # v1 deer-flow adapter + the tests that pin ITS behaviour (it keeps its own route literal).
    "tests/sdk/test_deerflow_inbound_route.py",
    "tests/sdk/test_bridge_route_side.py",
    # the retirement pins: they must be able to spell the old names out.
    "tests/sdk/test_legacy_surface_pin.py",
    "tests/sdk/test_wheel_manifest.py",
    "tests/sdk/release/l2-verify-wheel.sh",
}


def strip_comments(text: str, suffix: str) -> str:
    """Comments do not bind code — a doc comment may explain what was retired."""
    if suffix == ".py":
        return "\n".join(re.sub(r"#.*$", "", line) for line in text.splitlines())
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def main() -> int:
    try:
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — cannot judge is not a pass
        print(f"[no-local-endpoint] CANNOT JUDGE: {MANIFEST.relative_to(REPO)} unreadable: {exc}")
        return 2

    problems: list[str] = []

    # 1) manifest: naming only.
    for key in FORBIDDEN_KEYS:
        if key in man:
            problems.append(f"manifest declares `{key}` again — the naming contract carries no endpoint")

    # 2) mirrors: no endpoint constants.
    for rel in MIRRORS:
        path = REPO / rel
        if not path.is_file():
            problems.append(f"mirror `{rel}` is missing")
            continue
        body = strip_comments(path.read_text(encoding="utf-8"), path.suffix)
        for const in FORBIDDEN_CONSTS:
            if re.search(rf"\b{const}\b", body):
                problems.append(f"{rel} defines/uses `{const}` — the endpoint constants are retired")

    # 3) the whole v1 push/CLI surface must not come back.
    for rel in V1_PATHS:
        if (REPO / rel).exists():
            problems.append(
                f"`{rel}` reappeared — that is v1 push/CLI surface, retired 2026-11-25 "
                f"(SSE-V2 pulls; a binding comes from an activation code)"
            )

    # 4) no listener symbols / endpoint literals anywhere in source or fixtures.
    scanned = 0
    for base in SCAN_DIRS:
        root = REPO / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            rel = path.relative_to(REPO).as_posix()
            if rel in ALLOW:
                continue
            scanned += 1
            body = strip_comments(path.read_text(encoding="utf-8", errors="replace"), path.suffix)
            for sym in FORBIDDEN_SYMBOLS:
                if sym in body:
                    problems.append(f"{rel} references `{sym}` — no adapter binds a local endpoint any more")
            in_literal_scope = any(rel.startswith(prefix) for prefix in LITERAL_SCOPE)
            for lit in FORBIDDEN_LITERALS if in_literal_scope else ():
                if lit in body:
                    problems.append(
                        f"{rel} references the endpoint literal `{lit}` — a v1 adapter may keep its own "
                        f"route string, but it must not re-enter this surface"
                    )

    if problems:
        print("[no-local-endpoint] FAIL — the endpoint surface came back:")
        for p in problems:
            print(f"  · {p}")
        return 1

    print(
        f"[no-local-endpoint] clean: manifest keys = {sorted(k for k, v in man.items() if not str(k).startswith('_') and not isinstance(v, dict))} "
        f"(naming only) · no endpoint constants in either mirror · {scanned} file(s) scanned · "
        f"no listener symbol or endpoint literal · v1 paths absent ({', '.join(V1_PATHS)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

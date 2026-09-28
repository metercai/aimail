"""Registration webhook_url — the 2026-08-18 three-state ruling, converged 2026-09-28.

Measured 2026-09-27 (L2 journey J4e + a direct probe against the fixture gateway):
`system_domains.webhook_url` is handed straight to reqwest, so the cloud can only ever
deliver to an absolute http(s) URL. `'127.0.0.1'` and even the scheme-less
`'127.0.0.1:18789'` — the shape the CLI derived for the "bridge push" case — both die
with `request error: builder error`; the only visible trace was the mail being promoted
to overlimit, i.e. a silent inbound black hole.

The SDK de-bridging (owner ruling 2026-09-28) leaves ONE declaration to read:
`webhook_host`. Three states, in this order:

  ① the key is absent              -> the binding's own local endpoint (direct / standalone)
  ② explicit empty ("", "   ")     -> "" = pull (the cloud does not call back)
  ③ a deliverable absolute http(s) -> use it (the environment master declared a push entry)
  ③' anything else (bare host / host:port) -> LOUD warning + the local endpoint; a value
     the cloud cannot POST to must never be registered (builder error)

The old A2 third source — the URL the SDK captured from the bridge's own response
(`webhook_register_url` on disk) — is RETIRED: a residual key is ignored, and the SDK
never writes it. Bridge semantics do not belong to the SDK; the CLI owns the environment.

The old helpers this file used to pin (`is_deliverable_webhook_url`,
`is_bridge_host_port`, `store_bridge_register_url`, `bridge_register_url_path`) are gone
with the bridge — the symbol-level ratchet lives in tests/test_bridge_route_side.py.
"""
import ast
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysdk"))

import aimail_base as core  # noqa: E402
from aimail_contract import HERMES_INBOUND_PATH, INBOUND_PATH  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCAL = "http://127.0.0.1:18789" + INBOUND_PATH
#: what the bridge's own response reported while it was still in push mode (retired)
BRIDGE_URL = "http://127.0.0.1:38081" + HERMES_INBOUND_PATH
PLATFORM_URL = "http://127.0.0.1:18789" + INBOUND_PATH


# ── ① absent / ② explicit empty ───────────────────────────────────────────────
def test_absent_key_registers_the_local_endpoint():
    assert core.resolve_register_webhook_url({}, LOCAL) == LOCAL
    assert core.resolve_register_webhook_url(None, LOCAL) == LOCAL


def test_explicit_empty_means_pull_mode():
    assert core.resolve_register_webhook_url({"webhook_host": ""}, LOCAL) == ""
    assert core.resolve_register_webhook_url({"webhook_host": "   "}, LOCAL) == ""


# ── ③ a deliverable absolute URL is the declaration ───────────────────────────
def test_deliverable_webhook_host_is_passed_through():
    url = "http://1.2.3.4:38081" + HERMES_INBOUND_PATH
    assert core.resolve_register_webhook_url({"webhook_host": url}, LOCAL) == url
    assert core.resolve_register_webhook_url({"webhook_host": PLATFORM_URL}, LOCAL) == PLATFORM_URL


# ── ③' non-deliverable declarations never become the registration value ───────
def test_bare_webhook_host_registers_the_local_endpoint():
    """The regression: a bare host must never become the registration value."""
    assert core.resolve_register_webhook_url({"webhook_host": "127.0.0.1"}, LOCAL) == LOCAL
    # host:port cannot be delivered either (builder error) — same safe fallback
    assert core.resolve_register_webhook_url({"webhook_host": "1.2.3.4:38081"}, LOCAL) == LOCAL
    for bad in ("example.com/hook", "ftp://host/path", "//host/path", "127.0.0.1"):
        assert core.resolve_register_webhook_url({"webhook_host": bad}, LOCAL) == LOCAL, bad


def test_a_non_deliverable_declaration_is_loud(caplog):
    """③' must warn (never silently register an unreachable value)."""
    with caplog.at_level("WARNING", logger="aimail"):
        core.resolve_register_webhook_url({"webhook_host": "1.2.3.4:38081"}, LOCAL)
    assert any("not an absolute http(s) URL" in r.getMessage() for r in caplog.records)


# ── the retired A2 capture must not influence the value anywhere ──────────────
def test_a_residual_bridge_capture_is_ignored_in_every_state():
    """RED/GREEN anchor: the capture is dead weight — the declaration decides."""
    pull = {"webhook_register_url": BRIDGE_URL, "webhook_host": ""}
    assert core.resolve_register_webhook_url(pull, LOCAL) == ""
    push = {"webhook_register_url": BRIDGE_URL, "webhook_host": PLATFORM_URL}
    assert core.resolve_register_webhook_url(push, LOCAL) == PLATFORM_URL
    unusable = {"webhook_register_url": BRIDGE_URL, "webhook_host": "1.2.3.4:38081"}
    assert core.resolve_register_webhook_url(unusable, LOCAL) == LOCAL
    capture_only = {"webhook_register_url": BRIDGE_URL}
    assert core.resolve_register_webhook_url(capture_only, LOCAL) == LOCAL
    assert core.resolve_register_webhook_url({"webhook_register_url": "127.0.0.1:38081"}, LOCAL) == LOCAL


# ── the SDK does not own the declaration: it reads it, never writes it ────────
_WRITERS = {"dump", "dumps", "write_text", "write_bytes", "save", "writes"}


def _write_calls_receiving_the_declaration(tree: ast.AST) -> list:
    """Every call that looks like a persistence writer and receives a dict literal
    carrying the `webhook_host` key."""
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
        if name not in _WRITERS:
            continue
        for arg in list(node.args) + [k.value for k in node.keywords]:
            for sub in ast.walk(arg):
                if not isinstance(sub, ast.Dict):
                    continue
                keys = [k.value for k in sub.keys if isinstance(k, ast.Constant)]
                if "webhook_host" in keys or "webhook_register_url" in keys:
                    hits.append(f"{name}() writes {[k for k in keys if isinstance(k, str)]}")
    return hits


def test_the_sdk_never_writes_the_env_declaration():
    """The declaration is the environment master's (CLI / install). The SDK reads it
    (`gw.get("webhook_host")`) and must never persist it — nor the retired capture."""
    offenders, reads = [], 0
    for f in sorted((ROOT / "pysdk").rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        text = f.read_text(encoding="utf-8")
        offenders += [f"{f.relative_to(ROOT)}: {h}" for h in
                      _write_calls_receiving_the_declaration(ast.parse(text, filename=str(f)))]
        reads += text.count('"webhook_host"')
    assert offenders == [], "the SDK must not write the declaration: " + "; ".join(offenders)
    assert reads > 0, "positive control: the SDK does read the declaration somewhere"

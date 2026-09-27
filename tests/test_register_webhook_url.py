"""Registration webhook_url (A/B of the 2026-09-27 delivery fix).

Measured that day (L2 journey J4e + a direct probe against the fixture gateway):
`system_domains.webhook_url` is handed straight to reqwest, so the cloud can only ever
deliver to an absolute http(s) URL. `'127.0.0.1'` and even the scheme-less
`'127.0.0.1:18789'` — the shape the CLI derived for the "bridge push" case — both die
with `request error: builder error`; the only visible trace was the mail being promoted
to overlimit, i.e. a silent inbound black hole.

These tests pin the corrected contract of `resolve_register_webhook_url` (the 2026-08-18
three-state ruling, with its first-state precondition actually enforced) and the
`is_bridge_host_port` helper the CLI uses before writing `webhook_host`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysdk"))

import aimail_base as core  # noqa: E402

LOCAL = "http://127.0.0.1:18789/aimail/inbound"


def test_is_deliverable_webhook_url_only_absolute_http_urls():
    assert core.is_deliverable_webhook_url(LOCAL)
    assert core.is_deliverable_webhook_url("https://bridge.example.com/webhooks/aimail-inbound")
    for bad in ("", None, "127.0.0.1", "127.0.0.1:18789", "example.com/hook",
                "ftp://host/path", "//host/path"):
        assert not core.is_deliverable_webhook_url(bad), bad


def test_is_bridge_host_port_accepts_only_host_port():
    for good in ("1.2.3.4:38081", "127.0.0.1:38081", "[::1]:38081"):
        assert core.is_bridge_host_port(good), good
    for bad in ("", "127.0.0.1", "http://127.0.0.1:38081/x", "host:port", "host:0",
                "host:70000", "1.2.3.4:38081/x"):
        assert not core.is_bridge_host_port(bad), bad


def test_bare_webhook_host_registers_the_local_endpoint():
    """The regression: a bare host must never become the registration value."""
    assert core.resolve_register_webhook_url({"webhook_host": "127.0.0.1"}, LOCAL) == LOCAL
    # host:port cannot be delivered either (builder error) — same safe fallback
    assert core.resolve_register_webhook_url({"webhook_host": "1.2.3.4:38081"}, LOCAL) == LOCAL


def test_url_webhook_host_is_passed_through():
    url = "http://1.2.3.4:38081/webhooks/aimail-inbound"
    assert core.resolve_register_webhook_url({"webhook_host": url}, LOCAL) == url


def test_explicit_empty_means_pull_mode():
    assert core.resolve_register_webhook_url({"webhook_host": ""}, LOCAL) == ""
    assert core.resolve_register_webhook_url({"webhook_host": "   "}, LOCAL) == ""


def test_absent_key_means_no_bridge():
    assert core.resolve_register_webhook_url({}, LOCAL) == LOCAL
    assert core.resolve_register_webhook_url(None, LOCAL) == LOCAL


# ── A2 (owner ruling 2026-09-27): the bridge's own push entry wins ──────────────

BRIDGE_URL = "http://127.0.0.1:38081/webhooks/aimail-inbound"


def test_bridge_register_url_wins_over_every_other_state():
    gw = {"webhook_register_url": BRIDGE_URL, "webhook_host": "1.2.3.4:38081"}
    assert core.resolve_register_webhook_url(gw, LOCAL) == BRIDGE_URL
    gw2 = {"webhook_register_url": BRIDGE_URL, "webhook_host": ""}
    assert core.resolve_register_webhook_url(gw2, LOCAL) == BRIDGE_URL


def test_invalid_bridge_register_url_is_ignored_loudly():
    gw = {"webhook_register_url": "127.0.0.1:38081"}
    assert core.resolve_register_webhook_url(gw, LOCAL) == LOCAL


def test_store_bridge_register_url_is_idempotent_and_preserves_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "aimail_home", lambda: tmp_path)
    p = core.bridge_register_url_path("sys1")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"system_id": "sys1", "domain": "d.test", "admin_key": "k"}')

    changed, prev = core.store_bridge_register_url("sys1", BRIDGE_URL)
    assert (changed, prev) == (True, "")
    cfg = __import__("json").loads(p.read_text())
    assert cfg["webhook_register_url"] == BRIDGE_URL
    assert cfg["system_id"] == "sys1" and cfg["admin_key"] == "k", "other keys survive"
    assert oct(p.stat().st_mode)[-3:] == "600", "cfg stays 0600"

    changed2, prev2 = core.store_bridge_register_url("sys1", BRIDGE_URL)
    assert (changed2, prev2) == (False, BRIDGE_URL), "second call must be a no-op"

    changed3, prev3 = core.store_bridge_register_url("sys1", BRIDGE_URL + "?x=1")
    assert changed3 is True and prev3 == BRIDGE_URL, "a changed value must be reported"

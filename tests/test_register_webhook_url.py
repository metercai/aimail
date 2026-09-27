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

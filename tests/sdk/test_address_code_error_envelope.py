"""aimail_address failure feedback — the typed error envelope (design
2026-11-03 §1, owner-approved).

Pins the client-side mapping of ``POST /api/v1/activate-address-code``
failures into the stable envelope::

    {success: False, error_code, error, hint, retryable, status}

The gateway is a loopback stand-in: each case drives the exact status the
real gateway returns for that failure class (404 service absent, 400
invalid/expired code, 403 pre-bind mismatch, 409 already active, 5xx
internal) plus the network-unreachable class (status 0).

Cross-language contract: the ``EXPECTED_ERROR_CODES`` set below is written
independently of the implementation (from the approved design table) and
asserted equal to the set of codes the implementation can emit — the same
set the TS SDK asserts (tests/core/address-code.test.ts), so the two
SDKs cannot drift.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, "pysdk")  # repo pysdk wins over any cli/ shadow

from aimail_tools import _GatewayClient  # noqa: E402

# The stable failure classes (design §1.3). Kept independent of the
# implementation on purpose (gate expectation must not read product data).
EXPECTED_ERROR_CODES = {
    "NO_ACTIVATION_CONTEXT",      # tool level: no local trace + no code
    "ADDRESS_CODE_UNAVAILABLE",   # 404 — this gateway has no address-code service
    "CODE_INVALID",               # 400 — invalid or expired
    "CODE_ADDRESS_MISMATCH",      # 403 — code bound to a different address
    "ADDRESS_ACTIVE",             # 409 — address already has an active key
    "GATEWAY_ERROR",              # 5xx — internal gateway failure
    "NETWORK",                    # transport unreachable / timeout (status 0)
}


class _FakeGateway(BaseHTTPRequestHandler):
    """Loopback stand-in for POST /api/v1/activate-address-code."""

    status = 404
    response: dict = {}

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        payload = json.dumps(_FakeGateway.response).encode("utf-8")
        self.send_response(_FakeGateway.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_a):  # keep pytest output clean
        pass


@pytest.fixture
def gateway():
    _FakeGateway.status = 404
    _FakeGateway.response = {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGateway)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.mark.parametrize(
    "status, body, error_code",
    [
        (404, {"error": "not found"}, "ADDRESS_CODE_UNAVAILABLE"),
        (400, {"error": "invalid or expired activation code"}, "CODE_INVALID"),
        (403, {"error": "activation code is bound to a different address"},
         "CODE_ADDRESS_MISMATCH"),
        (409, {"error": "address already has an active key"}, "ADDRESS_ACTIVE"),
        (500, {"error": "internal error"}, "GATEWAY_ERROR"),
    ],
    ids=["404-unavailable", "400-invalid", "403-mismatch",
         "409-active", "5xx-gateway"],
)
def test_gateway_failure_maps_to_the_typed_envelope(gateway, status, body,
                                                     error_code):
    _FakeGateway.status = status
    _FakeGateway.response = body
    res = _GatewayClient(gateway, "").activate_address_code(
        "shared_a-x", "agent.demo@example.test")

    assert res["success"] is False
    assert res["error_code"] == error_code, res
    assert res["status"] == status, res
    assert res["retryable"] is False, res
    # the gateway's own wording stays visible (message), plus a behaviour
    # hint — hint is never empty
    assert res["error"], res
    assert res["hint"], res


def test_network_unreachable_maps_to_retryable_network():
    # port 1 on loopback: nothing listens ⇒ connection refused ⇒ the
    # transport layer's status-0 shape (the same shape a timeout produces).
    res = _GatewayClient("http://127.0.0.1:1", "").activate_address_code(
        "shared_a-x", "agent.demo@example.test")

    assert res["success"] is False
    assert res["error_code"] == "NETWORK", res
    assert res["status"] == 0, res
    # the ONLY retryable class
    assert res["retryable"] is True, res
    assert res["hint"], res


def test_emitted_error_code_set_matches_the_design_table(gateway):
    """Parity gate: the implementation's full code set equals the design
    table (nothing invented, nothing dropped)."""
    cases = [
        (404, "ADDRESS_CODE_UNAVAILABLE"),
        (400, "CODE_INVALID"),
        (403, "CODE_ADDRESS_MISMATCH"),
        (409, "ADDRESS_ACTIVE"),
        (500, "GATEWAY_ERROR"),
    ]
    emitted = set()
    for status, expected in cases:
        _FakeGateway.status = status
        _FakeGateway.response = {"error": "x"}
        emitted.add(_GatewayClient(gateway, "").activate_address_code(
            "c", "a@b.test")["error_code"])
    emitted.add(_GatewayClient("http://127.0.0.1:1", "").activate_address_code(
        "c", "a@b.test")["error_code"])
    # NO_ACTIVATION_CONTEXT is produced at the tool level (asserted in
    # test_address_lifecycle.py); the client layer covers the other six.
    assert emitted == EXPECTED_ERROR_CODES - {"NO_ACTIVATION_CONTEXT"}


def test_success_envelope_is_untouched(gateway):
    """2xx keeps its exact existing shape (no new required fields)."""
    _FakeGateway.status = 200
    _FakeGateway.response = {
        "raw_key": "rk-1", "system_id": "shared_addr_x",
        "email_address": "a@b.test", "expires_at": "2026-12-31T00:00:00Z",
    }
    res = _GatewayClient(gateway, "").activate_address_code(
        "shared_a-x", "a@b.test")

    assert res["success"] is True
    assert res["raw_key"] == "rk-1"
    assert res["system_id"] == "shared_addr_x"
    assert "error_code" not in res and "hint" not in res

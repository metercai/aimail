"""v1 API signature protocol baseline.

Contract: gateway docs/API-SIGNATURE-PROTOCOL.md; the Rust gateway and
bridge implement the same algorithm. Two Python implementations exist:
pysdk/aimail_base.compute_api_signature (canonical, timestamp injectable)
and cli/check_status._signed_headers (offline self-contained copy). Tests:
1) golden vector (portable to the Rust side for cross-language diff),
2) implementation cross-consistency, 3) edge semantics.
"""
import hmac
import hashlib

from aimail_base import compute_api_signature
from check_status import _signed_headers

GOLDEN_KEY = "0123456789abcdef"
GOLDEN_TS = "1700000000000"
GOLDEN_SIG = "febe8865310009c77673b64a04310ef531ec6e2d49c9d05538a13b7ff80e268d"


def test_golden_vector():
    r = compute_api_signature(
        GOLDEN_KEY, "post", "/api/v1/whoami", b'{"a":1}', timestamp_ms=1700000000000)
    assert r == {"X-Api-Timestamp": GOLDEN_TS, "X-Api-Signature": GOLDEN_SIG}


def test_base_string_independent():
    # manual recomputation of the base string from the documented layout
    key_hash = hashlib.sha256(GOLDEN_KEY.encode()).hexdigest()
    body_hash = hashlib.sha256(b'{"a":1}').hexdigest()
    base = f"POST\n/api/v1/whoami\n{GOLDEN_TS}\n{body_hash}"
    expect = hmac.new(key_hash.encode(), base.encode(), hashlib.sha256).hexdigest()
    assert expect == GOLDEN_SIG


def test_empty_key_returns_none():
    assert compute_api_signature("", "GET", "/api/v1/whoami") is None


def test_check_status_matches_pysdk_same_timestamp():
    # the offline copy timestamps from now; re-derive with pysdk at that ts
    h = _signed_headers(GOLDEN_KEY, "GET", "/api/v1/whoami")
    ts = int(h["X-Api-Timestamp"])
    canonical = compute_api_signature(GOLDEN_KEY, "GET", "/api/v1/whoami",
                                      b"", timestamp_ms=ts)
    assert canonical["X-Api-Timestamp"] == str(ts)
    assert canonical["X-Api-Signature"] == h["X-Api-Signature"]


def test_check_status_headers_structure():
    h = _signed_headers(GOLDEN_KEY, "POST", "/api/v1/ping", b"{}", identity="s1")
    assert h["X-Api-Identity"] == "s1"
    assert len(h["X-Api-Signature"]) == 64  # sha256 hex
    assert h["X-Api-Timestamp"].isdigit()

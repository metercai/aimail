"""gateway_api.whoami — identity is mandatory (gateway >= 1.0.2 contract).

Verifier recheck P3-2 (2026-11-02-handoff-identity-residual-risks.md):
the signature must distinguish "not passed" (TypeError) from "passed blank"
(ValueError), both BEFORE any request goes out. Gateway 1.0.2 answers an
empty ``X-Api-Identity`` with 401 ``identity_required`` and has no
server-side key-scan fallback, so an empty identity can never succeed.

QA case list (must all be covered):
  1 omit identity        -> TypeError, zero requests
  2 empty / blank only   -> ValueError, zero requests
  3 valid identity       -> X-Api-Identity header + Rust-side response shape
  4 parity               -> only the with-identity success path is compared
                            (Rust leg: gateway_standalone_parity.rs)
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_REPO_DIRS = [str(_REPO / "pysdk")]
# Self-contained environment isolation (same bootstrap as
# test_identity_header_contract.py): an earlier test in the same pytest run
# may have loaded the INSTALLED `agent-aimail` wheel, whose __init__ re-
# inserts site-packages/aimail at sys.path[0] and caches `gateway_api` in
# sys.modules — silently shadowing this repo's pysdk/. Drop those copies so
# this test exercises the REPO source.
_SP_MARK = "/site-packages/aimail"
for _name in ("gateway_api", "aimail_base"):
    _mod = sys.modules.get(_name)
    if _mod is not None and _SP_MARK in str(getattr(_mod, "__file__", "")):
        del sys.modules[_name]
for _p in [p for p in sys.path if _SP_MARK in p]:
    sys.path.remove(_p)
    sys.path.append(_p)
for _p in reversed(_REPO_DIRS):
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import gateway_api  # noqa: E402

# Guard: this test must exercise the REPO source, not an installed wheel.
assert str(gateway_api.__file__).startswith(str(_REPO)), (
    f"gateway_api resolved to {gateway_api.__file__} — the installed "
    "agent-aimail package is shadowing the repo sources; this test must "
    f"run against {_REPO / 'pysdk'}"
)


def _spy(monkeypatch):
    """Patch urlopen to record (not send) any attempt, return the list."""
    called = []

    def _fake(req, timeout=None):
        called.append(req)
        raise AssertionError("no network call may go out")

    monkeypatch.setattr(gateway_api.urllib.request, "urlopen", _fake)
    return called


def test_whoami_omitted_identity_type_error_no_request(monkeypatch):
    """QA case 1: omit identity => TypeError, zero requests."""
    called = _spy(monkeypatch)
    try:
        gateway_api.whoami("http://127.0.0.1:9", "deadbeef")
        raise AssertionError("expected TypeError for omitted identity")
    except TypeError as e:
        assert "identity" in str(e)
    assert not called


def test_whoami_empty_identity_value_error_no_request(monkeypatch):
    """QA case 2: empty string => ValueError, zero requests."""
    called = _spy(monkeypatch)
    try:
        gateway_api.whoami("http://127.0.0.1:9", "deadbeef", "")
        raise AssertionError("expected ValueError for empty identity")
    except ValueError as e:
        assert "identity" in str(e).lower()
    assert not called


def test_whoami_blank_identity_value_error_no_request(monkeypatch):
    """QA case 2 (whitespace variant): '   ' => ValueError, zero requests."""
    called = _spy(monkeypatch)
    try:
        gateway_api.whoami("http://127.0.0.1:9", "deadbeef", "   ")
        raise AssertionError("expected ValueError for blank identity")
    except ValueError:
        pass
    assert not called


def test_whoami_with_identity_sends_header_and_shape(monkeypatch):
    """QA case 3: valid identity => header on the wire, Rust-shaped reply."""
    captured = {}

    class _Resp:
        def read(self):
            return (
                b'{"system_id": "sys1", "email": "a@x.io", '
                b'"key_prefix": "aim_", "category": "system"}'
            )

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _Resp()

    monkeypatch.setattr(gateway_api.urllib.request, "urlopen", _fake_urlopen)
    who = gateway_api.whoami("http://127.0.0.1:9", "deadbeef", "sys1")
    assert captured["headers"].get("x-api-identity") == "sys1"
    assert captured["headers"].get("x-api-signature")
    # Same field set the Rust leg returns (gateway whoami.rs):
    assert who == {
        "system_id": "sys1",
        "email": "a@x.io",
        "key_prefix": "aim_",
        "category": "system",
    }

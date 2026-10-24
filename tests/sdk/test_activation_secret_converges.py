"""2026-10-01 owner plan A (activation station): Activating locally generated webhook_secret must align with gateway.

Lesion (measured L2 original text): registration side sha256=31ad2cfe5140 vs local binding sha256=e6b42e73e32b -- Defect 10.
Mechanism: `activate-address-code` body only has {code, email_address} (aimail_tools.py:429), gateway
issues one itself; `activate_address_code_persist` generates another one locally **afterwards** (:516-518) and **never returns it**
⇒ the two are permanently unequal. B (`228ebaa`) fixes the register path, this flow does not go through it ⇒ measured still unequal.
This test: gateway side pre-sets a different secret ⇒ assert activation finalization must align it to **this binding one** (single source of truth = binding).
"""
import logging
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _d in (ROOT / "pysdk",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import aimail_tools  # noqa: E402
from aimail_contract import BINDING_FILE  # noqa: E402 — contract single source: no literals (ratchet collision)

_REAL = aimail_tools._GatewayClient  # capture real class at import time (autouse fixture will override module name)

EMAIL = "agent.converge@demo.test"
GATEWAY_OLD_SECRET = "a" * 64


class _FakeGateway:
    """Gateway stub: already holds a **different** secret (preset), records all update writes."""
    writes: list = []

    def __init__(self, url="", key=""):
        self.url, self.key = url, key

    def list_system_domains(self, sid):
        return [{"id": "9", "domain": EMAIL}]

    def update_system_domain(self, domain_id, webhook_url="", webhook_secret=""):
        _FakeGateway.writes.append((domain_id, webhook_url, webhook_secret))
        return {"status": 200}


def _real_host():
    """Real `_GatewayClient` instance (:74, the method under test is attached to it), instance-level shadowing activates the call."""
    h = _REAL.__new__(_REAL)          # Skip __init__ (signature is irrelevant to the logic under test)
    h.gateway_url = "https://gw.test"
    h.activate_address_code = lambda code, ea: {
        "success": True,
        "system_id": "sys-1",
        "email_address": (ea or "").strip().lower(),
        "raw_key": "k" * 64,
        "expires_at": ""}
    return h


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    _FakeGateway.writes = []
    monkeypatch.setattr(aimail_tools, "_GatewayClient", _FakeGateway)


def _load_binding(home):
    hits = list(pathlib.Path(home).glob("**/" + BINDING_FILE))
    assert hits, "binding file must be persisted under tmp HOME"
    import json
    return json.loads(hits[0].read_text())


def test_activation_converges_gateway_secret_to_the_binding_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    _real_host().activate_address_code_persist("CODE-1", EMAIL)

    binding = _load_binding(tmp_path)
    local = str(binding.get("webhook_secret") or "")
    assert local and len(local) == 64, "binding must carry a 64-hex local secret"

    # The gateway's one (preset different value) must be aligned to this bound one: exactly one write, with non-empty secret, value equal.
    assert len(_FakeGateway.writes) == 1, (
        "expected exactly one gateway sync write, got %r" % (_FakeGateway.writes,))
    _did, _url, _gwsec = _FakeGateway.writes[0]
    assert _gwsec and len(_gwsec) == 64, "sync write must carry a non-empty secret (never url-only)"
    assert _gwsec == local, (
        "gateway must adopt THE binding secret (single source); "
        "gateway=%r binding=%r" % ((_gwsec or "")[:12], local[:12]))


def test_activation_keeps_an_existing_binding_secret_untouched(tmp_path, monkeypatch):
    """Existing secret ⇒ reuse without overwriting (invariant), but still complete the alignment action (idempotent convergence)."""
    monkeypatch.setenv("HOME", str(tmp_path))

    h = _real_host()
    # First run: generate and persist to disk
    h.activate_address_code_persist("CODE-1", EMAIL)
    first = _load_binding(tmp_path)["webhook_secret"]
    _FakeGateway.writes.clear()

    # Second run (same address): existing ⇒ reuse the same one
    h.activate_address_code_persist("CODE-1", EMAIL)
    second = _load_binding(tmp_path)["webhook_secret"]
    assert second == first, "existing binding secret must be reused (no churn)"
    if _FakeGateway.writes:
        assert _FakeGateway.writes[-1][2] == first, "any sync must carry that same secret"


# ── F1 (2026-10-02 Card A wrap-up): invisible row ⇒ no longer kill activation ──────────────────────
class _FakeGatewayNoRow(_FakeGateway):
    """Pure address-level activation scene: this address has **no** system_domains row in the gateway
    (gw log only has domain_created + address_code_activated) ⇒ list returns [];
    `_request` can be set to 401/403 to reproduce agent-scope key being rejected by http.rs:744-747."""

    status = 0

    def list_system_domains(self, sid):
        return []

    def _request(self, method, path):
        return {"status": _FakeGatewayNoRow.status}


def test_activation_continues_when_the_gateway_has_no_visible_row(tmp_path, monkeypatch):
    """F1 RED/GREEN: before the fix, this raises ⇒ **address-level activation fails entirely** ⇒ E2-pull red for 7 rounds."""
    monkeypatch.setattr(aimail_tools, "_GatewayClient", _FakeGatewayNoRow)
    monkeypatch.setenv("HOME", str(tmp_path))
    _FakeGatewayNoRow.status = 0

    res = _real_host().activate_address_code_persist("CODE-1", EMAIL)

    assert res.get("success") is True, res
    binding = _load_binding(tmp_path)
    assert len(str(binding.get("webhook_secret") or "")) == 64, \
        "binding must still be persisted even when the gateway side cannot be aligned"
    assert _FakeGateway.writes == [], "nothing to write without a visible row"


def test_activation_continues_and_calls_out_the_scope(tmp_path, monkeypatch, caplog):
    """F1: 403 ⇒ no exception, and the warning must name the scope (loud ≠ silent)."""
    monkeypatch.setattr(aimail_tools, "_GatewayClient", _FakeGatewayNoRow)
    monkeypatch.setenv("HOME", str(tmp_path))
    _FakeGatewayNoRow.status = 403

    with caplog.at_level(logging.WARNING):
        res = _real_host().activate_address_code_persist("CODE-1", EMAIL)

    assert res.get("success") is True, res
    assert "insufficient scope" in caplog.text, caplog.text[-800:]


def test_activation_still_fails_loudly_when_the_visible_row_cannot_be_written(
        tmp_path, monkeypatch):
    """Card A original intent unchanged: line visible but PUT broken ⇒ must raise(never leave two keys)."""
    monkeypatch.setenv("HOME", str(tmp_path))

    class _Broken(_FakeGateway):
        def update_system_domain(self, *a, **k):
            raise OSError("wire down")

    monkeypatch.setattr(aimail_tools, "_GatewayClient", _Broken)

    with pytest.raises(RuntimeError, match="activation secret sync to gateway failed"):
        _real_host().activate_address_code_persist("CODE-1", EMAIL)

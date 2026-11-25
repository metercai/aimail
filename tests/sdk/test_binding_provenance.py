"""Binding provenance — can this binding file be trusted to arm a mailbox?

The file is a CACHE of a capability, not the capability: the authority is the agent-scope key the
gateway issued when an activation code was consumed (``activate-address-code``), and every pull is
authenticated with it. So a hand-written file buys nothing —

  * without a real key the gateway answers 401 and no mail is delivered;
  * with a real key for THAT address it is the same capability an activation would have granted;
  * a forged ``email`` fetches nothing, because the gateway resolves the address from the key.

What was missing is the diagnostic half: a half-invented file armed silently. The activation chain
now stamps ``binding_source`` + ``activated_at``, and ``pysdk/v2/binding.py`` refuses any binding
that lacks them — unconditionally (nothing has shipped yet, so an unstamped file is hand-written or
a writer bug, never a legacy artefact) — and says why. This suite pins both halves.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pysdk"))

import v2.binding as B  # noqa: E402
from _anchor import ANCHOR_PATH  # noqa: E402


def _activated(**over):
    cfg = {
        "email": "agent@example.com",
        "gateway_url": "https://gw.test",
        "domain": "example.com",
        "system_id": "sys-1",
        "agent_system_id": "asid_abc",
        "api_key": "k" * 64,
        "agent_id": "agent",
        "binding_source": B.BINDING_SOURCE_ACTIVATION,
        "activated_at": "2026-11-25T10:00:00Z",
        # The anchor this address is bound to (contract §7.1). Its IDENTIFIER is registered with
        # the gateway at activation; nothing about it is judged locally any more.
        "workspace_id": ANCHOR_PATH,
        "workspace_path": ANCHOR_PATH,
    }
    cfg.update(over)
    return cfg


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    B._REPORTED_PROVENANCE.clear()
    yield
    B._REPORTED_PROVENANCE.clear()


def test_activation_stamped_binding_is_proven():
    assert B.binding_provenance(_activated()) == (True, "")


def test_hand_written_binding_has_no_stamp():
    cfg = _activated()
    del cfg["binding_source"]
    ok, reason = B.binding_provenance(cfg)
    assert ok is False
    assert "no activation stamp" in reason


def test_forged_stamp_is_not_trusted():
    ok, reason = B.binding_provenance(_activated(binding_source="register-address"))
    assert ok is False and "unknown binding_source" in reason


def test_stamp_needs_a_real_timestamp():
    assert B.binding_provenance(_activated(activated_at=""))[0] is False
    assert B.binding_provenance(_activated(activated_at="yesterday"))[0] is False


@pytest.mark.parametrize("field", B.REQUIRED_BINDING_FIELDS)
def test_every_contract_7_1_field_is_required(field):
    cfg = _activated()
    del cfg[field]
    ok, reason = B.binding_provenance(cfg)
    assert ok is False, f"{field} must be required"
    assert field in reason


def test_arming_refuses_an_unstamped_binding():
    # stamp removed ⇒ the provenance check is what refuses (the anchor is the gateway's business)
    cfg = _activated()
    del cfg["binding_source"]
    assert B.is_armable(cfg, "asid_abc") is False
    assert B._REPORTED_PROVENANCE, "the refusal must be reported, not silent"
    # …once per address, not on every beat
    B.is_armable(cfg, "asid_abc")
    assert len(B._REPORTED_PROVENANCE) == 1


def test_a_stamped_binding_still_arms():
    assert B.is_armable(_activated(), "asid_abc") is True


def test_no_escape_hatch_in_the_environment(monkeypatch):
    """Guards the removal of the old opt-in switch: setting it must change nothing."""
    monkeypatch.setenv("AIMAIL_STRICT_BINDING", "0")
    cfg = _activated()
    del cfg["binding_source"]
    assert B.is_armable(cfg, "asid_abc") is False


def test_a_binding_owned_by_another_agent_system_never_arms():
    assert B.is_armable(_activated(), "asid_other") is False


# ── gateway verdict: the authority, reflected back into arming ────────────────────

def test_gateway_rejection_stops_rearming():
    """401/403 from the gateway is the authoritative verdict — the binding must not re-arm."""
    cfg = _activated()
    assert B.is_armable(cfg, "asid_abc") is True
    B.refuse_after_gateway_rejection(cfg["email"])
    assert B.is_refused_by_gateway(cfg["email"]) is True
    assert B.is_armable(cfg, "asid_abc") is False
    B.reset_gateway_rejections()
    assert B.is_armable(cfg, "asid_abc") is True


def test_gateway_rejection_is_case_insensitive():
    B.refuse_after_gateway_rejection("Agent@Example.COM")
    assert B.is_refused_by_gateway("agent@example.com") is True
    B.reset_gateway_rejections()


def test_gateway_rejection_of_one_address_does_not_affect_another():
    B.refuse_after_gateway_rejection("other@example.com")
    assert B.is_armable(_activated(), "asid_abc") is True
    B.reset_gateway_rejections()

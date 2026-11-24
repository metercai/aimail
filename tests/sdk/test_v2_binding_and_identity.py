# -*- coding: utf-8 -*-
"""V2 core: AgentSystemId derivation and binding selection/grouping (contract §5/§6).

These pin the behaviours the release must keep, mirroring the TS suite item by item
(``agent-system-id`` / ``poll-entry`` / ``stream/plan``) so a py/ts drift is a red test
rather than a field bug.
"""
import json
import os

import pytest

import aimail_base as base
from v2 import agent_system_id as asid_mod
from v2 import binding as binding_mod


# ── AgentSystemId ────────────────────────────────────────────────────────────────

def test_derive_is_deterministic_and_prefixed():
    a = asid_mod.derive_agent_system_id("/tmp/home-a", "host|linux|x86_64|mid")
    b = asid_mod.derive_agent_system_id("/tmp/home-a", "host|linux|x86_64|mid")
    assert a == b
    assert a.startswith("asid_") and len(a) == len("asid_") + 32


def test_derive_changes_with_home_and_fingerprint():
    base_fp = "host|linux|x86_64|mid"
    one = asid_mod.derive_agent_system_id("/tmp/home-a", base_fp)
    other_home = asid_mod.derive_agent_system_id("/tmp/home-b", base_fp)
    other_fp = asid_mod.derive_agent_system_id("/tmp/home-a", "host|linux|x86_64|OTHER")
    assert len({one, other_home, other_fp}) == 3


@pytest.mark.parametrize("home,fp", [("", "fp"), ("/tmp/h", ""), ("   ", "fp")])
def test_derive_refuses_blank_inputs(home, fp):
    """A blank home/fingerprint must raise, never collapse every host onto one id."""
    with pytest.raises(ValueError):
        asid_mod.derive_agent_system_id(home, fp)


def test_env_override_and_reset(monkeypatch):
    monkeypatch.setenv(asid_mod.ENV_AGENT_SYSTEM_ID, "asid_fixed_for_test")
    assert asid_mod.get_agent_system_id("/tmp/whatever") == "asid_fixed_for_test"
    monkeypatch.delenv(asid_mod.ENV_AGENT_SYSTEM_ID)
    asid_mod.reset_for_tests()
    monkeypatch.setenv(asid_mod.ENV_HARDWARE_FINGERPRINT, "fp-fixed")
    one = asid_mod.get_agent_system_id("/tmp/home-a")
    asid_mod.reset_for_tests()
    two = asid_mod.get_agent_system_id("/tmp/home-a")
    assert one == two  # deterministic across cache resets
    assert one == asid_mod.derive_agent_system_id("/tmp/home-a", "fp-fixed")


def test_home_resolver_is_the_host_seam(monkeypatch):
    """Hosts derive from THEIR home (profile/instance), not the OS user's home."""
    monkeypatch.delenv(asid_mod.ENV_AGENT_SYSTEM_ID, raising=False)
    monkeypatch.setenv(asid_mod.ENV_HARDWARE_FINGERPRINT, "fp-fixed")
    asid_mod.set_agent_home_resolver(lambda: "/tmp/profile-home")
    try:
        got = asid_mod.get_agent_system_id()
        assert got == asid_mod.derive_agent_system_id("/tmp/profile-home", "fp-fixed")
    finally:
        asid_mod.set_agent_home_resolver(None)
        asid_mod.reset_for_tests()


# ── binding predicates (B1 regression) ───────────────────────────────────────────

def _cfg(**over):
    cfg = {"email": "a@test.local", "gateway_url": "http://gw", "system_id": "system-1",
           "api_key": "sk-x"}
    cfg.update(over)
    return cfg


def test_v2_binding_is_agent_scope_even_without_v1_prefix():
    """B1: a V2 binding's system_id is ``system-*`` — the V1 prefix alone rejected it."""
    cfg = _cfg(agent_system_id="asid_0123456789abcdef0123456789abcdef")
    assert base.is_v1_agent_scope_binding(cfg) is False
    assert base.is_v2_agent_scope_binding(cfg) is True
    assert base.is_agent_scope_binding(cfg) is True


def test_v1_binding_still_recognised():
    cfg = _cfg(system_id="shared_addr_abc123")
    assert base.is_v1_agent_scope_binding(cfg) is True
    assert base.is_v2_agent_scope_binding(cfg) is False
    assert base.is_agent_scope_binding(cfg) is True


def test_binding_without_key_or_system_is_not_agent_scope():
    assert base.is_agent_scope_binding(_cfg(api_key="")) is False
    assert base.is_agent_scope_binding(_cfg(system_id="")) is False
    assert base.is_agent_scope_binding({}) is False


# ── grouping / representative / counts ───────────────────────────────────────────

def test_is_armable_filters_paused_keyless_and_foreign():
    asid = "asid_self"
    assert binding_mod.is_armable(_cfg(agent_system_id=asid), asid) is True
    assert binding_mod.is_armable(_cfg(agent_system_id=asid, paused=True), asid) is False
    assert binding_mod.is_armable(_cfg(agent_system_id=asid, api_key=""), asid) is False
    assert binding_mod.is_armable(_cfg(agent_system_id="asid_other"), asid) is False
    assert binding_mod.is_armable(_cfg(system_id="shared_addr_x"), asid) is False


def test_group_by_gateway_uses_asid_and_normalised_url():
    a = _cfg(email="b@test.local", agent_system_id="asid_1", gateway_url="http://gw/")
    b = _cfg(email="a@test.local", agent_system_id="asid_1", gateway_url="http://gw")
    c = _cfg(email="c@test.local", agent_system_id="asid_1", gateway_url="http://gw2")
    groups = binding_mod.group_by_gateway([a, b, c])
    assert set(groups) == {("asid_1", "http://gw"), ("asid_1", "http://gw2")}
    gw_members = groups[("asid_1", "http://gw")]
    assert [m["email"] for m in gw_members] == ["a@test.local", "b@test.local"]  # sorted
    assert binding_mod.representative(gw_members) is gw_members[0]


def test_representative_skips_paused_and_reports_all_paused():
    paused = _cfg(email="a@test.local", agent_system_id="asid_1", paused=True)
    active = _cfg(email="b@test.local", agent_system_id="asid_1")
    assert binding_mod.representative([paused, active]) is active
    assert binding_mod.representative([paused]) is None


def test_iter_active_reads_disk_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    asid = "asid_self"
    for sid, addr, extra in (
        ("system-1", "a@test.local", {"agent_system_id": asid}),
        ("system-1", "b@test.local", {"agent_system_id": asid}),
        ("system-2", "a@test.local", {"agent_system_id": asid}),      # copied file
        ("system-1", "c@test.local", {"agent_system_id": "asid_other"}),  # foreign
        ("system-1", "d@test.local", {"agent_system_id": asid, "paused": True}),
    ):
        cfg = _cfg(email=addr, system_id=sid, **extra)
        base.save_agent_config(addr.split("@")[0], cfg, sid)
    got = [c["email"] for c in binding_mod.iter_active(asid)]
    assert got == ["a@test.local", "b@test.local"]  # deduped, sorted, paused/foreign dropped
    assert binding_mod.count_active(asid) == 2


# ── fresh delivery lookup (P0-6/P0-7) ───────────────────────────────────────────

def test_fresh_target_skip_reasons(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    base.save_agent_config("a", _cfg(email="a@test.local", agent_system_id="asid_1"), "system-1")
    base.save_agent_config("p", _cfg(email="p@test.local", agent_system_id="asid_1", paused=True),
                           "system-1")

    cfg, skip = binding_mod.fresh_target("a@test.local", "http://gw")
    assert cfg is not None and skip is None

    _, skip = binding_mod.fresh_target("p@test.local", "http://gw")
    assert skip == binding_mod.SKIP_PAUSED

    cfg, skip = binding_mod.fresh_target("nobody@test.local", "http://gw")
    assert cfg is None and skip == binding_mod.SKIP_UNKNOWN

    # same address, another Gateway ⇒ this delivery is not addressed here (contract §9.1)
    _, skip = binding_mod.fresh_target("a@test.local", "http://other-gw")
    assert skip == binding_mod.SKIP_UNKNOWN


def test_skip_and_accepted_result_shapes():
    assert binding_mod.skip_result(binding_mod.SKIP_PAUSED) == {"status": "skip", "reason": "paused"}
    assert binding_mod.accepted_result()["status"] == "accepted"


# ── contract C1: single-address hosts refuse a second mailbox ────────────────────

def test_single_address_host_refuses_second_bind_without_spending_the_code(tmp_path, monkeypatch):
    """§11.2/C1: refusal is LOCAL and happens BEFORE the activation call."""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    import aimail_tools as tools
    import aimail_base as base_mod

    base_mod.save_agent_config("first", _cfg(email="first@test.local",
                                             agent_system_id="asid_self"), "system-1")
    calls = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        def activate_address_code_persist(self, *a, **k):
            calls.append((a, k))
            return {"success": True, "system_id": "system-2"}

    monkeypatch.setattr(tools, "_GatewayClient", _Client)
    monkeypatch.setattr(base_mod, "get_binding_context",
                        lambda: {"agent_system_id": "asid_self", "address_model": "single"})
    out = tools.aimail_address(action="bind", code="addr-x", email="second@test.local",
                               gateway_url="http://gw")
    assert out["success"] is False and out["reason"] == "second-address"
    assert out["error_code"] == "SINGLE_ADDRESS_HOST" and out["retryable"] is False
    assert calls == [], "the activation code must not be spent on a refused bind"

"""C1 (P6, owner ruling 2026-10-02): plan_address_name = "Fixed Name" single source of truth.

All rules (validation/alias normalization/direct determination F13/email derivation) are in the SDK; platforms.json only provides
Mapping layer data (alias table/template argv), CLI only "fetch plan → execute".
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "pysdk") not in sys.path:
    sys.path.insert(0, str(_REPO / "pysdk"))

import pytest

import aimail_base


def test_invalid_names_raise_valueerror_with_legacy_message():
    for bad in ("", "a.b", "a b", "a@b"):
        with pytest.raises(ValueError, match="illegal address name"):
            aimail_base.plan_address_name(bad, agent_id="agent", domain="d.tm")


def test_alias_normalization_maps_platform_internal_id_to_agent():
    p = aimail_base.plan_address_name("main", agent_id="main", domain="d.tm",
                                      system_name="xixi", aliases=("main",))
    assert p["target_name"] == "agent"
    assert p["target_email"] == "agent.xixi@d.tm"


def test_verbatim_request_keeps_name_and_only_normalizes_aliases():
    p = aimail_base.plan_address_name("weijia", agent_id="main", domain="d.tm",
                                      aliases=("main",))
    assert p["target_name"] == "weijia"
    assert p["target_email"] == "weijia@d.tm"


def test_email_token_means_direct_registration_no_rename():
    p = aimail_base.plan_address_name("alice", agent_id="agent", domain="d.tm",
                                      register_argv=["register", "--email", "{email}"])
    assert p["reg_as"] == "alice" and p["needs_rename"] is False
    assert p["email"] == p["target_email"] == "alice@d.tm"


def test_name_token_means_direct_registration_no_rename():
    p = aimail_base.plan_address_name("alice", agent_id="agent", domain="d.tm",
                                      register_argv=["register", "--name", "{name}"])
    assert p["reg_as"] == "alice" and p["needs_rename"] is False
    assert p["email"] == "alice@d.tm"


def test_registrar_without_name_token_registers_default_then_needs_rename():
    p = aimail_base.plan_address_name("alice", agent_id="agent", domain="d.tm",
                                      register_argv=["register", "--agent", "{agent}"])
    assert p["default_name"] == "agent"
    assert p["reg_as"] == "agent" and p["needs_rename"] is True
    assert p["email"] == "agent@d.tm"          # address registered this time (default name)
    assert p["target_email"] == "alice@d.tm"   # target address after rename closure


def test_shared_domain_infix_applies_to_both_addresses():
    p = aimail_base.plan_address_name("alice", agent_id="agent",
                                      domain="d.tm", system_name="sys1",
                                      register_argv=["--name", "{name}"])
    assert p["email"] == "alice.sys1@d.tm"
    assert p["target_email"] == "alice.sys1@d.tm"

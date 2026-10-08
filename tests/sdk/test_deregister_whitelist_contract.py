"""Deregistration whitelist contract (Python side) — parity with the TS test.

There was no Python test for this step at all (the TS one existed but had drifted
two status strings behind the implementation, commit f829519 / F10), so the same
drift was free to happen here. This pins the current contract on this side:

  rows of ANOTHER address, manager known or not ⇒ "not_found_addr", no delete
  rows of THIS address (exact match or the by-address fallback) ⇒ deleted by id,
      and the fallback is visible as the "(by_addr)" marker
  no rows at all in the domain ⇒ "not_found"
  no domain derivable ⇒ "unsupported"

The by-address fallback (F10, 2026-09-25) is the residue fix: a binding with an
empty manager (or a gateway row with an empty value) used to leave the whitelist
row behind forever, because only a (domain_addr, value) match could delete it.
domain_addr == email already pins the address, so its rows are orphans.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "pysdk") not in sys.path:
    sys.path.insert(0, str(ROOT / "pysdk"))

import aimail_base  # noqa: E402


class FakeClient:
    """Records calls, answers with scripted values (mirrors the TS MockClient)."""

    def __init__(self, rows=None, api_key=None, domains=None):
        self.rows = rows if rows is not None else []
        self.api_key = api_key
        self.domains = domains if domains is not None else []
        self.deleted = []

    # step 1 — api key
    def get_api_key_by_email(self, email):  # noqa: ARG002
        return self.api_key or {}

    def delete_api_key(self, key_id):  # noqa: ARG002
        return {"status": 200}

    # step 2 — domain entry
    def list_system_domains(self, system_id):  # noqa: ARG002
        return self.domains

    def _request(self, method, path, body=None):  # noqa: ARG002
        return {"status": 200}

    # step 3 — whitelist
    def list_whitelists_by_domain(self, domain):  # noqa: ARG002
        return self.rows

    def delete_whitelist_entry_by_id(self, entry_id):
        self.deleted.append(entry_id)
        return {"status": 204}


def _dereg(client, email="agent@test.example", manager="mgr@test.example"):
    return aimail_base.deregister_agent_email(client, "system-test", email, manager)


def test_rows_of_this_address_with_exact_match_are_deleted_by_id():
    client = FakeClient(rows=[
        {"id": 33, "domain_addr": "agent@test.example", "value": "mgr@test.example"},
        {"id": 44, "domain_addr": "other@test.example", "value": "mgr@test.example"},
    ])
    out = _dereg(client)
    assert out["whitelist"] == "204"          # no "(by_addr)" marker: exact match
    assert client.deleted == [33]             # never the other address's row


def test_never_deletes_rows_of_another_address():
    client = FakeClient(rows=[
        {"id": 44, "domain_addr": "other@test.example", "value": "mgr@test.example"},
    ])
    out = _dereg(client, email="ghost@test.example")
    assert out["whitelist"] == "not_found_addr"
    assert client.deleted == []


def test_no_manager_known_still_never_deletes_by_value():
    client = FakeClient(rows=[
        {"id": 44, "domain_addr": "other@test.example", "value": "mgr@test.example"},
    ])
    out = _dereg(client, manager="")
    assert out["whitelist"] == "not_found_addr"
    assert client.deleted == []


def test_empty_value_row_of_this_address_is_drained_by_addr_fallback():
    """F10 residue case: value empty / manager unknown ⇒ fallback, marker printed."""
    client = FakeClient(rows=[
        {"id": 55, "domain_addr": "agent@test.example", "value": ""},
        {"id": 44, "domain_addr": "other@test.example", "value": ""},
    ])
    out = _dereg(client)
    assert out["whitelist"] == "204(by_addr)"
    assert client.deleted == [55]


def test_no_rows_in_the_domain_reports_not_found():
    client = FakeClient(rows=[])
    assert _dereg(client)["whitelist"] == "not_found"


def test_no_derivable_domain_is_unsupported():
    client = FakeClient(rows=[])
    out = aimail_base.deregister_agent_email(client, "system-test", "not-an-address", "")
    assert out["whitelist"] == "unsupported"


def test_rows_without_id_are_skipped_not_deleted():
    """A row without an id cannot be deleted by id ⇒ it must not be guessed at."""
    client = FakeClient(rows=[{"domain_addr": "agent@test.example", "value": "mgr@test.example"}])
    out = _dereg(client)
    assert out["whitelist"] == "not_found_addr"
    assert client.deleted == []

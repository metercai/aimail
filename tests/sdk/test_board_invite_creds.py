"""Board invite → board_creds.json: the board id is derived from the address.

B1 (2026-11-03): the board's email address in the From header is the single
board handle. The 20-hex board_id is derived locally —
``sha256(lower(address))[:20]``, byte-identical to the gateway's
``derive_board_id`` (board/models.rs) — so the gateway no longer prints a
``Board ID:`` line and the SDK no longer reads one. Keying board_creds.json
with the derived id matches the gateway's storage layout exactly.
"""
import hashlib
import json
import sys

import pytest

sys.path.insert(0, "pysdk")

import aimail_base as base  # noqa: E402

SID = "shared_addr_abc123"
ADDR = "boardpy@mail.example.test"
BOARD_ADDR = "brdpy.a2a@mail.example.test"
BOARD_ID = hashlib.sha256(BOARD_ADDR.encode("utf-8")).hexdigest()[:20]

# The gateway's current invite format: no `Board ID:` line (removed with B1).
INVITE_BODY = """── A2A Board ──

Board Invitation
  Board: brdpy
  Board Email: brdpy.a2a@mail.example.test

── Information ──
API: http://127.0.0.1:8080
Token: bdt_0123456789abcdef"""


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "aimail"))
    active = {"system_id": SID, "email": ADDR, "api_key": "k",
              "gateway_url": "http://127.0.0.1:8080"}
    monkeypatch.setattr(base, "_ACTIVE_AGENT_CONFIG", active)
    monkeypatch.setattr(base, "_CONFIG_LOADER", lambda: active)
    seen = {}
    monkeypatch.setattr(base, "_BOARD_GATEWAY_SINK",
                        lambda bid, url: seen.update({bid: url}))
    base._seen = seen
    yield active


def _creds():
    p = base._board_creds_path()
    return json.loads(p.read_text()) if p and p.exists() else {}


def test_invite_keys_creds_with_the_derived_board_id(cfg):
    base._extract_board_gateway(
        {"subject": "[A2A] invite: brdpy",
         "from": "brdpy <brdpy.a2a@mail.example.test>",
         "body": INVITE_BODY})
    creds = _creds()
    assert list(creds) == [BOARD_ID]
    assert creds[BOARD_ID]["token"] == "bdt_0123456789abcdef"
    assert creds[BOARD_ID]["gateway_url"] == "http://127.0.0.1:8080"
    assert base._seen[BOARD_ID] == "http://127.0.0.1:8080"
    # the derived id is a pure function of the address, never the gateway host
    assert BOARD_ID != hashlib.sha256(b"brdpy.a2a@127.0.0.1").hexdigest()[:20]


def test_legacy_invite_with_stale_board_id_line_is_ignored(cfg):
    """Old-format invites (still carrying `Board ID:`) parse by address too —
    the stated line is no longer read, so a stale/wrong id there cannot win."""
    legacy = INVITE_BODY.replace(
        "Token: ", "Board ID: bb74d6a76fe52bc0390e\nToken: ")
    base._extract_board_gateway(
        {"subject": "[A2A] invite: brdpy",
         "from": "brdpy <brdpy.a2a@mail.example.test>",
         "body": legacy})
    assert list(_creds()) == [BOARD_ID]


def test_pull_payload_without_from_key_uses_sender(cfg):
    """Pull bodies carry `sender`; push carries `from`."""
    base._extract_board_gateway(
        {"subject": "[A2A] invite: brdpy",
         "sender": "brdpy <brdpy.a2a@mail.example.test>",
         "body": INVITE_BODY})
    assert list(_creds()) == [BOARD_ID]


def test_non_board_mail_is_ignored(cfg):
    base._extract_board_gateway(
        {"subject": "hello", "from": "someone@example.test", "body": "hi"})
    assert _creds() == {}


def test_invite_without_board_address_is_ignored(cfg):
    """No .a2a@ address in the sender → nothing to derive, nothing stored."""
    base._extract_board_gateway(
        {"subject": "[A2A] invite", "from": "someone@example.test",
         "body": INVITE_BODY})
    assert _creds() == {}

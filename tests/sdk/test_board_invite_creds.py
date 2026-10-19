"""Board invite → board_creds.json: the board id must be the gateway's.

The invite mail states `Board ID:` — authoritative. The fallback derivation is
sha256(FULL board address)[:20], and that address carries the MAIL domain of
the sender, **never the gateway host**. The old code took the domain from the
gateway URL, so whenever the gateway is not served on the mail domain the
derived id did not exist on the gateway: the token was stored under a bogus key
and every board call answered 401 "invalid token or role".
"""
import hashlib
import json
import sys

import pytest

sys.path.insert(0, "pysdk")

import aimail_base as base  # noqa: E402

SID = "shared_addr_abc123"
ADDR = "boardpy@mail.example.test"

INVITE_BODY = """── A2A Board ──

Board Invitation
  Board: brdpy
  Board Email: brdpy.a2a@mail.example.test

── Information ──
API: http://127.0.0.1:8080
Board ID: bb74d6a76fe52bc0390e
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


def test_stated_board_id_wins_over_derivation(cfg):
    base._extract_board_gateway(
        {"subject": "[A2A] invite: brdpy",
         "from": "brdpy <brdpy.a2a@mail.example.test>",
         "body": INVITE_BODY})
    creds = _creds()
    assert list(creds) == ["bb74d6a76fe52bc0390e"]
    assert creds["bb74d6a76fe52bc0390e"]["token"] == "bdt_0123456789abcdef"
    assert creds["bb74d6a76fe52bc0390e"]["gateway_url"] == "http://127.0.0.1:8080"
    assert base._seen["bb74d6a76fe52bc0390e"] == "http://127.0.0.1:8080"


def test_fallback_derives_from_the_mail_domain_not_the_gateway_host(cfg):
    body = INVITE_BODY.replace("Board ID: bb74d6a76fe52bc0390e\n", "")
    base._extract_board_gateway(
        {"subject": "[A2A] invite: brdpy",
         "from": "brdpy <brdpy.a2a@mail.example.test>",
         "body": body})
    want = hashlib.sha256(b"brdpy.a2a@mail.example.test").hexdigest()[:20]
    assert list(_creds()) == [want]
    # the gateway host (127.0.0.1) must never leak into the derivation
    assert want != hashlib.sha256(b"brdpy.a2a@127.0.0.1").hexdigest()[:20]


def test_pull_payload_without_from_key_uses_sender(cfg):
    """Pull bodies carry `sender`; push carries `from`."""
    base._extract_board_gateway(
        {"subject": "[A2A] invite: brdpy",
         "sender": "brdpy <brdpy.a2a@mail.example.test>",
         "body": INVITE_BODY})
    assert list(_creds()) == ["bb74d6a76fe52bc0390e"]


def test_non_board_mail_is_ignored(cfg):
    base._extract_board_gateway(
        {"subject": "hello", "from": "someone@example.test", "body": "hi"})
    assert _creds() == {}

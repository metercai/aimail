"""Anchor identifier — what actually travels to the gateway.

The anchor is an agent-side concept (dsh: workspace path, pi: cwd, openclaw: agent workspace), so the
gateway never learns it. What travels is ``sha256(lower(email) + NUL + anchor)``:

  * registered at activation (``activate-address-code`` body ``anchor_id``, echoed back);
  * presented on every pull (``X-Aimail-Anchor: email;hash[,email;hash...]``);
  * compared by the gateway, which refuses a mismatching pull before handing out any mail.

An earlier revision also kept an ``anchor.json`` ledger next to the binding and judged it at arming.
That was removed (2026-11-25): both files live in the user's home, so a user editing both defeats it,
and the gateway's witness makes it redundant. These tests therefore cover the hash, the header, and
the client wiring — there is no local record left to test.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pysdk"))

import v2.anchor as A  # noqa: E402
import v2.pull_client as pc  # noqa: E402

WS_A = "/work/project-a"
WS_B = "/work/project-b"


def test_anchor_id_is_sha256_of_address_and_anchor():
    want = hashlib.sha256(("agent@example.com" + "\u0000" + WS_A).encode()).hexdigest()
    assert A.anchor_id("Agent@Example.com", WS_A) == want
    assert len(A.anchor_id("a@x.com", WS_A)) == 64


def test_anchor_id_needs_both_sides():
    assert A.anchor_id("", WS_A) == ""
    assert A.anchor_id("a@x.com", "") == ""


def test_different_anchors_give_different_ids():
    assert A.anchor_id("a@x.com", WS_A) != A.anchor_id("a@x.com", WS_B)


def test_separator_prevents_concatenation_collisions():
    # without a separator sha256("ab" + "c") == sha256("a" + "bc")
    assert A.anchor_id("ab@x.com", "c") != A.anchor_id("a@x.com", "bc")


def test_address_is_normalised_and_anchor_trimmed():
    assert A.anchor_id("A@X.com", WS_A) == A.anchor_id("a@x.com", WS_A)
    assert A.anchor_id("a@x.com", " /w ") == A.anchor_id("a@x.com", "/w")


def test_header_lists_each_address_once():
    value = A.anchor_header([
        {"email": "A@x.com", "workspace_path": "/w1"},
        {"email": "a@x.com", "workspace_path": "/w1"},   # duplicate address
        {"email": "b@x.com", "workspace_path": "/w2"},
        {"email": "c@x.com"},                            # no anchor ⇒ nothing to claim
    ])
    parts = value.split(",")
    assert len(parts) == 2
    assert parts[0] == "a@x.com;" + A.anchor_id("a@x.com", "/w1")
    assert parts[1] == "b@x.com;" + A.anchor_id("b@x.com", "/w2")


def test_header_is_empty_when_nothing_to_claim():
    assert A.anchor_header([{"email": "c@x.com"}]) == ""
    assert A.anchor_header([]) == ""


def test_aggregate_client_sends_the_anchor_header():
    client = pc.build_agent_system_client(
        "https://gw.test", "k" * 16, "asid_x", identity="a@x.com",
        members=[{"email": "a@x.com", "workspace_path": WS_A}])
    assert client.extra_headers[A.ANCHOR_HEADER] == "a@x.com;" + A.anchor_id("a@x.com", WS_A)


def test_aggregate_client_omits_the_header_when_nothing_to_claim():
    client = pc.build_agent_system_client("https://gw.test", "k" * 16, "asid_x", identity="a@x.com")
    assert A.ANCHOR_HEADER not in client.extra_headers


def test_activation_sends_the_anchor_id_and_no_record_is_written():
    """The claim is registered with the gateway; nothing local is written alongside the binding."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(here, "..", "..", "pysdk")
    tools = open(os.path.join(root, "aimail_tools.py"), encoding="utf-8").read()
    assert 'body["anchor_id"]' in tools
    anchor_src = open(os.path.join(root, "v2", "anchor.py"), encoding="utf-8").read()
    for gone in ("write_anchor_record", "judge_anchor", "ANCHOR_RECORD_NAME"):
        assert gone not in anchor_src, f"the local anchor ledger is back: {gone}"

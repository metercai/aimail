# -*- coding: utf-8 -*-
"""P4 — outbound Message-ID aliases: replies stay in their thread (contract §6.6/C8).

The fact these cases are built on: a reply to OUR reply carries our own outbound
Message-ID as ``references[0]``, so without an alias the thread root — and therefore the
host session — changes on every round trip.
"""
from aimail.contract import BINDING_FILE
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pysdk"))

from v2 import outbound  # noqa: E402
from v2 import thread  # noqa: E402


def _binding(home: Path, addr: str = "agent@test.local") -> dict:
    """A binding config shaped like the ones activation writes."""
    d = home / "systems" / "system-abc" / addr.replace("@", "_")
    d.mkdir(parents=True, exist_ok=True)
    p = d / BINDING_FILE
    p.write_text(json.dumps({"email": addr, "system_id": "system-abc"}), encoding="utf-8")
    return {"email": addr, "system_id": "system-abc", "config_path": str(p)}


def _reply(refs, message_id="reply-1", in_reply_to=""):
    return ({"message_id": message_id, "references": refs, "in_reply_to": in_reply_to},
            {"message_id": message_id})


# ── store ────────────────────────────────────────────────────────────────────────────

def test_binding_dir_is_the_binding_own_directory(tmp_path):
    cfg = _binding(tmp_path)
    assert outbound.binding_dir(cfg) == str(Path(cfg["config_path"]).parent)
    assert outbound.binding_dir({}) == ""


def test_record_and_lookup_roundtrip(tmp_path):
    cfg = _binding(tmp_path)
    d = outbound.binding_dir(cfg)
    assert outbound.record(d, "<out-1@aimail.test>", "<root@ext.test>") is True
    assert outbound.lookup(d, "<out-1@aimail.test>") == "root@ext.test"
    # Normalisation: brackets and whitespace never change the key.
    assert outbound.lookup(d, "out-1@aimail.test") == "root@ext.test"


def test_record_is_idempotent_and_prunes_to_the_limit(tmp_path):
    d = outbound.binding_dir(_binding(tmp_path))
    for i in range(outbound.ALIAS_LIMIT + 20):
        outbound.record(d, "out-%d@x" % i, "root-%d@x" % i)
    outbound.record(d, "out-5@x", "root-5@x")  # re-record must not grow the store
    data = json.loads((Path(d) / outbound.ALIAS_FILE).read_text(encoding="utf-8"))
    assert len(data["aliases"]) == outbound.ALIAS_LIMIT
    assert len(data["order"]) == outbound.ALIAS_LIMIT
    # Newest survive, oldest are gone; re-recording moves an entry to the newest slot.
    assert "out-0@x" not in data["aliases"]
    assert "out-%d@x" % (outbound.ALIAS_LIMIT + 19) in data["aliases"]
    assert data["order"][-1] == "out-5@x"

def test_no_directory_never_writes_anything(tmp_path):
    assert outbound.record("", "a@x", "b@x") is False
    assert outbound.lookup("", "a@x") == ""


def test_corrupt_store_degrades_to_no_alias(tmp_path):
    d = outbound.binding_dir(_binding(tmp_path))
    (Path(d) / outbound.ALIAS_FILE).write_text("{not json", encoding="utf-8")
    assert outbound.lookup(d, "out-1@x") == ""
    assert outbound.record(d, "out-1@x", "root@x") is True  # recovers by rewriting


# ── thread resolution ───────────────────────────────────────────────────────────────

def test_reply_to_our_reply_lands_on_the_original_thread(tmp_path):
    cfg = _binding(tmp_path)
    d = outbound.binding_dir(cfg)
    # External mail A opened the thread; we answered with out-1.
    payload, headers = _reply(["<a@ext.test>"], message_id="a@ext.test")
    assert outbound.resolve_root(payload, headers, d) == "a@ext.test"
    outbound.record(d, "out-1@aimail.test", outbound.resolve_root(payload, headers, d))

    # The external party now replies to OUR mail: references[0] is our own id.
    payload, headers = _reply(["<out-1@aimail.test>", "<a@ext.test>"], message_id="b@ext.test")
    assert thread.thread_root(payload, headers) == "out-1@aimail.test"  # the naive answer
    assert outbound.resolve_root(payload, headers, d) == "a@ext.test"  # the correct one
    # And the session identity follows the root, so it is the SAME session.
    assert (thread.chat_id_for(outbound.resolve_root(payload, headers, d))
            == thread.chat_id_for("a@ext.test"))


def test_a_second_round_trip_still_resolves(tmp_path):
    cfg = _binding(tmp_path)
    d = outbound.binding_dir(cfg)
    outbound.record(d, "out-1@aimail.test", "a@ext.test")
    # We answer again (out-2) inside the same thread; the alias points at the root, not
    # at the previous outbound id, so the chase is always one hop.
    outbound.record(d, "out-2@aimail.test", "a@ext.test")
    payload, headers = _reply(["<out-2@aimail.test>"], message_id="c@ext.test")
    assert outbound.resolve_root(payload, headers, d) == "a@ext.test"


def test_chained_aliases_are_chased_and_cycles_stop(tmp_path):
    cfg = _binding(tmp_path)
    d = outbound.binding_dir(cfg)
    outbound.record(d, "out-1@x", "out-2@x")  # a hand-written chain (not produced by record())
    outbound.record(d, "out-2@x", "root@x")
    payload, headers = _reply(["<out-1@x>"], message_id="z@x")
    assert outbound.resolve_root(payload, headers, d) == "root@x"
    # Cycle: degrades to the last value instead of looping forever.
    outbound.record(d, "cyc-a@x", "cyc-b@x")
    outbound.record(d, "cyc-b@x", "cyc-a@x")
    payload, headers = _reply(["<cyc-a@x>"], message_id="q@x")
    assert outbound.resolve_root(payload, headers, d) in ("cyc-a@x", "cyc-b@x")


def test_new_mail_is_a_new_thread(tmp_path):
    cfg = _binding(tmp_path)
    d = outbound.binding_dir(cfg)
    outbound.record(d, "out-1@aimail.test", "a@ext.test")
    payload = {"message_id": "fresh@ext.test"}
    assert outbound.resolve_root(payload, {}, d) == "fresh@ext.test"
    assert outbound.resolve_root(payload, {}, d) != "a@ext.test"


def test_unknown_reference_is_a_new_thread_not_a_guess(tmp_path):
    cfg = _binding(tmp_path)
    d = outbound.binding_dir(cfg)
    outbound.record(d, "out-1@aimail.test", "a@ext.test")
    payload, headers = _reply(["<never-seen@elsewhere.test>"], message_id="u@ext.test")
    assert outbound.resolve_root(payload, headers, d) == "never-seen@elsewhere.test"


def test_aliases_never_cross_bindings(tmp_path):
    a = outbound.binding_dir(_binding(tmp_path, "a@test.local"))
    b = outbound.binding_dir(_binding(tmp_path, "b@test.local"))
    outbound.record(a, "out-1@aimail.test", "thread-a@ext.test")
    payload, headers = _reply(["<out-1@aimail.test>"], message_id="r@ext.test")
    assert outbound.resolve_root(payload, headers, a) == "thread-a@ext.test"
    # Same Message-ID, different mailbox: no session guessing across profiles/ASIDs.
    assert outbound.resolve_root(payload, headers, b) == "out-1@aimail.test"
    assert outbound.resolve_root(payload, headers, "") == "out-1@aimail.test"


def test_no_thread_information_stays_empty(tmp_path):
    d = outbound.binding_dir(_binding(tmp_path))
    assert outbound.resolve_root({}, {}, d) == ""
    assert thread.chat_id_for("") == "aimail:" + "0" * 12


# ── outbound headers ────────────────────────────────────────────────────────────────

def test_threading_headers_name_the_parent_and_the_root():
    got = outbound.threading("root@ext.test", "parent@ext.test")
    assert got["In-Reply-To"] == "<parent@ext.test>"
    assert got["References"] == "<root@ext.test> <parent@ext.test>"


def test_threading_without_a_parent_anchors_on_the_root():
    got = outbound.threading("root@ext.test")
    assert got["In-Reply-To"] == "<root@ext.test>"
    assert got["References"] == "<root@ext.test>"
    assert outbound.threading("", "") == {}


def test_self_check_reports_only_shape(tmp_path):
    d = outbound.binding_dir(_binding(tmp_path))
    outbound.record(d, "out-1@x", "root@x")
    info = outbound.self_check(d)
    assert info["size"] == 1 and info["writable"] is True
    assert "out-1@x" not in json.dumps(info)  # no ids leak into diagnostics


# ── tool-side recording (the send path) ─────────────────────────────────────────────

def test_send_path_records_the_alias_from_the_reference_chain(tmp_path):
    """`send_mail` records ``generated_mid → root`` using the same root order."""
    tools = pytest.importorskip("aimail_tools")
    cfg = _binding(tmp_path)
    d = outbound.binding_dir(cfg)
    tools._record_outbound_alias(cfg, "<out-9@aimail.test>",
                                 references="<root@ext.test> <parent@ext.test>",
                                 in_reply_to="parent@ext.test", parent_mid="parent@ext.test")
    assert outbound.lookup(d, "out-9@aimail.test") == "root@ext.test"


def test_send_path_falls_back_to_the_parent_then_its_own_id(tmp_path):
    tools = pytest.importorskip("aimail_tools")
    cfg = _binding(tmp_path)
    d = outbound.binding_dir(cfg)
    tools._record_outbound_alias(cfg, "<out-a@aimail.test>", references=None,
                                 in_reply_to="parent@ext.test")
    assert outbound.lookup(d, "out-a@aimail.test") == "parent@ext.test"
    tools._record_outbound_alias(cfg, "<out-b@aimail.test>")  # a brand-new conversation
    assert outbound.lookup(d, "out-b@aimail.test") == "out-b@aimail.test"


def test_send_path_never_raises_without_a_binding():
    tools = pytest.importorskip("aimail_tools")
    tools._record_outbound_alias({}, "<out-x@aimail.test>", references="<r@x>")  # no store, no crash

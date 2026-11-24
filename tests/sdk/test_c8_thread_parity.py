# -*- coding: utf-8 -*-
"""Contract C8 — thread identity parity, driven by the shared vector file.

C8 is a PRODUCT CONTRACT requirement (docs/aimail-host-sse-contract-v2.md:519:
"同 thread followup，新 thread 新 session，空间=cwd"). Both SDKs implement it
(tssdk runtime/session-router.ts, pysdk v2/thread.py) and both only had their own private
unit expectations — a change on one side could silently break parity.

This test reads contract/c8-thread-vectors.json, the single source of these cases, so the two
implementations are pinned to the SAME vectors. The tssdk suite should consume the same file
(it currently keeps a private copy in test/core/runtime-router.test.ts).

Scope note: this pins the DECISION LOGIC only. The host-side half of C8 — a real agent that
replies inside the same host session when an external reply references its own outbound
Message-ID — still has no fixture (no L2/L3 stage sends In-Reply-To/references). That is the
`s6` gap recorded in the release review, for both SDKs.
"""
import json
from pathlib import Path

import pytest

from v2 import thread

REPO = Path(__file__).resolve().parents[2]
VECTORS = json.loads((REPO / "contract/c8-thread-vectors.json").read_text())


@pytest.mark.parametrize("case", VECTORS["thread_root"], ids=[c["why"][:40] for c in VECTORS["thread_root"]])
def test_thread_root_matches_contract_vectors(case):
    got = thread.thread_root(case["payload"], case["headers"])
    assert got == case["expect"], f"{case['why']}: got {got!r}"


@pytest.mark.parametrize("case", VECTORS["route"], ids=[c["why"][:40] for c in VECTORS["route"]])
def test_route_matches_contract_vectors(case):
    got = thread.route(case["meta"], case["workspace_path"])
    assert got["kind"] == case["expect"]["kind"], case["why"]
    assert got.get("session_id", "") == case["expect"]["session_id"], case["why"]


def test_session_keys_are_deterministic_and_scoped():
    """Same thread must map to the same session key across processes; two mailboxes must not collide."""
    assert thread.chat_id_for("root@x") == thread.chat_id_for("root@x")
    assert thread.chat_id_for("root@x") != thread.chat_id_for("other@x")
    a = thread.thread_id_for("sys-1", "a@l3.local", "root@x")
    b = thread.thread_id_for("sys-1", "A@l3.local", "root@x")      # address is case-insensitive
    c = thread.thread_id_for("sys-2", "a@l3.local", "root@x")      # different mailbox -> different id
    assert a == b and a != c

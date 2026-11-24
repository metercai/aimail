# -*- coding: utf-8 -*-
"""V2 transport: aggregate client wiring, skip/ack semantics, reconcile entry.

Guards the three defects that cost the TS SDK a QA round:
  * B2 — an aggregate call built the per-address client (no aggregate paths/header);
  * B1 — V2 bindings were not recognised as agent-scope, so nothing armed;
  * a paused/unknown target looked like a delivery error and was retried forever.
"""
import json

import pytest

import aimail_base as base
import aimail_tools as tools
from v2 import binding as binding_mod
from v2 import pull_client as pull_client_mod
from v2 import system_pull as system_pull_mod

ASID = "asid_0123456789abcdef0123456789abcdef"


def _cfg(**over):
    cfg = {"email": "a@test.local", "gateway_url": "http://gw", "system_id": "system-1",
           "api_key": "sk-x", "agent_system_id": ASID}
    cfg.update(over)
    return cfg


# ── aggregate client (B2 regression) ────────────────────────────────────────────

def test_aggregate_client_carries_paths_and_header():
    client = pull_client_mod.build_agent_system_client("http://gw", "sk-x", ASID, identity="a@test.local")
    assert pull_client_mod.is_aggregate_client(client) is True
    assert client.stream_path == "/api/v1/agent-system/pending/stream"
    assert client.pending_path == "/api/v1/agent-system/pending"
    assert client.ack_path == "/api/v1/agent-system/pending/ack"
    headers = client._signed_headers("GET", client.stream_path, b"")
    assert headers.get("X-Agent-System-Id") == ASID
    assert headers.get("X-Api-Identity") == "a@test.local"


def test_legacy_client_is_unchanged():
    """A plain client keeps the V1 paths — the override must be opt-in, not a default."""
    client = tools._GatewayClient("http://gw", "sk-x", identity="a@test.local")
    assert pull_client_mod.is_aggregate_client(client) is False
    assert client.stream_path == tools.PULL_STREAM_PATH
    assert "X-Agent-System-Id" not in client._signed_headers("GET", "/x", b"")


def test_aggregate_client_requires_asid():
    with pytest.raises(ValueError):
        pull_client_mod.build_agent_system_client("http://gw", "sk-x", "")


# ── skip / accepted / error semantics in the shared delivery loop ───────────────

class _Recorder:
    """Minimal stand-in for the pull client's transport surface."""

    def __init__(self):
        self.acked = []

    def pull_ack(self, ids):
        self.acked.append(list(ids))
        return {"success": True, "acked": len(ids)}


def _batches(*deliveries):
    return [{"body": {"subject": "hi"}, "deliveries": [
        {"id": d, "email": "a@test.local", "headers": {"message_id": "<m%d@x>" % d}}
        for d in deliveries]}]


def test_delivery_acks_accepted_only_and_leaves_skips_pending():
    rec = _Recorder()
    stats = {"pulled": 0, "acked": 0, "errors": 0}
    seen = set()

    def on_email(mail):
        if mail["id"] == 2:
            return binding_mod.skip_result(binding_mod.SKIP_PAUSED)
        if mail["id"] == 3:
            raise RuntimeError("host rejected")
        return None

    tools._GatewayClient._deliver_batches(rec, _batches(1, 2, 3), on_email, seen, stats)
    assert rec.acked == [[1]]              # only the accepted delivery
    assert stats["pulled"] == 1 and stats["errors"] == 1 and stats.get("skipped") == 1
    assert 2 not in seen and 3 not in seen  # both re-evaluated next round


def test_delivery_treats_explicit_skip_dict_as_typed_acceptance_of_silence():
    rec = _Recorder()
    stats = {"pulled": 0, "acked": 0, "errors": 0}
    seen = set()
    tools._GatewayClient._deliver_batches(
        rec, _batches(7), lambda mail: {"status": "skip", "reason": "unknown"}, seen, stats)
    assert rec.acked == []
    assert stats.get("skipped") == 1 and stats["errors"] == 0


# ── reconcile entry (arm / no-arm / all-paused) ────────────────────────────────

def _seed(tmp_path, monkeypatch, cfgs):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    for cfg in cfgs:
        base.save_agent_config(str(cfg["email"]).split("@")[0], cfg, cfg["system_id"])


def test_reconcile_arms_one_stream_per_gateway(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        _cfg(email="a@test.local", gateway_url="http://gw1"),
        _cfg(email="b@test.local", gateway_url="http://gw1"),
        _cfg(email="c@test.local", gateway_url="http://gw2"),
    ])
    lines = []
    started = []

    class _FakeClient:
        def start_polling(self, on_email, interval=30.0, limit=20, stop_event=None, mode=None):
            return {"pulled": 0, "acked": 0, "errors": 0}

    def _start(**kw):
        started.append((kw["agent_system_id"], kw["gateway_url"]))
        handle = system_pull_mod.SystemPullHandle(kw["agent_system_id"], kw["gateway_url"], {"enabled": True})
        handle.started = True
        return handle

    monkeypatch.setattr(system_pull_mod, "start_system_pull", _start)
    handles = system_pull_mod.start_reconcile(
        agent_system_id=ASID, on_email=lambda cfg, mail: None,
        client_for=lambda cfg: _FakeClient(), log=lines.append)
    assert len(handles) == 2
    assert started == [(ASID, "http://gw1"), (ASID, "http://gw2")]  # one per Gateway, not per binding
    assert any("SSE reconcile: 2 gateway stream(s) from 3 binding(s)" in l for l in lines)


def test_reconcile_reports_no_binding_without_arming(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    lines = []
    handles = system_pull_mod.start_reconcile(
        agent_system_id=ASID, on_email=lambda cfg, mail: None, log=lines.append)
    assert handles == []
    assert any("SSE reconcile: 0 gateway stream(s) from 0 binding(s)" in l for l in lines)
    assert any("no agent-scope binding" in l for l in lines)


def test_reconcile_skips_all_paused_group(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        _cfg(email="a@test.local", gateway_url="http://gw1", paused=True),
    ])
    lines = []
    handles = system_pull_mod.start_reconcile(
        agent_system_id=ASID, on_email=lambda cfg, mail: None, log=lines.append)
    assert handles == []
    assert any("all members paused" in l for l in lines)


# ── system pull: fresh lookup + skip wiring ────────────────────────────────────

def test_start_system_pull_delivers_via_fresh_lookup(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [_cfg(email="a@test.local")])
    seen = {}

    class _FakeClient:
        def __init__(self):
            self.calls = 0

        def start_polling(self, on_email, interval=30.0, limit=20, stop_event=None, mode=None):
            self.calls += 1
            status = on_email({"id": 11, "email": "a@test.local", "headers": {}, "body": {}})
            seen["result"] = status
            return {"pulled": 1, "acked": 1, "errors": 0}

    handle = system_pull_mod.start_system_pull(
        agent_system_id=ASID, gateway_url="http://gw", api_key="sk-x",
        representative_email="a@test.local", members=[_cfg(email="a@test.local")],
        on_email=lambda cfg, mail: binding_mod.accepted_result(),
        client=_FakeClient(), log=lambda l: None)
    handle.join(5)
    assert seen["result"]["status"] == "accepted"
    assert handle.stats()["pulled"] == 1


def test_start_system_pull_requires_ids():
    with pytest.raises(ValueError):
        system_pull_mod.start_system_pull(
            agent_system_id="", gateway_url="http://gw", api_key="k",
            representative_email="a@test.local", members=[], on_email=lambda c, m: None)

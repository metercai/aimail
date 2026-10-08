"""agent-scope pull entry (W2b) — pure-local L1 baseline.

Covers the address-code activation pull path end to end **without network or
containers**:

1. enablement decision — a ``shared_addr_*`` binding enables polling, a plain
   system id / keyless binding / missing binding does NOT, ``AIMAIL_PULL=0``
   switches it off, and an explicit override beats the env;
2. interval & batch size — defaults + env/override precedence + clamping
   (floor 1s, ceiling 24h; limit 1..200);
3. processing failure ⇒ **no ack** and the SAME delivery id is re-pulled on
   the next round (the gap W2b closed: the id used to stay in the dedup set
   ⇒ the mail was never re-delivered locally although the gateway kept it
   pending);
4. stoppable — ``stop_event`` aborts the sleep at once (not after a whole
   interval) and a pre-set event stops the loop before round 1;
5. the shared entry point wires one loop per binding to the adapter callback
   and skips bindings it cannot deliver to (non-agent scope / no url /
   no secret).

Contract literals (binding / pointer filenames) are imported from
``aimail_contract`` — never spelled out here (the literal ratchet gate).
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, "pysdk")  # ensure repo pysdk wins over any cli/ shadow

import aimail_base as base  # noqa: E402
import aimail_contract as contract  # noqa: E402
from aimail_contract import BINDING_FILE  # noqa: E402
from aimail_tools import _GatewayClient, _poll_sleep  # noqa: E402

AGENT_SCOPE_SID = "shared_addr_abc123"
PLAIN_SID = "shared-token-40b34a66"
PULL_ENVS = ("AIMAIL_PULL", "AIMAIL_PULL_INTERVAL_MS", "AIMAIL_PULL_LIMIT")


def _agent_scope_cfg(**over) -> dict:
    cfg = {"system_id": AGENT_SCOPE_SID, "api_key": "raw-agent-key",
           "email": "agent.demo@example.test",
           "gateway_url": "http://127.0.0.1:1"}
    cfg.update(over)
    return cfg


PLAIN_CFG = {"system_id": PLAIN_SID, "api_key": "k", "email": "a@example.test"}


@pytest.fixture
def clean_env(monkeypatch):
    """No ambient pull config leaking into the decision under test."""
    for name in PULL_ENVS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _write_binding(home: Path, sid: str, addr: str, cfg: dict) -> Path:
    """Real on-disk binding (filename comes from the contract constant)."""
    d = home / "systems" / sid / addr
    d.mkdir(parents=True, exist_ok=True)
    p = d / BINDING_FILE
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


class ScriptedTransport:
    """``pull_list`` / ``pull_ack`` double — no network (gateway wire shape)."""

    def __init__(self, rounds=None):
        self.rounds = list(rounds or [])
        self.index = 0
        self.ack_calls = []
        self.pull_calls = 0

    def pull_list(self, limit=20):
        self.pull_calls += 1
        if self.index >= len(self.rounds):
            return {"success": True, "batches": []}
        batches = self.rounds[self.index]
        self.index += 1
        return {"success": True, "batches": list(batches)}

    def pull_ack(self, ids):
        self.ack_calls.append(list(ids))
        return {"success": True, "acked": len(ids)}


def _client_with(rounds):
    c = _GatewayClient("http://127.0.0.1:1", "")
    t = ScriptedTransport(rounds)
    c.pull_list = t.pull_list      # type: ignore[method-assign]
    c.pull_ack = t.pull_ack        # type: ignore[method-assign]
    return c, t


class FakePullClient:
    """Signature-compatible client handed to the entry point via ``client_for``."""

    def __init__(self, rounds=None):
        self.real, self.transport = _client_with(rounds)
        self.interval = None
        self.limit = None

    def start_polling(self, on_email, interval=30.0, limit=20,
                      max_rounds=None, stop_event=None):
        self.interval, self.limit = interval, limit
        return self.real.start_polling(on_email, interval=interval, limit=limit,
                                       stop_event=stop_event)


def _wait_for(pred, timeout=4.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


# ── 1. enablement decision ─────────────────────────────────────────────────

def test_enablement_agent_scope_binding_is_enabled(clean_env):
    cfg = _agent_scope_cfg()
    assert base.is_agent_scope_binding(cfg) is True
    s = base.resolve_agent_pull_settings(cfg, env={})
    assert s["enabled"] is True and s["reason"] == "enabled"


@pytest.mark.parametrize("sid", [PLAIN_SID, "hosts415349", "system-1a2b3c4d"])
def test_enablement_plain_system_id_not_enabled(clean_env, sid):
    """Platform/system/bridge bindings have a push path — never polled."""
    cfg = _agent_scope_cfg(system_id=sid)
    assert base.is_agent_scope_binding(cfg) is False
    s = base.resolve_agent_pull_settings(cfg, env={})
    assert s["enabled"] is False and s["reason"] == "not-agent-scope"


@pytest.mark.parametrize("cfg", [None, {}, ""])
def test_enablement_no_binding_not_enabled(clean_env, cfg):
    assert base.resolve_agent_pull_settings(cfg, env={}) == {
        "enabled": False, "reason": "no-binding",
        "interval_ms": base.DEFAULT_PULL_INTERVAL_MS,
        "interval": base.DEFAULT_PULL_INTERVAL_MS / 1000.0,
        "limit": base.DEFAULT_PULL_LIMIT}


def test_enablement_agent_scope_without_key_not_enabled(clean_env):
    """The prefix alone is not enough — a binding needs the agent key too."""
    cfg = _agent_scope_cfg(api_key="")
    assert base.is_agent_scope_binding(cfg) is False
    assert base.resolve_agent_pull_settings(cfg, env={})["reason"] == "not-agent-scope"


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "off", "no", " 0 "])
def test_enablement_env_switch_off(clean_env, raw):
    s = base.resolve_agent_pull_settings(_agent_scope_cfg(),
                                         env={"AIMAIL_PULL": raw})
    assert s["enabled"] is False and s["reason"] == "disabled-by-config"


@pytest.mark.parametrize("raw", ["1", "true", "yes", "", "  "])
def test_enablement_env_default_and_truthy_values_stay_on(clean_env, raw):
    s = base.resolve_agent_pull_settings(_agent_scope_cfg(),
                                         env={"AIMAIL_PULL": raw})
    assert s["enabled"] is True and s["reason"] == "enabled"


def test_enablement_explicit_override_beats_env(clean_env):
    cfg = _agent_scope_cfg()
    # override on wins over env off
    s = base.resolve_agent_pull_settings(cfg, env={"AIMAIL_PULL": "0"},
                                         overrides={"enabled": True})
    assert s["enabled"] is True and s["reason"] == "enabled"
    # override off wins over absent env
    s = base.resolve_agent_pull_settings(cfg, env={}, overrides={"enabled": False})
    assert s["enabled"] is False and s["reason"] == "disabled-by-config"
    # …but never turns a non-agent-scope binding on (decision comes first)
    s = base.resolve_agent_pull_settings(PLAIN_CFG, env={}, overrides={"enabled": True})
    assert s["enabled"] is False and s["reason"] == "not-agent-scope"


def test_pull_env_names_match_the_cross_language_contract(clean_env):
    """Same variable names as the TS pull entry (docs/one vocabulary)."""
    assert base.PULL_ENABLE_ENV == "AIMAIL_PULL"
    assert base.PULL_INTERVAL_ENV == "AIMAIL_PULL_INTERVAL_MS"
    assert base.PULL_LIMIT_ENV == "AIMAIL_PULL_LIMIT"


# ── 2. interval / batch size: defaults, overrides, clamping ────────────────

def test_pull_defaults(clean_env):
    s = base.resolve_agent_pull_settings(_agent_scope_cfg(), env={})
    assert base.DEFAULT_PULL_INTERVAL_MS == 30_000
    assert s["interval_ms"] == 30_000 and s["interval"] == 30.0
    assert base.DEFAULT_PULL_LIMIT == 20 and s["limit"] == 20


def test_pull_env_overrides(clean_env):
    s = base.resolve_agent_pull_settings(
        _agent_scope_cfg(),
        env={"AIMAIL_PULL_INTERVAL_MS": "5000", "AIMAIL_PULL_LIMIT": "7"})
    assert s["interval_ms"] == 5000 and s["interval"] == 5.0 and s["limit"] == 7


def test_pull_explicit_override_beats_env(clean_env):
    s = base.resolve_agent_pull_settings(
        _agent_scope_cfg(),
        env={"AIMAIL_PULL_INTERVAL_MS": "5000", "AIMAIL_PULL_LIMIT": "7"},
        overrides={"interval_ms": 2000, "limit": 3})
    assert s["interval_ms"] == 2000 and s["limit"] == 3


def test_pull_interval_is_clamped(clean_env):
    assert base.MIN_PULL_INTERVAL_MS == 1_000
    assert base.MAX_PULL_INTERVAL_MS == 24 * 3600 * 1000
    for raw in (10, 999, "50"):
        assert base.resolve_agent_pull_settings(
            _agent_scope_cfg(), env={"AIMAIL_PULL_INTERVAL_MS": raw})["interval_ms"] == 1_000
    for raw in (10 ** 12, base.MAX_PULL_INTERVAL_MS + 1, "99999999999"):
        assert base.resolve_agent_pull_settings(
            _agent_scope_cfg(), env={"AIMAIL_PULL_INTERVAL_MS": raw})["interval_ms"] == 86_400_000
    # exact boundaries pass through unchanged
    assert base.resolve_agent_pull_settings(
        _agent_scope_cfg(), env={"AIMAIL_PULL_INTERVAL_MS": "86400000"})["interval_ms"] == 86_400_000


@pytest.mark.parametrize("raw", ["abc", "", 0, -5, None, True, float("nan")])
def test_pull_invalid_interval_falls_back_to_default(clean_env, raw):
    assert base.resolve_agent_pull_settings(
        _agent_scope_cfg(), env={"AIMAIL_PULL_INTERVAL_MS": raw})["interval_ms"] == 30_000


def test_pull_limit_is_clamped_and_invalid_falls_back(clean_env):
    for raw, want in ((999, 200), (200, 200), (1, 1), (0, 20), (-1, 20), ("abc", 20)):
        assert base.resolve_agent_pull_settings(
            _agent_scope_cfg(), env={"AIMAIL_PULL_LIMIT": raw})["limit"] == want


# ── 3. failure ⇒ no ack ⇒ same id re-pulled next round ─────────────────────

def test_failure_not_acked_and_same_id_repulled_next_round(clean_env):
    batch = {"body": {"subject": "hi"}, "deliveries": [{"id": 7, "email": "a@x"}]}
    # rounds 1..3 all carry the same pending delivery
    c, t = _client_with([[batch], [batch], [batch]])
    seen_ids, attempts = [], []

    def handler(mail):
        seen_ids.append(mail["id"])
        attempts.append(mail)
        if len(attempts) == 1:
            raise RuntimeError("handler down")   # first attempt fails

    stats = c.start_polling(handler, interval=0, max_rounds=3)

    assert seen_ids == [7, 7], "failed id must be re-delivered, succeeded one must not"
    assert t.ack_calls == [[7]], "ack only after the successful attempt"
    assert stats == {"pulled": 1, "acked": 1, "errors": 1}


def test_failure_never_acked_when_every_round_fails(clean_env):
    batch = {"body": {}, "deliveries": [{"id": 11, "email": "a@x"},
                                        {"id": 12, "email": "b@x"}]}
    c, t = _client_with([[batch], [batch]])
    got = []

    def boom(mail):
        got.append(mail["id"])
        raise RuntimeError("down")

    stats = c.start_polling(boom, interval=0, max_rounds=2)
    assert got == [11, 12, 11, 12]      # both re-pulled, in order
    assert t.ack_calls == []            # nothing acked
    assert stats["pulled"] == 0 and stats["errors"] == 4


# ── 4. stoppable ───────────────────────────────────────────────────────────

def test_stop_event_interrupts_the_sleep_immediately(clean_env):
    c, t = _client_with([])             # always an empty round
    stop = threading.Event()
    th = threading.Thread(
        target=lambda: c.start_polling(lambda m: None, interval=30.0,
                                       stop_event=stop),
        daemon=True)
    th.start()
    assert _wait_for(lambda: t.pull_calls >= 1), "loop must enter the first round"
    t0 = time.monotonic()
    stop.set()
    th.join(3.0)
    elapsed = time.monotonic() - t0
    assert not th.is_alive(), "stop_event must end the loop"
    assert elapsed < 2.0, "30s interval must be aborted at once, not waited out"


def test_stop_event_already_set_runs_zero_rounds(clean_env):
    c, t = _client_with([])
    stop = threading.Event()
    stop.set()
    stats = c.start_polling(lambda m: None, interval=0, stop_event=stop)
    assert t.pull_calls == 0 and stats == {"pulled": 0, "acked": 0, "errors": 0}


def test_poll_sleep_returns_early_when_stopped(clean_env):
    stop = threading.Event()
    threading.Timer(0.1, stop.set).start()
    t0 = time.monotonic()
    _poll_sleep(30.0, stop)
    assert time.monotonic() - t0 < 2.0
    # non-positive interval never waits, even without an event
    t0 = time.monotonic()
    _poll_sleep(0, None)
    assert time.monotonic() - t0 < 0.5


# ── 5. shared entry point wiring ───────────────────────────────────────────

def test_entries_no_binding_returns_empty_and_logs(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    lines = []
    handles = base.start_agent_pull_entries(
        system_id="", on_email=lambda c, m: {}, env={}, log=lines.append)
    assert handles == []
    assert any("no agent-scope binding" in ln for ln in lines), lines


def test_entries_plain_system_id_not_started(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    _write_binding(tmp_path, PLAIN_SID, "agent.demo", 
                   {"system_id": PLAIN_SID, "api_key": "k", "email": "a@example.test"})
    lines = []

    def _boom(_cfg):            # must never be reached for a push binding
        raise AssertionError("client built for a non-agent-scope binding")

    handles = base.start_agent_pull_entries(
        system_id="", on_email=lambda c, m: {}, env={}, log=lines.append,
        client_for=_boom)
    assert handles == []
    assert any("not-agent-scope" in ln for ln in lines), lines


def test_entries_agent_scope_without_target_not_started(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    cfg = _agent_scope_cfg(email="agent.demo@example.test")
    _write_binding(tmp_path, AGENT_SCOPE_SID, "agent.demo", cfg)
    lines = []
    handles = base.start_agent_pull_entries(
        system_id="", on_email=lambda c, m: {}, env={}, log=lines.append,
        target_for=lambda _cfg: {"url": "", "secret": "s", "ok": False,
                                 "reason": "no-url"},
        client_for=lambda _cfg: FakePullClient([]))
    assert handles == []
    assert any("no local inbound endpoint" in ln and "no-url" in ln for ln in lines), lines


def test_entries_disabled_by_env_starts_nothing(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    _write_binding(tmp_path, AGENT_SCOPE_SID, "agent.demo", _agent_scope_cfg())
    lines = []
    handles = base.start_agent_pull_entries(
        system_id="", on_email=lambda c, m: {}, env={"AIMAIL_PULL": "0"},
        log=lines.append,
        target_for=lambda _cfg: {"url": "http://127.0.0.1:1/x", "secret": "s",
                                 "ok": True, "reason": ""},
        client_for=lambda _cfg: FakePullClient([]))
    assert handles == []
    assert any("disabled-by-config" in ln for ln in lines), lines


def test_entries_client_build_failure_skips_binding(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    _write_binding(tmp_path, AGENT_SCOPE_SID, "agent.demo", _agent_scope_cfg())
    lines = []

    def _boom(_cfg):
        raise RuntimeError("no client")

    handles = base.start_agent_pull_entries(
        system_id="", on_email=lambda c, m: {}, env={}, log=lines.append,
        target_for=lambda _cfg: {"url": "http://127.0.0.1:1/x", "secret": "s",
                                 "ok": True, "reason": ""},
        client_for=_boom)
    assert handles == []            # never raises, binding skipped
    assert any("client build failed" in ln for ln in lines), lines


def test_entries_starts_loop_delivers_acks_and_stops(clean_env, tmp_path, monkeypatch):
    """Wiring proof: discovery → per-binding loop → callback → ack → stop()."""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    cfg = _agent_scope_cfg(email="agent.demo@example.test")
    _write_binding(tmp_path, AGENT_SCOPE_SID, "agent.demo", cfg)

    batch = {"body": {"subject": "pulled"}, "deliveries": [{"id": 42, "email": "agent.demo@example.test"}]}
    fake = FakePullClient([[batch]])
    handled = []
    lines = []
    handles = base.start_agent_pull_entries(
        system_id=AGENT_SCOPE_SID, env={}, log=lines.append,
        overrides={"interval_ms": "1"},          # clamped up to the 1s floor
        target_for=lambda _cfg: {"url": "http://127.0.0.1:1/inbound",
                                 "secret": "s", "ok": True, "reason": ""},
        client_for=lambda _cfg: fake,
        on_email=lambda c, mail: handled.append((c.get("email"), mail["id"])))
    try:
        assert len(handles) == 1
        h = handles[0]
        assert (h.email, h.system_id, h.reason) == ("agent.demo@example.test",
                                                    AGENT_SCOPE_SID, "enabled")
        assert h.interval_ms == 1_000 and h.limit == base.DEFAULT_PULL_LIMIT
        assert fake.interval == 1.0 and fake.limit == base.DEFAULT_PULL_LIMIT
        assert _wait_for(lambda: fake.transport.ack_calls), "loop must ack the delivery"
        assert handled == [("agent.demo@example.test", 42)]
        assert fake.transport.ack_calls == [[42]]
        assert any("polling enabled every 1000ms" in ln for ln in lines), lines
    finally:
        base.stop_agent_pull_entries(handles)
    assert h.join(3.0) is True and h.alive() is False
    assert h.stats()["acked"] == 1


def test_stop_agent_pull_entries_is_idempotent(clean_env):
    base.stop_agent_pull_entries([])
    base.stop_agent_pull_entries(None)


def test_list_agent_scope_bindings_filters_the_filesystem(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    _write_binding(tmp_path, AGENT_SCOPE_SID, "agent.demo", _agent_scope_cfg())
    _write_binding(tmp_path, PLAIN_SID, "sys.demo",
                   {"system_id": PLAIN_SID, "api_key": "k", "email": "b@example.test"})
    all_ = base.list_agent_scope_bindings()
    assert [c["email"] for c in all_] == ["agent.demo@example.test"]
    assert [c["email"] for c in base.list_agent_scope_bindings(AGENT_SCOPE_SID)] == \
        ["agent.demo@example.test"]
    assert base.list_agent_scope_bindings(PLAIN_SID) == []


# ── 6. local secret self-provision (defect #7 fix A) ──────────────────────
#
# A pure address-level binding (address-code activation, no system install) had
# no `webhook_secret`; a replay needs url+secret as a PAIR, and only the url had
# a fallback ⇒ every such binding was skipped with reason `no-secret` and the
# pull loop never started. These tests pin the fix: the secret is generated
# locally (same mechanism/format as the install chain), landed in the existing
# binding format/0600, reused when already present, and the old `no-secret`
# skip is gone for provisionable bindings.

def _mode(p: Path) -> int:
    import stat
    return stat.S_IMODE(p.stat().st_mode)


DEFAULT_INBOUND_URL = contract.inbound_url(contract.INBOUND_PORTS["deerflow"])


def test_provision_lands_secret_in_the_shared_binding_format(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    p = _write_binding(tmp_path, AGENT_SCOPE_SID, "agent.demo",
                       {"agent_id": "agent.demo", "email": "agent.demo@example.test",
                        "gateway_url": "http://127.0.0.1:1", "domain": "example.test",
                        "system_id": AGENT_SCOPE_SID, "api_key": "raw-agent-key"})
    cfg = base.iter_agentmail_configs(AGENT_SCOPE_SID)[0]

    out = base.ensure_binding_webhook_secret(cfg)

    assert out["changed"] is True and out["reason"] == "provisioned", out
    assert out["path"] == str(p)
    assert len(out["secret"]) == base.WEBHOOK_SECRET_HEX_BYTES * 2
    assert _mode(p) == 0o600, oct(_mode(p))
    on_disk = json.loads(p.read_text())
    assert on_disk["webhook_secret"] == out["secret"]
    # field order/format follow the existing binding format (parity with the
    # register chain + TS saveBinding): secret right after api_key, indent=2,
    # trailing newline, internal `_`-prefixed fields never landed.
    keys = list(on_disk)
    assert keys.index("webhook_secret") == keys.index("api_key") + 1, keys
    assert all(not k.startswith("_") for k in keys), keys
    assert p.read_text() == json.dumps(on_disk, indent=2, ensure_ascii=False) + "\n"


def test_provision_is_idempotent_and_never_overwrites_an_existing_secret(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    existing = "e" * (base.WEBHOOK_SECRET_HEX_BYTES * 2)
    p = _write_binding(tmp_path, AGENT_SCOPE_SID, "agent.demo",
                       _agent_scope_cfg(webhook_secret=existing))
    before = p.read_bytes()
    cfg = base.iter_agentmail_configs(AGENT_SCOPE_SID)[0]

    out = base.ensure_binding_webhook_secret(cfg)

    assert out == {"changed": False, "secret": existing, "path": str(p),
                   "reason": "exists", "detail": ""}, out
    assert p.read_bytes() == before          # byte-identical: nothing rewritten
    # a second call (already provisioned) stays a no-op too
    assert base.ensure_binding_webhook_secret(cfg)["changed"] is False


def test_provision_without_a_landing_path_does_not_pretend_success():
    """In-memory cfg (no `_config_path`) ⇒ reason `no-path`, no fake secret."""
    cfg = _agent_scope_cfg()
    out = base.ensure_binding_webhook_secret(cfg)
    assert out["changed"] is False and out["reason"] == "no-path", out
    assert out["secret"] == "" and "webhook_secret" not in cfg, out


def test_entries_provisions_before_starting_and_the_no_secret_skip_is_gone(
        tmp_path, monkeypatch):
    """Legacy binding (landed before this fix, no secret) now really polls.

    Uses the REAL resolver (deer-flow shape: url falls back to the contract
    port+path, secret has no fallback) — so a green here means the loop started
    because the secret was self-provisioned, not because a stub said `ok`.
    """
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    p = _write_binding(tmp_path, AGENT_SCOPE_SID, "agent.demo", _agent_scope_cfg())
    assert "webhook_secret" not in json.loads(p.read_text())      # the legacy shape

    fake = FakePullClient([[{"body": {"subject": "pulled"},
                             "deliveries": [{"id": 7,
                                             "email": "agent.demo@example.test"}]}]])
    lines, handled = [], []
    handles = base.start_agent_pull_entries(
        system_id=AGENT_SCOPE_SID, env={}, log=lines.append,
        overrides={"interval_ms": "1"},
        target_for=lambda c: base.resolve_inbound_replay_target(
            c, default_url=DEFAULT_INBOUND_URL),
        client_for=lambda _c: fake,
        on_email=lambda c, mail: handled.append(mail["id"]))
    try:
        assert len(handles) == 1, lines        # used to be 0 (no-secret skip)
        assert any("provisioned one into" in ln for ln in lines), lines
        assert not any("no local inbound endpoint (no-secret)" in ln
                       for ln in lines), lines
        assert _wait_for(lambda: fake.transport.ack_calls), "loop must poll+ack"
        assert handled == [7]
        assert fake.transport.ack_calls == [[7]]
        landed = json.loads(p.read_text())
        assert len(landed["webhook_secret"]) == base.WEBHOOK_SECRET_HEX_BYTES * 2
        assert _mode(p) == 0o600, oct(_mode(p))
    finally:
        base.stop_agent_pull_entries(handles)


def test_entries_leaves_a_binding_that_already_has_a_secret_byte_identical(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    p = _write_binding(tmp_path, AGENT_SCOPE_SID, "agent.demo",
                       _agent_scope_cfg(webhook_secret="s" * 64))
    before = p.read_bytes()
    lines = []
    handles = base.start_agent_pull_entries(
        system_id=AGENT_SCOPE_SID, env={}, log=lines.append,
        overrides={"interval_ms": "1"},
        target_for=lambda c: base.resolve_inbound_replay_target(
            c, default_url=DEFAULT_INBOUND_URL),
        client_for=lambda _c: FakePullClient([]),
        on_email=lambda c, mail: None)
    try:
        assert len(handles) == 1, lines
        assert not any("provisioned one into" in ln for ln in lines), lines
        assert p.read_bytes() == before
    finally:
        base.stop_agent_pull_entries(handles)


def test_replay_target_prefers_the_live_route_secret_when_the_host_verifies_out_of_process():
    """Why the switch exists: hermes verifies inbound in the HOST webhook
    platform (route table is the live verifier), deer-flow reads the binding."""
    binding_secret, route_secret = "b" * 64, "r" * 64
    cfg = _agent_scope_cfg(webhook_secret=binding_secret)

    # default (deer-flow shape): the binding is the truth
    d = base.resolve_inbound_replay_target(cfg, default_url=DEFAULT_INBOUND_URL,
                                           route_secret=route_secret)
    assert d["ok"] is True and d["secret"] == binding_secret
    # hermes shape: the live route secret wins
    h = base.resolve_inbound_replay_target(cfg, default_url=DEFAULT_INBOUND_URL,
                                           route_secret=route_secret,
                                           prefer_route_secret=True)
    assert h["ok"] is True and h["secret"] == route_secret
    # ...but with no route secret it still falls back to the binding's
    h2 = base.resolve_inbound_replay_target(cfg, default_url=DEFAULT_INBOUND_URL,
                                            route_secret="",
                                            prefer_route_secret=True)
    assert h2["secret"] == binding_secret


def test_hermes_adapter_wires_the_route_secret_precedence():
    """Source-level proof of the wire (importing the adapter would leak its
    profile-dir resolver into the whole pytest process — see section 6)."""
    src = (Path(__file__).resolve().parents[1] / "pysdk" / "hermes"
           / "aimail_hermes.py").read_text(encoding="utf-8")
    block = src.split("def _pull_replay_target(", 1)[1].split("\ndef ", 1)[0]
    assert "prefer_route_secret=True" in block, block


# ── 7. adapter seam: import-safe, never raises, idempotent stop ────────────

def test_hermes_adapter_seam_is_inert_without_bindings(tmp_path):
    """`ensure_agent_pull_started()` is the host wiring point: with no
    agent-scope binding it returns [] and **never raises** (a wiring mistake
    must not kill gateway startup); stop is idempotent.

    Runs in a SUBPROCESS on purpose: importing the hermes adapter installs a
    process-wide profile-dir resolver
    (`aimail_hermes.py:833  core._PROFILE_DIR_RESOLVER = _resolve_profile_dir`),
    which makes `aimail_base.resolve_system_id_for_email()` fall back to the
    *host's* real profile pointer — that leaks this machine's sid into every
    later hermetic test (observed: `test_local_mail_search` then writes to
    `systems/<host-sid>/…` instead of `systems/_unassigned/…` and fails).
    Same reason the rest of the suite keeps the adapter out of the pytest
    process. The subprocess gets a scratch HOME + AIMAIL_HOME.
    """
    repo = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("AIMAIL_PULL")}
    env["HOME"] = str(tmp_path)
    env["AIMAIL_HOME"] = str(tmp_path / "aimail-home")
    code = (
        "import sys;"
        "sys.path[:0] = [%r, %r];"
        "import aimail_hermes as h;"
        "print('HANDLES', h.ensure_agent_pull_started(env={}));"
        "h.stop_agent_pull();"
        "h.stop_agent_pull();"
        "print('AFTER', h._PULL_HANDLES)"
        % (str(repo / "pysdk"), str(repo / "pysdk" / "hermes"))
    )
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "HANDLES []" in r.stdout and "AFTER []" in r.stdout, r.stdout

"""P1'/P2'/N1 wiring + behaviour (owner rulings 2026-10-02).

Covers the rulings as executable assertions:
  1. SDK `rename_address` = the ONLY rename path: validation + derivation
     (`email_for_agent` single naming source) + conflict precheck + cloud rename +
     whitelist orphan cleanup keyed by the FULL old address (shared-domain
     correctness) + local migration + pointer sync + **rename ends by emitting the
     inbound-live signal** (SDK triggers, CLI reconciles).
  2. CLI reconcile (`_reconcile_inbound_routes` / `_withdraw_inbound_routes` /
     `_cmd_inbound_route`): upsert desired bindings, withdraw ONLY this system's
     rows (never a foreign row), `down` = service-scope withdrawal + backlog line
     (D8); the signaled address is the anchor, not the scope.
  3. Signal locator: payload carries only the address (no `-s`, zero-bridge
     contract) ⇒ CLI resolves the owning system (`_sid_for_address`) and matches
     `-a` by full email.
  4. Wiring: set-name / set-manager / register-then-rename call the SDK entries —
     CLI performs no cloud CRUD and no bridge writes of its own anymore.
"""
import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "cli" / "aimail"
_CLI_DIR = _REPO / "cli"
if str(_CLI_DIR) not in sys.path:
    sys.path.insert(0, str(_CLI_DIR))
if str(_REPO / "pysdk") not in sys.path:
    sys.path.insert(0, str(_REPO / "pysdk"))

import aimail_base  # noqa: E402
import aimail_tools  # noqa: E402
import bridge_wire  # noqa: E402


def _load(path: Path, name: str):
    loader = SourceFileLoader(name, str(path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════════════
# 1. SDK rename_address
# ══════════════════════════════════════════════════════════════════════════

class _FakeClient:
    instances = []

    def __init__(self, gw, ak):
        self.gw, self.ak = gw, ak
        self.calls = []
        self.whitelist_queries = []
        self.deleted = []
        self.fail_rename = False
        _FakeClient.instances.append(self)

    def _request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.fail_rename and "addresses/rename" in str(path):
            raise RuntimeError("server says no")
        return {"ok": True}

    def list_whitelists_by_domain(self, domain):
        self.whitelist_queries.append(domain)
        # 网关语义: 该键(完整地址)解析到其所属系统 ⇒ 返回含该地址自身的行
        return [{"id": 7, "domain_addr": domain}]

    def delete_whitelist_entry_by_id(self, entry_id):
        self.deleted.append(entry_id)


@pytest.fixture
def sdk_env(tmp_path, monkeypatch):
    """AIMAIL_HOME + one shared-system binding + platform pointer."""
    home = tmp_path / "home"
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    sysdir = home / "systems" / "sid-t1"
    d = sysdir / "d1"
    d.mkdir(parents=True)
    old = "main.t1@shared.tm"
    (d / "agentmail.json").write_text(json.dumps(
        {"email": old, "system_id": "sid-t1", "agent_id": "main",
         "webhook_url": "http://127.0.0.1:18080/hook"}))
    phome = tmp_path / "phome"
    phome.mkdir()
    (phome / ".aimail").write_text(json.dumps(
        {"system_id": "sid-t1", "email": old}))
    cfg = {"domain": "shared.tm", "system_name": "t1",
           "gateway_url": "https://gw.test", "admin_key": "AK",
           "system_home": str(phome)}
    _FakeClient.instances.clear()
    monkeypatch.setattr(aimail_tools, "_GatewayClient", _FakeClient)
    signals = []

    def _fake_notify(email, state, **kw):
        signals.append((email, state))
        return {"state": "notified", "email": email}

    monkeypatch.setattr(aimail_base, "notify_inbound_state", _fake_notify)
    return SimpleNamespace(home=home, sysdir=sysdir, old=old, cfg=cfg,
                           signals=signals)


def test_rename_rejects_invalid_name_before_any_side_effect(sdk_env):
    with pytest.raises(ValueError, match="invalid address name"):
        aimail_base.rename_address("sid-t1", sdk_env.old, "bad.name", sdk_env.cfg)
    assert not _FakeClient.instances, "validation must run before any gateway call"
    assert not sdk_env.signals


def test_rename_conflict_with_registered_address(sdk_env):
    other = sdk_env.sysdir / "d2"
    other.mkdir()
    (other / "agentmail.json").write_text(json.dumps(
        {"email": "alice.t1@shared.tm", "system_id": "sid-t1"}))
    with pytest.raises(ValueError, match="conflicts with registered address"):
        aimail_base.rename_address("sid-t1", sdk_env.old, "alice", sdk_env.cfg)
    assert not _FakeClient.instances
    assert not sdk_env.signals


def test_rename_unchanged_is_noop(sdk_env):
    res = aimail_base.rename_address("sid-t1", "alice.t1@shared.tm", "alice", sdk_env.cfg)
    assert res["unchanged"] is True
    assert not _FakeClient.instances, "unchanged must not touch the gateway"
    assert not sdk_env.signals


def test_rename_full_chain_shared(sdk_env):
    res = aimail_base.rename_address("sid-t1", sdk_env.old, "alice", sdk_env.cfg)
    new = "alice.t1@shared.tm"
    assert res["new_email"] == new and res["migrated"] is True
    client = _FakeClient.instances[0]
    # cloud rename body
    post = [c for c in client.calls if c[0] == "POST" and "addresses/rename" in c[1]]
    assert post and post[0][2] == {"old_email": sdk_env.old, "new_email": new}
    # whitelist cleanup keyed by the FULL old address — on a shared domain this is
    # what makes the query resolve to OUR system, not the domain owner (N2 insight)
    assert client.whitelist_queries == [sdk_env.old], \
        "whitelist cleanup must be keyed by the full address, never the bare domain"
    assert client.deleted == [7]
    # local migration: binding now carries the new email
    emails = [json.loads(p.read_text())["email"]
              for p in sdk_env.sysdir.glob("*/agentmail.json")]
    assert emails == [new], f"binding not migrated: {emails}"
    # pointer synced
    ptr = json.loads((Path(sdk_env.cfg["system_home"]) / ".aimail").read_text())
    assert ptr["email"] == new
    # rename ENDS with the inbound-live signal (SDK triggers → CLI reconciles)
    assert sdk_env.signals == [(new, "live")]


def test_rename_cloud_failure_leaves_local_untouched(sdk_env, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(aimail_tools, "_GatewayClient",
                        lambda gw, ak: SimpleNamespace(
                            _request=_boom,
                            list_whitelists_by_domain=lambda d: [],
                            delete_whitelist_entry_by_id=lambda i: None))
    with pytest.raises(ValueError, match="server-side rename"):
        aimail_base.rename_address("sid-t1", sdk_env.old, "alice", sdk_env.cfg)
    emails = [json.loads(p.read_text())["email"]
              for p in sdk_env.sysdir.glob("*/agentmail.json")]
    assert emails == [sdk_env.old], "cloud failure must not touch the local binding"
    assert not sdk_env.signals, "no signal when the rename did not happen"


# ══════════════════════════════════════════════════════════════════════════
# 2+3. CLI reconcile / down / signal locator
# ══════════════════════════════════════════════════════════════════════════

_ALICE = "alice.t1@shared.tm"
_BOB = "bob.t1@shared.tm"
_CAROL = "carol.t1@shared.tm"
_MALLORY = "mallory.t2@shared.tm"


@pytest.fixture
def cli_route_env(tmp_path, monkeypatch):
    """Shared-domain system with: alice(push), carol(pull), bob(stale),
    mallory(foreign system row in the same bridge routes file)."""
    home = tmp_path / "home"
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    (home / "bridge").mkdir(parents=True)
    (home / "bridge" / "aimail_routes.toml").write_text(
        '# Auto-generated by aimail-bridge.\n'
        f'"{_ALICE}" = "http://127.0.0.1:18080/hook"\n'
        f'"{_BOB}" = "http://127.0.0.1:18081/hook"\n'
        f'"{_CAROL}" = "http://127.0.0.1:18083/hook"\n'
        f'"{_MALLORY}" = "http://127.0.0.1:18082/hook"\n')
    sysdir = home / "systems" / "sid-t1"
    for name, acfg in (
            ("a1", {"email": _ALICE, "webhook_url": "http://127.0.0.1:18080/hook"}),
            ("c1", {"email": _CAROL, "webhook_url": ""})):  # pull binding
        d = sysdir / name
        d.mkdir(parents=True)
        (d / "agentmail.json").write_text(json.dumps(acfg))
    cfg = {"domain": "shared.tm", "system_name": "t1",
           "gateway_url": "https://gw.test", "admin_key": "AK"}
    (sysdir / "aimail_gateway.json").write_text(json.dumps(cfg))
    cli = _load(_CLI, "aimail_cli_reconcile")
    calls = []

    def _fake_sync(action, email, target_url, gateway_cfg, bridge_cfg_path, **kw):
        calls.append((action, email, target_url))
        return {"state": "ok", "action": action, "email": email,
                "target": str(target_url or ""), "reason": "route upserted"}

    monkeypatch.setattr(bridge_wire, "sync_route", _fake_sync)
    return SimpleNamespace(cli=cli, cfg=cfg, calls=calls, home=home)


def test_system_route_scope_predicates(cli_route_env):
    cli = cli_route_env.cli
    pred = cli._system_route_scope({"domain": "shared.tm", "system_name": "t1"})
    assert pred(_ALICE) and not pred(_MALLORY)
    pred2 = cli._system_route_scope({"domain": "own.test", "system_name": ""})
    assert pred2("x@own.test") and not pred2("x@other.test")
    assert cli._system_route_scope({"domain": ""}) is None, "no domain ⇒ no guess"


def test_reconcile_upserts_desired_and_withdraws_only_own_rows(cli_route_env):
    cli = cli_route_env.cli
    reported, _ = cli._reconcile_inbound_routes("sid-t1", cli_route_env.cfg)
    actions = [(a, e) for a, e, _t in cli_route_env.calls]
    # desired: alice upserted (carol is pull ⇒ no upsert)
    assert ("live", _ALICE) in actions, actions
    assert not any(a == "live" and e == _CAROL for a, e in actions)
    # own rows no longer wanted: bob (stale) + carol (pull binding with a row)
    assert ("down", _BOB) in actions, actions
    assert ("down", _CAROL) in actions, actions
    # foreign system row must NEVER be touched
    assert not any(e == _MALLORY for _a, e in actions), \
        f"foreign row touched: {actions}"
    assert reported == len(cli_route_env.calls)


def test_down_withdraws_system_scope_and_anchor_always(cli_route_env, capsys):
    cli = cli_route_env.cli
    rc = cli._cmd_inbound_route(
        SimpleNamespace(inbound_live=False, inbound_down=True,
                        email=None, agent=_ALICE),
        "sid-t1", cli_route_env.cfg)
    assert rc == 0
    withdrawn = [e for a, e, _t in cli_route_env.calls if a == "down"]
    assert set(withdrawn) == {_ALICE, _BOB, _CAROL}, withdrawn
    assert _MALLORY not in withdrawn
    out = capsys.readouterr().out
    assert "inbound down:" in out and "3 route(s) withdrawn" in out, out


def test_down_backlog_line_is_d8(tmp_path, cli_route_env, monkeypatch, capsys):
    cli = cli_route_env.cli
    monkeypatch.setattr(cli, "_gateway_backlog_count", lambda cfg, emails: 3)
    rc = cli._cmd_inbound_route(
        SimpleNamespace(inbound_live=False, inbound_down=True,
                        email=None, agent=_ALICE),
        "sid-t1", cli_route_env.cfg)
    out = capsys.readouterr().out
    assert rc == 0 and "backlog 3 delivery(ies) held at gateway" in out, out
    assert "pull window 72h" in out, out


def test_live_anchor_failure_fails_closed(cli_route_env, monkeypatch):
    cli = cli_route_env.cli

    def _fail_sync(action, email, target_url, gateway_cfg, bridge_cfg_path, **kw):
        return {"state": "failed", "action": action, "email": email,
                "target": "", "reason": "bridge unreachable — nothing changed"}

    monkeypatch.setattr(bridge_wire, "sync_route", _fail_sync)
    with pytest.raises(SystemExit) as ei:
        cli._cmd_inbound_route(
            SimpleNamespace(inbound_live=True, inbound_down=False,
                            email=None, agent=_ALICE),
            "sid-t1", cli_route_env.cfg)
    assert ei.value.code == 1


def test_live_no_bridge_is_silent_success(cli_route_env, monkeypatch, capsys):
    cli = cli_route_env.cli

    def _nb(action, email, target_url, gateway_cfg, bridge_cfg_path, **kw):
        return {"state": "no_bridge", "action": action, "email": email,
                "target": "", "reason": "no bridge declared"}

    monkeypatch.setattr(bridge_wire, "sync_route", _nb)
    rc = cli._cmd_inbound_route(
        SimpleNamespace(inbound_live=True, inbound_down=False,
                        email=None, agent=_ALICE),
        "sid-t1", cli_route_env.cfg)
    assert rc == 0


def test_signal_without_sid_resolves_system_by_address(cli_route_env, capsys):
    """SDK payload carries only the address (no -s) ⇒ cmd_address must resolve
    the owning system itself, and `-a <full email>` must locate the binding."""
    cli = cli_route_env.cli
    rc = cli.cmd_address(SimpleNamespace(
        system_id=None, inbound_live=True, inbound_down=False,
        email=None, agent=_ALICE, name=None, manager=None, default=None))
    assert rc == 0, capsys.readouterr().out
    assert ("live", _ALICE) in [(a, e) for a, e, _t in cli_route_env.calls]


def test_sid_for_address_helper(cli_route_env):
    cli = cli_route_env.cli
    assert cli._sid_for_address(_ALICE) == "sid-t1"
    assert cli._sid_for_address("nobody@nowhere") == ""


# ══════════════════════════════════════════════════════════════════════════
# 4. Wiring: cmd_address set-name / set-manager / _rename_after_reg → SDK
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def cli_cmd_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    sysdir = home / "systems" / "sid-w"
    d = sysdir / "w1"
    d.mkdir(parents=True)
    old = "alice.t1@shared.tm"
    (d / "agentmail.json").write_text(json.dumps(
        {"email": old, "webhook_url": "http://127.0.0.1:18080/hook"}))
    cfg = {"domain": "shared.tm", "system_name": "t1",
           "gateway_url": "https://gw.test", "admin_key": "AK"}
    (sysdir / "aimail_gateway.json").write_text(json.dumps(cfg))
    cli = _load(_CLI, "aimail_cli_wiring")
    return SimpleNamespace(cli=cli, cfg=cfg, old=old, sysdir=sysdir)


def _addr_args(**kw):
    base = dict(system_id="sid-w", inbound_live=False, inbound_down=False,
                email=None, agent=None, name=None, manager=None, default=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_set_name_calls_sdk_entry_and_reports_signal(cli_cmd_env, monkeypatch, capsys):
    cli, old = cli_cmd_env.cli, cli_cmd_env.old
    seen = {}

    def _fake_rename(sid, old_email, new_name, cfg):
        seen["args"] = (sid, old_email, new_name)
        return {"old_email": old_email, "new_email": "bob.t1@shared.tm",
                "unchanged": False, "dir": "", "moved": True, "merged": False,
                "migrated": True, "signal": {"state": "notified"}}

    monkeypatch.setattr(aimail_base, "rename_address", _fake_rename)
    rc = cli.cmd_address(_addr_args(email=old, name="bob"))
    out = capsys.readouterr().out
    assert rc == 0
    assert seen["args"] == ("sid-w", old, "bob"), seen
    assert "地址改名" in out and "上线信号已触发路由对账" in out, out
    # the CLI itself must not touch the gateway or the bridge any more
    assert "服务端改名失败" not in out


def test_set_name_reports_lost_signal_honestly(cli_cmd_env, monkeypatch, capsys):
    cli, old = cli_cmd_env.cli, cli_cmd_env.old

    def _fake_rename(sid, old_email, new_name, cfg):
        return {"old_email": old_email, "new_email": "bob.t1@shared.tm",
                "unchanged": False, "dir": "", "moved": True, "merged": False,
                "migrated": True,
                "signal": {"state": "failed", "detail": "no aimail CLI"}}

    monkeypatch.setattr(aimail_base, "rename_address", _fake_rename)
    rc = cli.cmd_address(_addr_args(email=old, name="bob"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "上线信号未送达" in out and "路由未刷新" in out, out
    assert "新地址即日起收发" not in out, "the old false claim must stay gone"


def test_set_manager_calls_sdk_entry(cli_cmd_env, monkeypatch, capsys):
    cli, old = cli_cmd_env.cli, cli_cmd_env.old
    seen = {}

    def _fake_set(sid, email, mgr, cfg, binding_cfg):
        seen["args"] = (sid, email, mgr, binding_cfg.get("email"))

    monkeypatch.setattr(aimail_base, "set_agent_manager", _fake_set)
    rc = cli.cmd_address(_addr_args(email=old, manager="mgr@x.tm"))
    out = capsys.readouterr().out
    assert rc == 0
    assert seen["args"] == ("sid-w", old, "mgr@x.tm", old), seen
    assert "云端+本地已同步" in out


def test_register_then_rename_routes_through_sdk(cli_cmd_env, monkeypatch, capsys):
    cli = cli_cmd_env.cli
    seen = {}

    def _fake_rename(sid, old_email, new_name, cfg):
        seen["args"] = (sid, old_email, new_name)
        return {"old_email": old_email, "new_email": "alice.t1@shared.tm",
                "unchanged": False, "dir": "", "moved": True, "merged": False,
                "migrated": True, "signal": {"state": "notified"}}

    monkeypatch.setattr(aimail_base, "rename_address", _fake_rename)
    cli._rename_after_reg(cli_cmd_env.cfg, "agent", "default", "alice",
                          "sid-w", "shared.tm")
    out = capsys.readouterr().out
    # old is derived from the registrar's default name + system infix
    assert seen["args"] == ("sid-w", "agent.t1@shared.tm", "alice"), seen
    assert "地址名已改" in out
    assert "上线信号未送达" not in out

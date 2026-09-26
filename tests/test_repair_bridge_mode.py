"""repair must judge the bridge step exactly like install does.

Owner ruling 2026-09-27: "repair 的桥步骤没有模式判断需要与install对齐".

install (cli/aimail) decides with `_is_local_gateway(gw_url)`: a gateway on this
machine / local network pushes straight into the host inbound, so no bridge is
deployed and none is needed. repair's ladder ran the "start the bridge when
dead" step unconditionally, so on a direct-mode machine (and only when check
already had failures, which is what triggers the ladder) it printed
"bridge not running -> starting it" and then
"bridge not deployed (config/binary missing) -- run install first" -- both
misleading for a machine that by design has no bridge (measured 2026-09-27).

The assertions below are the contract: direct mode must not probe bridge
process state and must not call deploy_bridge.start_bridge; a remote gateway
must still start it; an unknown gateway url must fall back to the old
behaviour instead of silently claiming "no bridge needed".
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))

import deploy_bridge  # noqa: E402
import repair  # noqa: E402


def _fake_system(tmp_path, monkeypatch, gw_url):
    """A system dir the way install leaves it (aimail_gateway.json), or none."""
    sid = "unit-sid"
    sysdir = tmp_path / "systems" / sid
    sysdir.mkdir(parents=True, exist_ok=True)
    if gw_url is not None:
        (sysdir / "aimail_gateway.json").write_text(json.dumps({
            "admin_key": "AK-TEST", "gateway_url": gw_url, "system_id": sid,
        }))
    monkeypatch.setattr(repair, "SYSTEMS_DIR", tmp_path / "systems")
    return sid


def _no_process_probe(monkeypatch):
    def _boom():
        raise AssertionError("direct mode must not probe bridge process state")
    monkeypatch.setattr(repair, "_bridge_pids", _boom)


def test_direct_mode_does_not_touch_the_bridge(tmp_path, monkeypatch, capsys):
    started = []
    monkeypatch.setattr(deploy_bridge, "start_bridge",
                        lambda *a, **k: started.append(a) or True)
    _no_process_probe(monkeypatch)
    sid = _fake_system(tmp_path, monkeypatch, "http://127.0.0.1:39999")

    assert repair._ensure_bridge_running(sid) is True
    assert started == [], "direct mode must not start a bridge"
    out = capsys.readouterr().out
    assert "no bridge needed" in out
    assert "starting it" not in out


def test_local_hostname_and_loopback_are_direct_mode(tmp_path, monkeypatch):
    _no_process_probe(monkeypatch)
    for url in ("http://localhost:39999", "https://127.0.0.1", "http://[::1]:8080"):
        sid = _fake_system(tmp_path, monkeypatch, url)
        assert repair._ensure_bridge_running(sid) is True, url


def test_remote_gateway_still_starts_the_bridge(tmp_path, monkeypatch, capsys):
    started = []
    monkeypatch.setattr(deploy_bridge, "start_bridge",
                        lambda *a, **k: (started.append(a), True)[1])
    monkeypatch.setattr(repair, "_bridge_pids", lambda: [])
    bdir = tmp_path / "bridge"
    (bdir / "bin").mkdir(parents=True)
    cfg = bdir / "aimail_bridge.toml"
    cfg.write_text('bind = "127.0.0.1:38081"\n')
    binf = bdir / "bin" / "aimail-bridge"
    binf.write_text("#!/bin/true\n")
    monkeypatch.setattr(repair, "BRIDGE_CFG", cfg)
    monkeypatch.setattr(repair, "BRIDGE_BIN", binf)
    sid = _fake_system(tmp_path, monkeypatch, "https://aimail.token.tm")

    assert repair._ensure_bridge_running(sid) is True
    assert len(started) == 1, "a remote gateway machine must still get its bridge"
    assert "no bridge needed" not in capsys.readouterr().out


def test_shared_mode_judgement_covers_the_documented_forms(tmp_path, monkeypatch):
    """cli/_common.is_local_gateway is the single judgement shared by install+repair."""
    import socket
    from _common import is_local_gateway as shared

    for url in ("http://127.0.0.1:39999", "https://localhost", "http://[::1]:8080",
                "127.0.0.1:39999", "http://user:pw@127.0.0.1:1"):
        assert shared(url) is True, url
    for url in ("https://aimail.token.tm", "http://10.1.2.3:8080", "", "not a url"):
        assert shared(url) is False, url
    own_ips = {i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None)}
    for ip in own_ips:
        lit = f"[{ip}]" if ":" in ip else ip          # IPv6 literals need brackets in a URL
        assert shared(f"http://{lit}:39999") is True, ip


def test_running_bridge_is_reported_ok(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(repair, "_bridge_pids", lambda: [4242])
    monkeypatch.setattr(deploy_bridge, "start_bridge",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("an alive bridge must not be restarted")))
    sid = _fake_system(tmp_path, monkeypatch, "https://aimail.token.tm")

    assert repair._ensure_bridge_running(sid) is True
    assert "already running pid=4242" in capsys.readouterr().out


def test_unknown_gateway_url_keeps_the_old_behaviour(tmp_path, monkeypatch, capsys):
    """No gateway.json -> cannot judge the mode -> never claim 'no bridge needed'."""
    probed = []
    monkeypatch.setattr(repair, "_bridge_pids", lambda: (probed.append(1), [])[1])
    monkeypatch.setattr(repair, "BRIDGE_CFG", tmp_path / "missing.toml")
    monkeypatch.setattr(repair, "BRIDGE_BIN", tmp_path / "missing-bin")
    sid = _fake_system(tmp_path, monkeypatch, None)

    assert repair._ensure_bridge_running(sid) is False
    assert probed, "unknown mode must fall back to probing process state"
    out = capsys.readouterr().out
    assert "no bridge needed" not in out
    assert "run install first" in out

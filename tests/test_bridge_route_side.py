"""Route side contract (owner ruling 2026-09-27).

"registration succeeded" and "route added" are two separate outcomes, each with
its own success rate, and the route is pushed when the host inbound is actually
serving (registration happens before the host restarts — production 2026-09-26
created the route at 15:53:17 and the bridge pruned it at 15:56:07).

Assertions:
  1. a route is only pushed when the local receive endpoint is up;
  2. no bridge => 'no_bridge' (a skip with a reason, never a failure);
  3. every binding of the system gets its own route, targeted at that binding's
     own webhook_url (agentmail.json is the single trusted source);
  4. a bridge error is REPORTED ('failed' + the repair command), never swallowed
     (the old register_bridge_route returned {"error": ...} and every caller
     dropped it);
  5. the CLI/repair integration reports the same lines and never raises.

The TS hosts assert the mirrored contract in
tssdk/packages/mail-core/test/bridge-route.test.ts.
"""
import http.server
import json
import pathlib
import socket
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _d in (ROOT / "cli", ROOT / "pysdk"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import aimail_base  # noqa: E402
import repair  # noqa: E402


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep pytest output clean
        pass

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        self.server.posts.append((self.path, json.loads(body.decode() or "{}")))
        code = getattr(self.server, "fail_with", None) or 200
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_GET(self):  # noqa: N802
        self.send_response(404)
        self.end_headers()


class _Server:
    """A local HTTP server that can be stopped (to simulate 'host not serving')."""

    def __init__(self):
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.posts = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/aimail/inbound"

    @property
    def posts(self):
        return self.httpd.posts

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    return tmp_path


def _write_system(home: pathlib.Path, sid: str, admin_port: int) -> None:
    d = home / "systems" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "aimail_gateway.json").write_text(json.dumps({
        "gateway_url": "https://gw.test", "admin_key": "AK", "system_id": sid,
        "bridge_admin_port": admin_port,
    }))


def _write_binding(home: pathlib.Path, sid: str, email: str, webhook_url: str) -> None:
    d = home / "systems" / sid / email.replace("@", "_").replace("/", "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / "agentmail.json").write_text(json.dumps({
        "email": email, "system_id": sid, "domain": "gw.test", "api_key": "k" * 64,
        "webhook_url": webhook_url, "webhook_secret": "s" * 64,
    }))


def test_no_bridge_is_a_skip_with_a_reason(home):
    _write_system(home, "sys-1", _free_port())          # nothing listening there
    _write_binding(home, "sys-1", "agent.acme@gw.test", "http://127.0.0.1:1/aimail/inbound")

    out = aimail_base.ensure_bridge_routes_for_system("sys-1")
    assert len(out) == 1 and out[0]["state"] == "no_bridge"
    assert out[0]["count"] == 1
    assert "aimail bridge --restart" in out[0]["detail"]
    assert aimail_base.route_outcome_is_warning(out[0]) is False


def test_host_not_serving_defers_the_route(home):
    bridge = _Server()
    try:
        _write_system(home, "sys-1", bridge.httpd.server_address[1])
        _write_binding(home, "sys-1", "agent.acme@gw.test", "http://127.0.0.1:1/aimail/inbound")
        out = aimail_base.ensure_bridge_route(
            "sys-1", "agent.acme@gw.test",
            {"bridge_admin_port": bridge.httpd.server_address[1]},
            "http://127.0.0.1:1/aimail/inbound")
        assert out["state"] == "host_not_serving"
        assert bridge.posts == []                       # nothing pushed at a dead target
    finally:
        bridge.stop()


def test_every_binding_gets_its_own_route_at_its_own_url(home):
    bridge, host = _Server(), _Server()
    try:
        _write_system(home, "sys-1", bridge.httpd.server_address[1])
        _write_binding(home, "sys-1", "agent.one@gw.test", host.url)
        _write_binding(home, "sys-1", "agent.two@gw.test", host.url)

        out = aimail_base.ensure_bridge_routes_for_system("sys-1")
        assert [(o["email"], o["state"]) for o in out] == [
            ("agent.one@gw.test", "ok"), ("agent.two@gw.test", "ok")]
        assert [(p[1]["email"], p[1]["host"]) for p in bridge.posts] == [
            ("agent.one@gw.test", host.url), ("agent.two@gw.test", host.url)]
        assert aimail_base.format_bridge_route_line(out[0]) == f"route: agent.one@gw.test -> {host.url}"
    finally:
        bridge.stop()
        host.stop()


def test_a_bridge_error_is_reported_not_swallowed(home):
    bridge, host = _Server(), _Server()
    try:
        bridge.httpd.fail_with = 500
        _write_system(home, "sys-1", bridge.httpd.server_address[1])
        _write_binding(home, "sys-1", "agent.acme@gw.test", host.url)

        out = aimail_base.ensure_bridge_routes_for_system("sys-1")
        assert out[0]["state"] == "failed"
        assert aimail_base.route_outcome_is_warning(out[0]) is True
        line = aimail_base.format_bridge_route_line(out[0])
        assert "route FAILED for agent.acme@gw.test" in line and "aimail repair" in line
    finally:
        bridge.stop()
        host.stop()


def test_binding_without_webhook_is_not_guessed(home):
    bridge = _Server()
    try:
        out = aimail_base.ensure_bridge_route(
            "sys-1", "agent.x@gw.test", {"bridge_admin_port": bridge.httpd.server_address[1]}, "")
        assert out["state"] == "host_not_serving" and "no webhook_url" in out["detail"]
        assert bridge.posts == []
        # ... and a binding with no webhook_url is not even enumerated
        _write_system(home, "sys-1", bridge.httpd.server_address[1])
        _write_binding(home, "sys-1", "agent.y@gw.test", "")
        assert aimail_base.ensure_bridge_routes_for_system("sys-1") == []
    finally:
        bridge.stop()


def test_nothing_to_do_when_no_system_or_binding(home):
    assert aimail_base.ensure_bridge_routes_for_system("") == []
    _write_system(home, "sys-1", _free_port())
    assert aimail_base.ensure_bridge_routes_for_system("sys-1") == []
    assert aimail_base.format_bridge_route_line({"state": "no_bridge", "email": "", "count": 2}) == \
        "route skipped for 2 address(es): no local bridge"


def test_repair_and_install_entry_points_report_and_never_raise(home, capsys, monkeypatch):
    """repair's ladder step is the CLI's half of the contract (hermes/deer-flow have
    no in-process hook); install calls the same shared implementation."""
    bridge, host = _Server(), _Server()
    try:
        bridge.httpd.fail_with = 500                     # a real bridge error must be visible
        _write_system(home, "sys-1", bridge.httpd.server_address[1])
        _write_binding(home, "sys-1", "agent.acme@gw.test", host.url)

        assert repair._repair_inbound_routes("sys-1") is False      # nothing became ok
        err = capsys.readouterr().out
        assert "route FAILED for agent.acme@gw.test" in err

        bridge.httpd.fail_with = None                    # bridge healthy again
        assert repair._repair_inbound_routes("sys-1") is True
        out = capsys.readouterr().out
        assert f"route: agent.acme@gw.test -> {host.url}" in out
        assert len(bridge.posts) == 2                    # one failed + one ok (idempotent upsert)

        # no bridge at all -> skip, not failure
        bridge.stop()
        out = aimail_base.ensure_bridge_routes_for_system("sys-1")
        assert out[0]["state"] == "no_bridge"
    finally:
        host.stop()

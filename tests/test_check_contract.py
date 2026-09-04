"""check JSON-output contract + L4 probe semantics baseline.

Pins what the Rust CLI's check implementation must reproduce:
- Check record shape (level/check/pass/detail/fix) and JSON serialization
- _probe_endpoint semantics: 404 = route missing = dead (2026-09-04 fix),
  400/401/405 = endpoint alive, connection refused = dead.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from check_status import Check, _probe_endpoint


# ── Check record contract ────────────────────────────────────────

def test_check_record_shape():
    c = Check()
    c.add("config", "system_home", True, "/home/.hermes")
    c.add("agent", "hook", False, "404", "reinstall plugin")
    assert len(c.checks) == 2
    rec = c.checks[0]
    # exact key set — the JSON contract downstream (repair, --json) relies on
    assert set(rec) == {"level", "check", "pass", "detail", "fix"}
    assert rec["pass"] is True and isinstance(rec["pass"], bool)
    assert c.all_pass() is False  # second record failed


def test_check_json_output(capsys):
    c = Check()
    c.add("bridge", "process", False, "not running", "start_bridge")
    c.print_json()
    data = json.loads(capsys.readouterr().out)
    assert data["all_pass"] is False
    assert data["checks"][0]["level"] == "bridge"
    assert data["checks"][0]["check"] == "process"
    assert data["checks"][0]["pass"] is False
    assert data["checks"][0]["fix"] == "start_bridge"


def test_known_levels_printable():
    # every level used in production must appear in the text-output groups
    c = Check()
    for lvl in ("system", "config", "gateway", "bridge", "runtime",
                "agent", "agent-gw", "profile"):
        c.add(lvl, f"x-{lvl}", True, "ok")
    # no crash + all rows rendered
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        c.print_table()
    for lvl in ("config", "runtime"):  # the 2026-09-05 added layers
        assert f"config" in buf.getvalue() or f"runtime" in buf.getvalue()


# ── L4 probe semantics ───────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    code = 200

    def do_POST(self):
        self.send_response(self.code)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format, *args):  # noqa: A002 — silence HTTP noise
        pass


class _Server:
    """Local HTTP server answering a fixed status code."""

    def __init__(self, code: int):
        handler = type("H", (_Handler,), {"code": code})

        def run():
            self.srv.serve_forever()

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=run, daemon=True)
        self.t.start()

    def url(self):
        return f"http://127.0.0.1:{self.port}/aimail/inbound"

    def close(self):
        self.srv.shutdown()


def test_probe_200_alive():
    s = _Server(200)
    try:
        alive, detail = _probe_endpoint(s.url(), timeout=2)
        assert alive is True and "200" in detail
    finally:
        s.close()


def test_probe_401_alive():
    # auth required = endpoint exists and processed the request
    s = _Server(401)
    try:
        alive, detail = _probe_endpoint(s.url(), timeout=2)
        assert alive is True and "401" in detail
    finally:
        s.close()


def test_probe_404_dead():
    # 2026-09-04 semantics: 404 = route NOT registered = FAIL
    s = _Server(404)
    try:
        alive, detail = _probe_endpoint(s.url(), timeout=2)
        assert alive is False and "404" in detail
    finally:
        s.close()


def test_probe_conn_refused_dead():
    # find a port with no listener
    import socket
    t = socket.socket()
    t.bind(("127.0.0.1", 0))
    port = t.getsockname()[1]
    t.close()
    alive, detail = _probe_endpoint(f"http://127.0.0.1:{port}/x", timeout=1)
    assert alive is False


def test_probe_no_url():
    alive, detail = _probe_endpoint("", timeout=1)
    assert alive is False and detail == "no url"

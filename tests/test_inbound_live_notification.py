"""Inbound live/down notification — `aimail address --inbound-live|--inbound-down`.

Phase 2a (2026-09-28). The SDK tells the CLI "inbound is live / inbound is down" for one
address; the CLI decides what this *machine* must do (see cli/bridge_wire.py). These tests
are falsifiable: each one names the mutation that must turn it red.

  1. `--inbound-live` on the `address` family must NEVER silently degrade into the address
     list (the 2026-09-25 class of failure). RED anchor: delete the pre-branch (mutant CLI
     copied to a temp dir) ⇒ the list output comes back and the assertion fails.
  2. a pull binding (no local endpoint) ⇒ an explicit `skipped` + reason, exit 0, and not
     one byte pushed at the bridge.
  3. a declared+reachable bridge ⇒ idempotent route upsert of the FULL absolute URL (twice
     in, one route out); `--inbound-down` withdraws it.
  4. a declared but unreachable bridge ⇒ nothing is touched anywhere + one warning line
     (fail closed), not a silent green.
  5. a scheme-less `host:port` binding value is refused with a reason — the CLI never
     invents `http://host:port:80/...`.
  6. the two switches stay hidden (zero bloat in `address --help`).

The binding/config file names come from the contract constants (never spelled out here).
"""
import hashlib
import http.server
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import urllib.parse

import pytest
from aimail_contract import BINDING_FILE

REPO = pathlib.Path(__file__).resolve().parents[1]
CLI = REPO / "cli" / "aimail"
SID = "sys-test"
EMAIL = "agent.one@gw.test"
#: a real local receive endpoint shape; the port is a free-space placeholder, never probed
TARGET_URL = "http://127.0.0.1:8645/hook"


# ── fake bridge: the admin API contract read out of ~/aimail-bridge/src/admin.rs ──────

class _AdminHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep pytest output clean
        pass

    def _json(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        store = self.server.store
        if self.path == "/health":
            return self._json(200, {"status": "ok", "uptime_secs": 1, "version": "test+deadbeef"})
        if self.path == "/api/v1/routes":
            return self._json(200, [{"email": e, "host": h, "port": p}
                                    for e, (h, p) in store["routes"].items()])
        return self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        store = self.server.store
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        parsed = json.loads(body.decode() or "{}")
        store["posts"].append((self.path, parsed))
        if self.path != "/api/v1/routes":
            return self._json(404, {"error": "not found"})
        if not parsed.get("email") or not parsed.get("host") or not parsed.get("port"):
            return self._json(400, {"error": "email, host, and port are required"})
        store["routes"][parsed["email"]] = (parsed["host"], parsed["port"])   # upsert
        return self._json(200, {"status": "ok", "webhook_url": ""})

    def do_DELETE(self):  # noqa: N802
        store = self.server.store
        store["deletes"].append(self.path)
        prefix = "/api/v1/routes/"
        if not self.path.startswith(prefix):
            return self._json(404, {"error": "not found"})
        email = urllib.parse.unquote(self.path[len(prefix):])
        store["routes"].pop(email, None)
        return self._json(200, "ok")


class _FakeBridge:
    """A local stand-in for the bridge admin API (route table + health)."""

    def __init__(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _AdminHandler)
        self.httpd.store = {"routes": {}, "posts": [], "deletes": []}
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    @property
    def routes(self) -> dict:
        return self.httpd.store["routes"]

    @property
    def posts(self) -> list:
        return self.httpd.store["posts"]

    @property
    def deletes(self) -> list:
        return self.httpd.store["deletes"]

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


# ── fixture builders ────────────────────────────────────────────────────────────────

def _home(tmp_path) -> pathlib.Path:
    home = tmp_path / "aimail-home"
    (home / "systems" / SID).mkdir(parents=True, exist_ok=True)
    return home


def _write_system(home, admin_port=0) -> pathlib.Path:
    cfg = {"gateway_url": "https://gw.test", "admin_key": "AK", "system_id": SID,
           "domain": "gw.test", "default_agent_name": "agent"}
    if admin_port:
        cfg["bridge_admin_port"] = admin_port
    p = home / "systems" / SID / "aimail_gateway.json"
    p.write_text(json.dumps(cfg))
    return p


def _write_binding(home, webhook_url, email=EMAIL) -> pathlib.Path:
    d = home / "systems" / SID / email.replace("@", "_")
    d.mkdir(parents=True, exist_ok=True)
    p = d / BINDING_FILE
    p.write_text(json.dumps({"email": email, "system_id": SID, "domain": "gw.test",
                             "api_key": "k" * 64, "webhook_url": webhook_url,
                             "webhook_secret": "s" * 64}))
    return p


def _write_bridge_cfg(home, port, mode="push") -> pathlib.Path:
    d = home / "bridge"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "aimail_bridge.toml"
    p.write_text(f'schema_version = 1\nbind = "127.0.0.1:{port}"\nmode = "{mode}"\n')
    return p


def _run_cli(home, *argv, cli=None):
    env = dict(os.environ, HOME=str(home), AIMAIL_HOME=str(home))
    return subprocess.run([sys.executable, str(cli or CLI), *argv],
                          env=env, capture_output=True, text=True, timeout=120)


def _live(home, *extra, cli=None):
    return _run_cli(home, "address", "-s", SID, "-a", "agent", "--inbound-live",
                    *extra, cli=cli)


def _down(home, *extra, cli=None):
    return _run_cli(home, "address", "-s", SID, "-a", "agent", "--inbound-down",
                    *extra, cli=cli)


def _snapshot(root: pathlib.Path) -> dict:
    """content hash of every file under root (proves a call changed nothing)."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


LIST_MARKER = "默认主 agent 名"       # the visible `address` list branch's own header


# ── 1. never a silent fall-through into the list ────────────────────────────────────

def test_inbound_live_never_falls_through_to_list(tmp_path):
    home = _home(tmp_path)
    _write_system(home)
    _write_binding(home, TARGET_URL)
    # no bridge declared -> the new branch is a silent no-op, never the address list
    listed = _run_cli(home, "address", "-s", SID, "-a", "agent")
    assert listed.returncode == 0 and LIST_MARKER in listed.stdout

    live = _live(home)
    assert live.returncode == 0, live.stderr
    assert LIST_MARKER not in live.stdout, "the flag fell through into the address list"
    assert live.stdout.strip() == "", "no bridge declared: nothing to do, nothing to say"
    assert "未找到" not in live.stdout and "Traceback" not in live.stderr


def test_removing_the_pre_branch_is_detected_red(tmp_path):
    """The RED anchor for the test above: strip the pre-branch from a copy of the CLI and
    the *same* assertion must fire (the flag degrades to `list`)."""
    home = _home(tmp_path)
    _write_system(home)
    _write_binding(home, TARGET_URL)

    mutant_cli = tmp_path / "mutant" / "cli"
    shutil.copytree(REPO / "cli", mutant_cli)
    os.symlink(REPO / "pysdk", tmp_path / "mutant" / "pysdk")

    target = mutant_cli / "aimail"
    src = target.read_text()
    guard = ('    if bool(getattr(args, "inbound_live", False)) or '
             'bool(getattr(args, "inbound_down", False)):\n'
             '        return _cmd_inbound_route(args, sid, cfg)\n')
    assert src.count(guard) == 1, "the pre-branch moved: update this mutation"
    target.write_text(src.replace(guard, "    if False:\n"
                                        "        return _cmd_inbound_route(args, sid, cfg)\n"))

    live = _live(home, cli=target)
    assert live.returncode == 0, live.stderr
    assert LIST_MARKER in live.stdout, \
        "mutant survived: the assertion in the test above would not catch a fall-through"

    shutil.rmtree(tmp_path / "mutant" / "cli", ignore_errors=True)


# ── 2. a pull binding is an explicit skip ────────────────────────────────────────────

def test_pull_binding_is_skipped_with_a_reason(tmp_path):
    bridge = _FakeBridge()
    try:
        home = _home(tmp_path)
        _write_system(home, admin_port=bridge.port)
        _write_bridge_cfg(home, bridge.port, mode="pull")
        _write_binding(home, "")

        r = _live(home)
        assert r.returncode == 0, r.stderr
        assert "route skipped for " in r.stdout and EMAIL in r.stdout
        assert "webhook_url" in r.stdout and "pull" in r.stdout, r.stdout
        assert "route:" not in r.stdout.replace("route skipped", ""), "not a fake green"
        assert bridge.posts == [], "a pull binding must push nothing"
    finally:
        bridge.stop()


# ── 3. idempotent upsert + withdrawal ──────────────────────────────────────────────

def test_live_upserts_idempotently_then_down_withdraws(tmp_path):
    bridge = _FakeBridge()
    try:
        home = _home(tmp_path)
        _write_system(home, admin_port=bridge.port)
        _write_bridge_cfg(home, bridge.port)
        _write_binding(home, TARGET_URL)

        first = _live(home)
        assert first.returncode == 0, first.stderr
        assert f"route: {EMAIL} -> {TARGET_URL}" in first.stdout, first.stdout
        assert bridge.routes == {EMAIL: (TARGET_URL, 80)}, bridge.routes

        second = _live(home)
        assert second.returncode == 0, second.stderr
        assert second.stdout == first.stdout, "the upsert must be idempotent, line and all"
        assert len(bridge.routes) == 1 and bridge.routes == {EMAIL: (TARGET_URL, 80)}
        assert len(bridge.posts) == 2 and all(p[0] == "/api/v1/routes" for p in bridge.posts)
        # the full absolute URL is passed through verbatim: no invented ":80" suffix
        assert all(p[1]["host"] == TARGET_URL for p in bridge.posts), bridge.posts
        assert all("8645:80" not in p[1]["host"] for p in bridge.posts)

        gone = _down(home)
        assert gone.returncode == 0, gone.stderr
        assert f"route withdrawn: {EMAIL}" in gone.stdout, gone.stdout
        assert bridge.routes == {}, bridge.routes
        assert [urllib.parse.unquote(p) for p in bridge.deletes] == [f"/api/v1/routes/{EMAIL}"]
    finally:
        bridge.stop()


# ── 3b. `down` is keyed by email: an emptied binding still withdraws ────────────────

def test_down_withdraws_after_the_binding_url_goes_empty(tmp_path):
    """up (binding had a URL) → the binding's URL is emptied → down ⇒ the route is GONE.

    The route table is keyed by address (`DELETE /api/v1/routes/:email`), so a withdrawal
    needs the email and a reachable bridge and nothing else. Were `down` gated on the
    binding still carrying a deliverable `webhook_url` (the pre-2026-09-28 behaviour), the
    route would outlive the push endpoint until the bridge's own health prune (up to 180s).
    RED anchor: put the target check back in front of *both* actions in
    cli/bridge_wire.py::sync_route ⇒ `bridge.routes == {}` below fails; the sibling test
    `test_down_skip_on_empty_url_mutation_is_detected_red` measures exactly that.
    """
    bridge = _FakeBridge()
    try:
        home = _home(tmp_path)
        _write_system(home, admin_port=bridge.port)
        _write_bridge_cfg(home, bridge.port)
        _write_binding(home, TARGET_URL)

        up = _live(home)
        assert up.returncode == 0, up.stderr
        assert bridge.routes == {EMAIL: (TARGET_URL, 80)}, bridge.routes
        posts_after_up = len(bridge.posts)

        # the form changed / the binding was rewritten: no local endpoint any more
        _write_binding(home, "")

        down = _down(home)
        assert down.returncode == 0, down.stderr
        assert f"route withdrawn: {EMAIL}" in down.stdout, down.stdout
        assert "route skipped" not in down.stdout, \
            "an empty webhook_url must not turn the withdrawal into a skip"
        assert bridge.routes == {}, "the route survived: mail keeps being pushed nowhere"
        assert [urllib.parse.unquote(p) for p in bridge.deletes] == [f"/api/v1/routes/{EMAIL}"], \
            bridge.deletes
        assert len(bridge.posts) == posts_after_up, "down must not upsert anything"
    finally:
        bridge.stop()


def test_down_skip_on_empty_url_mutation_is_detected_red(tmp_path):
    """RED anchor for the test above: the mutant re-couples `down` to the URL check, and the
    same expectation must then be observably violated (route still present)."""
    bridge = _FakeBridge()
    try:
        home = _home(tmp_path)
        _write_system(home, admin_port=bridge.port)
        _write_bridge_cfg(home, bridge.port)
        _write_binding(home, TARGET_URL)

        mutant_root = tmp_path / "mutant"
        mutant_cli = mutant_root / "cli"
        shutil.copytree(REPO / "cli", mutant_cli)
        os.symlink(REPO / "pysdk", mutant_root / "pysdk")
        module = mutant_cli / "bridge_wire.py"
        src = module.read_text()
        gate = "    if act != ACTION_DOWN:\n"
        assert src.count(gate) == 1, "the up/down split moved: update this mutation"
        module.write_text(src.replace(gate, "    if True:  # mutant: gate down on the URL\n"))

        assert _live(home, cli=mutant_cli / "aimail").returncode == 0
        assert bridge.routes == {EMAIL: (TARGET_URL, 80)}
        _write_binding(home, "")

        down = _down(home, cli=mutant_cli / "aimail")
        assert "route skipped" in down.stdout, down.stdout
        assert bridge.routes == {EMAIL: (TARGET_URL, 80)}, \
            "mutant survived: the assertion in the test above would not catch a stranded route"

        shutil.rmtree(mutant_cli, ignore_errors=True)
    finally:
        bridge.stop()


# ── 4. declared but unreachable -> fail closed, touch nothing ──────────────────────

def test_declared_but_unreachable_warns_and_changes_nothing(tmp_path):
    home = _home(tmp_path)
    dead = _free_port()                      # nothing listening there
    _write_system(home, admin_port=dead)
    _write_bridge_cfg(home, dead)
    _write_binding(home, TARGET_URL)

    before = _snapshot(home)
    r = _live(home)

    assert r.returncode != 0, "a declared-but-unreachable bridge must not exit green"
    assert "route FAILED for " in r.stdout and EMAIL in r.stdout, r.stdout
    assert f"127.0.0.1:{dead}" in r.stdout and "unreachable" in r.stdout, r.stdout
    assert "nothing changed" in r.stdout
    assert sum(1 for ln in r.stdout.splitlines() if "route FAILED" in ln) == 1, "one line"
    assert _snapshot(home) == before, "fail closed: not a single file may be touched"

    # `down` is judged by the same rule (no withdrawal without a reachable bridge)
    r2 = _down(home)
    assert r2.returncode != 0 and "route FAILED for " in r2.stdout
    assert _snapshot(home) == before


# ── 5. refuse a scheme-less value instead of inventing a URL ────────────────────────

def test_scheme_less_binding_is_refused_with_a_reason(tmp_path):
    bridge = _FakeBridge()
    try:
        home = _home(tmp_path)
        _write_system(home, admin_port=bridge.port)
        _write_bridge_cfg(home, bridge.port)
        _write_binding(home, "127.0.0.1:9999/hook")     # bare host:port — undeliverable

        r = _live(home)
        assert r.returncode != 0, r.stdout
        assert "not an absolute http(s) URL" in r.stdout and "127.0.0.1:9999/hook" in r.stdout
        assert "9999:80" not in r.stdout and "http://127.0.0.1:9999:" not in r.stdout, \
            "the CLI must never rebuild the value into http://host:port:80/..."
        assert bridge.posts == [], "a refused value must never reach the bridge"
    finally:
        bridge.stop()


def test_bridge_wire_validates_and_passes_the_url_through(monkeypatch):
    sys.path.insert(0, str(REPO / "cli"))
    import bridge_wire

    url, why = bridge_wire.validate_target("http://127.0.0.1:9999/hook")
    assert (url, why) == ("http://127.0.0.1:9999/hook", ""), "passed through verbatim"
    assert bridge_wire.validate_target("https://bridge.example.com/hook")[0] == \
        "https://bridge.example.com/hook"
    for bad in ("", None, "127.0.0.1:9999/hook", "127.0.0.1", "example.com/hook",
                "ftp://host/hook", "http://host:70000/hook"):
        got, why = bridge_wire.validate_target(bad)
        assert got == "" and why, f"must refuse {bad!r} with a reason, got {got!r}/{why!r}"

    # the declaration reader: no config + no port ⇒ no_bridge (a deliberate no-op)
    decl = bridge_wire.load_declaration({}, "/nonexistent/aimail_bridge.toml")
    assert decl["declared"] is False and decl["usable"] is True
    assert "no bridge config" in decl["reason"]


# ── 6. the switches stay hidden ────────────────────────────────────────────────────

def test_inbound_switches_stay_hidden_from_help(tmp_path):
    home = _home(tmp_path)
    help_out = _run_cli(home, "address", "--help")
    assert help_out.returncode == 0
    assert "--inbound-live" not in help_out.stdout and "--inbound-down" not in help_out.stdout
    assert "inbound" not in help_out.stdout, "zero help bloat"


@pytest.mark.parametrize("argv", [["--inbound-live", "--inbound-down"]])
def test_mutually_exclusive_switches_are_refused(tmp_path, argv):
    home = _home(tmp_path)
    _write_system(home)
    _write_binding(home, TARGET_URL)
    r = _run_cli(home, "address", "-s", SID, "-a", "agent", *argv)
    assert r.returncode != 0 and "mutually exclusive" in r.stdout

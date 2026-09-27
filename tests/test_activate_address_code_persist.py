"""Address-code activation self-service chain — **hermetic** (2026-09-27).

The agent-facing ``activate_address_code`` tool is the only path by which an
agent turns a one-time code (``shared_a-…``) into a working mailbox with **no
CLI and no admin credentials**.  This file pins the whole local landing:

1. the address-keyed binding file (``BINDING_FILE``) is written **0600** in a
   **0700** address dir,
   with register-isomorphic fields (the file the other 6 tools and the
   preprocess/pull path read the key from);
2. the ``aimail_gateway.json`` connection file is created **agent-scope** and
   never clobbers an existing system file;
3. the discovery pointer is written at
   ``<profile_home>/<POINTER_FILE>`` — the exact landing deer-flow / hermes /
   dsh / pi resolve, and where a ``~``/relative ``profile_home`` must be
   expanded+absolutised first (a relative path would otherwise resolve against
   whatever cwd the agent process happens to have);
4. the returned ``system_id`` is an **agent-scope** id (``shared_addr_``
   prefix) — that prefix is what enables the pull entry
   (``aimail_base.is_agent_scope_binding``);
5. a failed activation persists **nothing**;
6. the MCP-facing wrapper (wire parameter ``profile_home``) drives the same
   chain — i.e. the parameter the tool registry advertises is really wired.

Hermetic by construction: the "gateway" is a loopback ``http.server`` on an
ephemeral port (no external network, no relay, no containers), ``HOME`` and
``AIMAIL_HOME`` both point into ``tmp_path``.

Contract literals (binding / pointer filenames) always come from
``aimail_contract`` — never spelled out here (the literal ratchet gate).
"""
import json
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, "pysdk")  # ensure repo pysdk wins over any cli/ shadow

import aimail_base as base  # noqa: E402
import aimail_mcp_server as mcp  # noqa: E402
from aimail_contract import BINDING_FILE, POINTER_FILE  # noqa: E402
from aimail_tools import activate_address_code  # noqa: E402

AGENT_SCOPE_SID = "shared_addr_abc123"
ADDR = "agent.demo@example.test"
RAW_KEY = "raw-agent-key-from-code"


class _FakeGateway(BaseHTTPRequestHandler):
    """Loopback stand-in for ``POST /api/v1/activate-address-code``."""

    status = 200
    response: dict = {}
    seen: list = []

    def do_POST(self):  # noqa: N802 (http.server API)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            body = {"_unparsed": raw.decode("utf-8", "replace")}
        _FakeGateway.seen.append({"path": self.path, "body": body})
        payload = json.dumps(_FakeGateway.response).encode("utf-8")
        self.send_response(_FakeGateway.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_a):  # keep pytest output clean
        pass


@pytest.fixture
def gateway():
    """Ephemeral loopback gateway; returns ``(url, seen)``."""
    _FakeGateway.seen = []
    _FakeGateway.status = 200
    _FakeGateway.response = {
        "raw_key": RAW_KEY,
        "system_id": AGENT_SCOPE_SID,
        "email_address": ADDR,
        "expires_at": "2026-12-31T00:00:00Z",
    }
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGateway)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", _FakeGateway.seen
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(5.0)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Fully private HOME + AIMAIL_HOME (no host state can leak in)."""
    home = tmp_path / "home"
    ai = tmp_path / "aimail-home"
    home.mkdir()
    ai.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AIMAIL_HOME", str(ai))
    monkeypatch.delenv("AIMAIL_URL", raising=False)
    return {"home": home, "aimail": ai, "tmp": tmp_path}


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _binding(hermes_home) -> Path:
    hits = list((hermes_home["aimail"] / "systems" / AGENT_SCOPE_SID).glob(
        "*/" + BINDING_FILE))
    assert len(hits) == 1, f"expected exactly one binding, got {hits}"
    return hits[0]


def _pointer(profile_home: Path) -> Path:
    return profile_home / POINTER_FILE


# ── 1. success: binding 0600 + isomorphic fields + shared_addr_ id ─────────

def test_activation_lands_private_binding_with_register_isomorphic_fields(
        hermes_home, gateway):
    url, seen = gateway
    pf = hermes_home["tmp"] / "deer" / ".deer-flow"

    res = activate_address_code("shared_a-CODE", ADDR, gateway_url=url,
                                profile_home=str(pf))

    assert res["success"] is True, res
    assert res["raw_key"] == RAW_KEY
    assert res["system_id"].startswith("shared_addr_"), res["system_id"]
    assert res["email_address"] == ADDR
    assert res["expires_at"] == "2026-12-31T00:00:00Z"

    # the wire call really happened, lowercased address (pull matches exact SQL)
    assert len(seen) == 1, seen
    assert seen[0]["path"] == "/api/v1/activate-address-code"
    assert seen[0]["body"] == {"code": "shared_a-CODE",
                               "email_address": ADDR.lower()}

    p = _binding(hermes_home)
    assert res["config_path"] == str(p)
    assert _mode(p) == 0o600, oct(_mode(p))
    assert _mode(p.parent) == 0o700, oct(_mode(p.parent))
    cfg = json.loads(p.read_text())
    assert cfg == {
        "agent_id": "agent.demo",              # defaults to the local-part
        "email": ADDR,
        "gateway_url": url,
        "domain": "example.test",
        "system_id": AGENT_SCOPE_SID,
        "api_key": RAW_KEY,
        "expires_at": "2026-12-31T00:00:00Z",
    }
    # the atomic writer must not leave a partially-written tmp behind
    assert not (p.parent / (p.name + ".tmp")).exists()


def test_agent_scope_prefix_is_what_enables_the_pull_entry(hermes_home, gateway):
    """Wiring proof: the persisted sid drives ``is_agent_scope_binding``."""
    url, _ = gateway
    res = activate_address_code("c", ADDR, gateway_url=url)
    cfg = json.loads(_binding(hermes_home).read_text())
    assert cfg["system_id"] == res["system_id"]
    assert base.is_agent_scope_binding(cfg) is True
    assert base.resolve_agent_pull_settings(cfg, env={})["enabled"] is True


def test_gateway_connection_file_is_agent_scope_and_never_clobbers(
        hermes_home, gateway):
    url, _ = gateway
    activate_address_code("c", ADDR, gateway_url=url)

    gp = hermes_home["aimail"] / "systems" / AGENT_SCOPE_SID / "aimail_gateway.json"
    assert _mode(gp) == 0o600
    got = json.loads(gp.read_text())
    assert got == {"gateway_url": url, "system_id": AGENT_SCOPE_SID,
                   "domain": "example.test", "scope": "agent"}
    # the agent key lives in ONE place only — never in this connection file
    assert "api_key" not in got and "admin_key" not in got

    # create-if-absent: an existing system file (e.g. a real admin key) stays
    gp.write_text('{"gateway_url": "http://old", "admin_key": "keep-me"}')
    activate_address_code("c2", ADDR, gateway_url=url)
    assert json.loads(gp.read_text()) == {"gateway_url": "http://old",
                                          "admin_key": "keep-me"}


# ── 2. the pointer really lands at profile_home/POINTER_FILE ───────────────

def test_pointer_is_written_private_and_correct(hermes_home, gateway):
    url, _ = gateway
    pf = hermes_home["tmp"] / "deer" / ".deer-flow"

    res = activate_address_code("c", ADDR, gateway_url=url,
                                profile_home=str(pf))

    assert res["pointer_written"] is True
    ptr = _pointer(pf)
    assert res["pointer_path"] == str(ptr)
    assert ptr.is_file(), f"pointer not landed at {ptr}"
    assert _mode(ptr) == 0o600, oct(_mode(ptr))
    assert _mode(ptr.parent) == 0o700, oct(_mode(ptr.parent))
    assert json.loads(ptr.read_text()) == {"system_id": AGENT_SCOPE_SID,
                                           "email": ADDR}
    assert not (ptr.parent / (ptr.name + ".tmp")).exists()


def test_pointer_home_expands_tilde_and_absolutises_relative_paths(
        hermes_home, gateway, monkeypatch):
    url, _ = gateway

    # `~` → $HOME (which is the scratch dir), never the operator's real home
    res = activate_address_code("c", ADDR, gateway_url=url, profile_home="~/pf")
    assert res["pointer_path"] == str(hermes_home["home"] / "pf" / POINTER_FILE)
    assert json.loads(_pointer(hermes_home["home"] / "pf").read_text()) == {
        "system_id": AGENT_SCOPE_SID, "email": ADDR}

    # relative → absolute against the *current* cwd, not left relative
    monkeypatch.chdir(hermes_home["tmp"])
    res = activate_address_code("c", ADDR, gateway_url=url, profile_home="relhome")
    want = hermes_home["tmp"] / "relhome" / POINTER_FILE
    assert res["pointer_path"] == str(want)
    assert want.is_file()


def test_pointer_omitted_skips_pointer_but_keeps_the_binding(hermes_home, gateway):
    url, _ = gateway
    res = activate_address_code("c", ADDR, gateway_url=url)

    assert res["success"] is True
    assert res["pointer_written"] is False and res["pointer_path"] == ""
    assert _binding(hermes_home).is_file()          # binding still landed
    assert list(hermes_home["tmp"].rglob(POINTER_FILE)) == []


def test_gateway_url_falls_back_to_env(hermes_home, gateway, monkeypatch):
    url, _ = gateway
    monkeypatch.setenv("AIMAIL_URL", url)
    pf = hermes_home["tmp"] / "pf"
    res = activate_address_code("c", ADDR, profile_home=str(pf))
    assert res["success"] is True and _pointer(pf).is_file()


def test_missing_gateway_url_is_an_error(hermes_home, gateway):
    with pytest.raises(ValueError):
        activate_address_code("c", ADDR)


# ── 3. failure ⇒ nothing persisted ─────────────────────────────────────────

def test_rejected_code_persists_nothing(hermes_home, gateway):
    url, _ = gateway
    _FakeGateway.status = 400
    _FakeGateway.response = {"error": "code expired"}   # no raw_key ⇒ failure
    pf = hermes_home["tmp"] / "pf"

    res = activate_address_code("bad", ADDR, gateway_url=url,
                                profile_home=str(pf))

    assert res["success"] is False and res["status"] == 400, res
    assert "code expired" in res["error"]
    assert not (hermes_home["aimail"] / "systems" / AGENT_SCOPE_SID).exists()
    assert not _pointer(pf).exists()
    assert list(hermes_home["aimail"].rglob(BINDING_FILE)) == []


# ── 4. MCP-facing wire parameter is really wired ───────────────────────────

def test_mcp_tool_wrapper_drives_the_same_chain(hermes_home, gateway):
    """The registry advertises ``profile_home``; the wrapper must honour it."""
    url, _ = gateway
    pf = hermes_home["tmp"] / "mcp-home"

    out = mcp.tool_activate_address_code({
        "code": "shared_a-CODE", "address": ADDR,
        "gateway_url": url, "profile_home": str(pf),
    })

    assert out["ok"] is True, out
    assert out["pointer_written"] is True
    assert out["system_id"].startswith("shared_addr_")
    assert json.loads(_pointer(pf).read_text()) == {
        "system_id": AGENT_SCOPE_SID, "email": ADDR}
    assert _binding(hermes_home).is_file()


def test_mcp_registry_advertises_profile_home(hermes_home):
    """Discoverability: the tool list itself carries the home parameter."""
    tool = next(t for t in mcp.TOOLS if t["name"] == "activate_address_code")
    props = tool["inputSchema"]["properties"]
    assert "profile_home" in props, sorted(props)
    assert props["profile_home"]["type"] == "string"
    assert tool["inputSchema"]["required"] == ["code", "address"]

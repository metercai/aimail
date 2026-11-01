"""X-Api-Identity header contract — every signed (non-empty-key) gateway client
must name its identity (D1, gateway >= 1.0.1 strict mode returns
401 identity_required on empty identity).

Two invariants are pinned here:

1. Client-level: a signed key WITH identity puts X-Api-Identity on the
   request; a signed key without identity is NOT refused client-side —
   the request still goes out (header omitted) and the gateway decides.
   Since the empty-identity full-table-scan fallback was removed for
   security, the gateway now ALWAYS answers empty identity with
   401 identity_required (there is no degraded mode).
2. Construction-level: every agent-scope call site passes identity=email,
   every system-scope (admin_key) call site passes identity=system_id —
   the L3 hermes S1-11/12/13/14/19 failure class (agent replies went out
   unsigned-identity and were 401'd by the gateway).

Public-endpoint call sites (activate-address / activate-address-code) carry
an EMPTY key and are intentionally excluded: no key => no signature => D1
does not apply.
"""
import re
import sys
from pathlib import Path

# ── Self-contained environment isolation (owner rule: never depend on a
# preinstalled environment). An earlier test file in the same pytest run may
# have loaded the INSTALLED `agent-aimail` wheel, whose __init__ inserts
# site-packages/aimail at sys.path[0] and caches its flat modules in
# sys.modules — silently shadowing this repo's pysdk/. That is exactly the
# "depend on a preinstalled environment" trap the D1 bug came from. So before
# importing any core module: drop cached site-packages copies and force the
# repo dirs ahead of the installed package. (Repo dirs at index 0 is also what
# tests/sdk/conftest.py does for a fresh run.)
_REPO = Path(__file__).resolve().parents[2]
_REPO_DIRS = [
    str(_REPO / "pysdk"),
    str(_REPO / "pysdk" / "hermes"),
    str(_REPO / "pysdk" / "deer-flow"),
]
_SP_MARK = "/site-packages/aimail"
for _name in ("aimail_base", "aimail_tools", "aimail_board", "sdk_ops",
              "aimail_agent", "aimail_contract", "gateway_api",
              "aimail_deerflow", "install", "_resources_release"):
    _mod = sys.modules.get(_name)
    if _mod is not None and _SP_MARK in str(getattr(_mod, "__file__", "")):
        del sys.modules[_name]
for _p in [p for p in sys.path if _SP_MARK in p]:
    sys.path.remove(_p)
    sys.path.append(_p)
for _p in reversed(_REPO_DIRS):
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import aimail_base as ab
import aimail_board as B
import aimail_tools as T

import sdk_ops as SO

REPO = _REPO
HERMES_SRC = REPO / "pysdk" / "hermes" / "aimail_hermes.py"

# Guard: this test must exercise the REPO sources, not a pip-installed
# agent-aimail wheel. Fail loudly instead of silently testing old code.
for _m in (ab, B, T, SO):
    assert str(_m.__file__).startswith(str(REPO)), (
        f"{_m.__name__} resolved to {_m.__file__} — the installed agent-aimail "
        f"package is shadowing the repo sources; this test must run against "
        f"{REPO}/pysdk"
    )
# NOTE: we intentionally do NOT `import hermes.aimail_hermes` here. That module
# does `from aimail import hosts` at import time, which loads the installed
# `agent-aimail` package; its __init__ re-inserts site-packages/aimail ahead
# of the repo dirs (see bootstrap above). The two hermes invariants below are
# therefore pinned by reading the source text instead of importing the module,
# so this test is deterministic whether or not a wheel is installed.


def _agent_cfg(tmp_path, email="hermess1@a1.local", sid=""):
    """Shape of the L3 hermes probe config: email present, system_id empty."""
    return {
        "email": email,
        "system_id": sid,
        "gateway_url": "http://127.0.0.1:9",
        "api_key": "deadbeef",
        "domain": "a1.local",
    }


# ── 1. Client-level: header emission + no client-side gate ──────────────────


def test_signed_key_without_identity_is_not_gated_client_side(monkeypatch):
    """A signed key with empty identity is NOT refused client-side.

    The request must proceed to urlopen (transport failure on a dead port =>
    status 0 error dict), never a client-side identity_required refusal —
    the gateway decides (empty identity is always 401 identity_required
    server-side since the full-table-scan fallback was removed).
    """
    c = T._GatewayClient("http://127.0.0.1:9", "deadbeef", identity="")
    res = c._request("POST", "/api/v1/send", body={"to": []})
    assert res["status"] == 0
    assert "identity_required" not in str(res.get("error", ""))


def test_unsigned_empty_key_is_not_gated_client_side():
    """Empty key (public activation endpoints) must NOT be refused client-side."""
    c = T._GatewayClient("http://127.0.0.1:9", "", identity="")
    # Nothing may refuse it client-side: _request proceeds to urlopen (which
    # fails with connection refused -> status 0 error dict, NOT a refusal).
    res = c._request("GET", "/api/v1/activate-address")
    assert "identity_required" not in str(res.get("error", ""))


def test_identity_is_put_on_the_wire_when_set(monkeypatch):
    import urllib.request as _ur

    captured = {}

    class _Resp:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        return _Resp()

    monkeypatch.setattr(T.urllib.request, "urlopen", _fake_urlopen)
    c = T._GatewayClient("http://127.0.0.1:9", "deadbeef", identity="agent@x.com")
    c._request("POST", "/api/v1/send", body={"to": []})
    # urllib normalizes header names to first-upper: compare case-insensitively.
    low = {k.lower(): v for k, v in captured["headers"].items()}
    assert low.get("x-api-identity") == "agent@x.com"
    assert low.get("x-api-signature")


def test_signed_headers_carry_identity_for_system_key():
    c = T._GatewayClient("http://127.0.0.1:9", "deadbeef", identity="sys1")
    h = c._signed_headers("POST", "/api/v1/whoami", b"{}")
    assert h.get("X-Api-Identity") == "sys1"
    assert h.get("X-Api-Signature")
    c2 = T._GatewayClient("http://127.0.0.1:9", "deadbeef", identity="")
    assert "X-Api-Identity" not in c2._signed_headers("GET", "/x", b"")


# ── 2. Agent-scope call sites must pass identity=email ───────────────────────


def test_role_calibrator_passes_identity_email(monkeypatch, tmp_path):
    cfg = _agent_cfg(tmp_path)
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    monkeypatch.setattr(T, "_load_profile_config", lambda: cfg)
    calls = []

    class _FakeClient:
        def __init__(self, url, key, *a, **kw):
            calls.append(("init", url, key, kw))

        def _request(self, method, path, body=None, **kw):
            calls.append((method, path, body))
            return {"status": 200, "email_id": "e-1",
                    "message_id": "<m1@a1.local>", "cc": []}

    monkeypatch.setattr(T, "_GatewayClient", _FakeClient)
    res = T.role_calibrator()
    assert res["success"] is True
    init = calls[0]
    assert init[3].get("identity") == cfg["email"]  # the L3 S1-11 fix


def test_send_mail_passes_identity_email(monkeypatch, tmp_path):
    cfg = _agent_cfg(tmp_path)
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    monkeypatch.setattr(T, "_load_profile_config", lambda: cfg)
    calls = []

    class _FakeClient:
        def __init__(self, url, key, *a, **kw):
            calls.append(("init", url, key, kw))

        def send_mail(self, **kw):
            calls.append(("send_mail", kw))
            return {"status": 200, "email_id": "e-2", "message_id": "<m2@a1.local>"}

    monkeypatch.setattr(T, "_GatewayClient", _FakeClient)
    res = T.send_mail(to="bob@a1.local", subject="hi", body="there")
    assert res["success"] is True
    init = calls[0]
    assert init[3].get("identity") == cfg["email"]  # the L3 S1-12/13/14/19 fix
    assert calls[1][0] == "send_mail"


# ── 3. System-scope call sites must pass identity=system_id ──────────────────


def test_rename_address_passes_identity_system_id(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    cfg = {"gateway_url": "http://127.0.0.1:9", "admin_key": "adminkey1",
           "domain": "a1.local", "system_name": "sys"}
    calls = []

    class _FakeClient:
        def __init__(self, url, key, *a, **kw):
            calls.append(("init", url, key, kw))

        def _request(self, method, path, body=None, **kw):
            calls.append((method, path, body))
            return {"status": 200}

    monkeypatch.setattr(T, "_GatewayClient", _FakeClient)
    monkeypatch.setattr(ab, "notify_inbound_state", lambda *a, **k: {"state": "no_cli"})
    res = ab.rename_address("sys1", "agent1@a1.local", "agent2", cfg)
    assert res["unchanged"] is False
    init = calls[0]
    assert init[3].get("identity") == "sys1"
    method, path, body = calls[1]
    assert path == "/api/v1/admin/systems/sys1/addresses/rename"


def test_set_agent_manager_passes_identity_system_id(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    cfg = {"gateway_url": "http://127.0.0.1:9", "admin_key": "adminkey1"}
    calls = []

    class _FakeClient:
        def __init__(self, url, key, *a, **kw):
            calls.append(("init", url, key, kw))

        def _request(self, method, path, body=None, **kw):
            calls.append((method, path, body))
            return {"status": 200}

    monkeypatch.setattr(T, "_GatewayClient", _FakeClient)
    binding_cfg = {"agent_id": "agent1", "email": "agent1@a1.local",
                   "system_id": "sys1"}
    ab.set_agent_manager("sys1", "agent1@a1.local", "mgr@a1.local",
                         cfg, binding_cfg)
    init = calls[0]
    assert init[3].get("identity") == "sys1"
    method, path, body = calls[1]
    assert path == "/api/v1/admin/agent-meta/agent1@a1.local"
    assert body == {"manager_address": "mgr@a1.local"}


def test_sdk_ops_gateway_client_passes_identity_from_gw(monkeypatch):
    calls = []

    class _FakeClient:
        def __init__(self, url, key, *a, **kw):
            calls.append((url, key, kw))

    monkeypatch.setattr(T, "_GatewayClient", _FakeClient)
    gw = {"gateway_url": "http://127.0.0.1:9", "admin_key": "adminkey1",
          "system_id": "sys1"}
    SO._gateway_client(gw)
    assert calls[0][2].get("identity") == "sys1"


def test_deerflow_make_client_passes_identity_system_id(monkeypatch, tmp_path):
    import importlib
    df = importlib.import_module("aimail_deerflow")
    calls = []

    class _FakeClient:
        def __init__(self, url, key, *a, **kw):
            calls.append((url, key, kw))

    monkeypatch.setattr(df._tools, "_GatewayClient", _FakeClient)
    monkeypatch.setattr(df, "load_agent_config_for_key",
                        lambda sid: {"gateway_url": "http://127.0.0.1:9",
                                     "api_key": "agentkey1"})
    df.make_client(system_id="sys1")
    assert calls[0][2].get("identity") == "sys1"


def test_board_client_agent_key_passes_identity_email(monkeypatch, tmp_path):
    calls = []

    class _FakeClient:
        def __init__(self, url, key, *a, **kw):
            calls.append((url, key, kw))

    monkeypatch.setattr(B, "_GatewayClient", _FakeClient)
    board_id = "a" * 20
    cfg = {"email": "agent1@a1.local", "api_key": "agentkey1"}
    B._board_client(board_id, cfg)
    url, key, kw = calls[0]
    assert key == "agentkey1"
    assert kw.get("identity") == cfg["email"]
    # token channel (empty key) stays ungated
    monkeypatch.setattr(B, "_get_board_token", lambda bid: "tok")
    calls.clear()
    B._board_client(board_id, cfg)
    assert calls[0][1] == ""


# ── 4. Hermes adapter: source-level invariants (heavy host wiring) ───────────


def test_hermes_admin_key_sites_pass_identity_system_id():
    src = HERMES_SRC.read_text()
    # both admin_key paths (auto-register :574 / auto-deregister :792) must
    # name the system
    assert src.count("_GatewayClient(gateway_url, admin_key, identity=system_id)") >= 2
    # the empty-key public activation site stays identity-free
    m = re.search(r"_GatewayClient\(config\.get\(\"gateway_url\".*?\)\,\s*\"\", timeout=5\)", src)
    assert m, "empty-key activation site unexpectedly changed shape"


def test_hermes_client_alias_resolves_to_tools_client():
    # The adapter aliases the shared core client (single source) — pin by source.
    assert "_GatewayClient = tools._GatewayClient" in HERMES_SRC.read_text()

"""role_calibrator tool — identity-calibration welcome trigger (agent scope).

Contract (C2): the tool triggers the system welcome mail for the caller's OWN
address via the unified POST /api/v1/system/welcome endpoint (agent branch —
`to` derived from the key, body `to` ignored). One gateway call, no local
state. Requires activation first (agent-scope binding present).
"""
import aimail_agent as A
import aimail_base as ab
import aimail_mcp_server as m
import aimail_tools as T


def test_role_calibrator_registered_in_agent_toolset():
    names = [t["name"] for t in A.agent_toolset()]
    assert "role_calibrator" in names


def test_role_calibrator_in_mcp_tool_surface():
    names = {str(t.get("name") or "") for t in m.TOOLS}
    assert "role_calibrator" in names


def test_role_calibrator_requires_activation(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    monkeypatch.setattr(ab, "_CONFIG_LOADER", lambda: None)
    res = T.role_calibrator()
    assert res["success"] is False
    assert "not activated" in res["error"]


def test_role_calibrator_posts_system_welcome_with_own_key(monkeypatch, tmp_path):
    cfg = {
        "email": "agent1@token.tm",
        "system_id": "sys1",
        "gateway_url": "http://127.0.0.1:9",
        "api_key": "deadbeef",
    }
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    monkeypatch.setattr(ab, "_CONFIG_LOADER", lambda: cfg)
    calls = []

    class _FakeClient:
        def __init__(self, url, key, *a, **kw):
            calls.append(("init", url, key))

        def _request(self, method, path, body=None, **kw):
            calls.append((method, path, body))
            return {
                "status": 200,
                "email_id": "e-1",
                "message_id": "<m1@token.tm>",
                "cc": ["mgr@corp.com"],
            }

    monkeypatch.setattr(T, "_GatewayClient", _FakeClient)
    res = T.role_calibrator()
    assert res["success"] is True
    assert res["email_id"] == "e-1"
    # unified endpoint, agent branch: the client sends a schema-shaped body
    # whose `to` is ignored gateway-side; the key signs the request.
    assert calls[0] == ("init", "http://127.0.0.1:9", "deadbeef")
    method, path, body = calls[1]
    assert (method, path) == ("POST", "/api/v1/system/welcome")
    assert body == {"to": []}


def test_role_calibrator_gateway_error_is_reported(monkeypatch, tmp_path):
    cfg = {
        "email": "agent1@token.tm",
        "system_id": "sys1",
        "gateway_url": "http://127.0.0.1:9",
        "api_key": "deadbeef",
    }
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    monkeypatch.setattr(ab, "_CONFIG_LOADER", lambda: cfg)

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def _request(self, method, path, body=None, **kw):
            return {"status": 403, "error": "Agent key has no bound address"}

    monkeypatch.setattr(T, "_GatewayClient", _FakeClient)
    res = T.role_calibrator()
    assert res["success"] is False
    assert "Agent key has no bound address" in res["error"]

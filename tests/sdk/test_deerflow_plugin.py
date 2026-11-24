# -*- coding: utf-8 -*-
"""DeerFlow extension under fake-host conditions (contract §9, §11.1, §14).

The real host is not importable here, so these tests reconstruct the exact surface the
extension is allowed to touch — `registry.plugin(PluginContribution(...))`, the host's
`deerflow_extension_api.plugins` dataclasses, and its HTTP run API — and assert the
behaviour through it. The two measured traps are pinned explicitly:

  * a contribution with ``enabled=False`` (or a rejected registry) must NOT look installed;
  * a refused run must RAISE, because the shared client acks every normal return.
"""
import asyncio
import sys
import types

import pytest

import aimail_base as base
from deerflow_plugin import install
from deerflow_plugin import identity as identity_mod
from deerflow_plugin import tools as tools_mod
from deerflow_plugin.runtime import DeerFlowRuntime, render_inbound
from v2 import binding as binding_mod
from v2 import thread as thread_mod


# ── fake host API ───────────────────────────────────────────────────────────────

class _ModelTool:
    def __init__(self, name, description, input_schema, handler, group="extensions"):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler
        self.group = group


class _Contribution:
    def __init__(self, namespace, title, description="", enabled=False, tools=()):
        self.namespace = namespace
        self.title = title
        self.description = description
        self.enabled = enabled
        self.tools = tuple(tools)


class _Registry:
    def __init__(self, accept=True):
        self.accept = accept
        self.contributions = []

    def plugin(self, contribution):
        if not self.accept:
            return False
        self.contributions.append(contribution)
        return True


@pytest.fixture(autouse=True)
def fake_extension_api(monkeypatch):
    mod = types.ModuleType("deerflow_extension_api")
    plugins = types.ModuleType("deerflow_extension_api.plugins")
    plugins.ModelTool = _ModelTool
    plugins.PluginContribution = _Contribution
    mod.plugins = plugins
    monkeypatch.setitem(sys.modules, "deerflow_extension_api", mod)
    monkeypatch.setitem(sys.modules, "deerflow_extension_api.plugins", plugins)
    yield


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "aimail"))
    monkeypatch.setenv("DEERFLOW_HOME", str(tmp_path))
    monkeypatch.setenv("AIMAIL_HARDWARE_FINGERPRINT", "fp-deerflow")
    monkeypatch.delenv("AIMAIL_AGENT_SYSTEM_ID", raising=False)
    monkeypatch.delenv("AIMAIL_DEERFLOW_API", raising=False)
    return tmp_path


# ── install(): contribution shape + tool surface ────────────────────────────────

def test_install_registers_one_enabled_contribution_with_the_shared_toolset(home):
    reg = _Registry()
    install(reg, {"inbound": False})
    assert len(reg.contributions) == 1
    c = reg.contributions[0]
    assert c.namespace == "aimail" and c.title == "AIMail"
    assert c.enabled is True, "a disabled contribution is silently dropped by the host"
    names = {t.name for t in c.tools}
    assert "aimail_address" in names and len(names) == 15
    assert all(t.group == tools_mod.TOOL_GROUP for t in c.tools)


def test_rejected_contribution_does_not_look_installed(home):
    reg = _Registry(accept=False)
    assert tools_mod.register_tools(reg) == []


def test_tool_descriptions_carry_the_intent_because_names_are_hashed(home):
    """The host hashes model-facing names, so the description is the trigger (R15)."""
    reg = _Registry()
    tools_mod.register_tools(reg)
    spec = {t.name: t for t in reg.contributions[0].tools}
    assert "bind" in spec["aimail_address"].description.lower()
    assert "mailbox" in spec["aimail_address"].description.lower()


def test_tool_handlers_await_the_shared_dict_handler(home):
    reg = _Registry()
    tools_mod.register_tools(reg)
    handler = {t.name: t for t in reg.contributions[0].tools}["search_mail"].handler
    out = asyncio.run(handler({"query": "probe"}, None))
    assert isinstance(out, dict)


# ── identity ───────────────────────────────────────────────────────────────────

def test_identity_is_instance_scoped_and_multi_address(home):
    asid = identity_mod.agent_system_id({"home": str(home)})
    assert asid.startswith("asid_")
    assert identity_mod.address_model() == "multi"
    assert identity_mod.workspace_path({"home": str(home)}) == str(home)


# ── delivery: thread idempotency, accepted/refused ─────────────────────────────

def _seed(tmp_path, monkeypatch, **over):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "aimail"))
    cfg = {"email": "a@test.local", "gateway_url": "http://gw", "system_id": "system-1",
           "api_key": "sk-x", "agent_system_id": "asid_self"}
    cfg.update(over)
    base.save_agent_config("a", cfg, cfg["system_id"])
    return cfg


def test_deliver_creates_thread_then_run_with_idempotency_key(home, monkeypatch):
    cfg = _seed(home, monkeypatch)
    calls = []

    def fake_post(url, payload, headers=None, timeout=60.0):
        calls.append((url, payload, headers or {}))
        if url.endswith("/api/threads"):
            return 200, {"thread_id": payload["thread_id"]}
        return 200, {"run_id": "run-1", "status": "pending"}

    monkeypatch.setattr("deerflow_plugin.runtime._post", fake_post)
    rt = DeerFlowRuntime({"api_base": "http://api"}, agent_home=str(home))
    payload = {"subject": "Hi", "from": "x@ext", "body": "b", "message_id": "<m1@ext>"}
    res = rt.deliver(cfg, {"id": 7, "email": "a@test.local", "headers": {}, "body": payload})
    assert res["status"] == "accepted"
    assert calls[0][0].endswith("/api/threads")
    assert calls[1][0].endswith("/runs")
    assert calls[1][1]["metadata"]["aimail_thread_root"] == "m1@ext"
    assert calls[1][2]["Idempotency-Key"] == "aimail:<m1@ext>"
    assert calls[0][1]["thread_id"] == thread_mod.thread_id_for("system-1", "a@test.local", "m1@ext")


def test_deliver_raises_when_the_run_is_refused(home, monkeypatch):
    cfg = _seed(home, monkeypatch)

    def fake_post(url, payload, headers=None, timeout=60.0):
        if url.endswith("/api/threads"):
            return 200, {"thread_id": payload["thread_id"]}
        return 500, {"error": "boom"}

    monkeypatch.setattr("deerflow_plugin.runtime._post", fake_post)
    rt = DeerFlowRuntime({"api_base": "http://api"}, agent_home=str(home))
    with pytest.raises(RuntimeError):
        rt.deliver(cfg, {"id": 8, "email": "a@test.local", "headers": {}, "body": {"message_id": "<m@x>"}})


def test_consumed_pipeline_is_accepted_without_a_run(home, monkeypatch):
    cfg = _seed(home, monkeypatch)
    calls = []
    monkeypatch.setattr("deerflow_plugin.runtime._post",
                        lambda *a, **k: calls.append(a) or (200, {}))
    monkeypatch.setattr("deerflow_plugin.runtime._base.process_inbound_mail",
                        lambda payload, headers: None)
    rt = DeerFlowRuntime({"api_base": "http://api"}, agent_home=str(home))
    res = rt.deliver(cfg, {"id": 9, "email": "a@test.local", "headers": {}, "body": {"message_id": "<p@x>"}})
    assert res["status"] == "accepted" and calls == []


def test_render_inbound_marks_external_provenance():
    text = render_inbound({"subject": "s", "from": "x@ext", "body": "b"})
    assert text.startswith("[AIMail inbound]") and text.rstrip().endswith("b")

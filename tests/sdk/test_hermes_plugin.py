# -*- coding: utf-8 -*-
"""Hermes plugin under fake-host conditions (contract §9, §14; plan §6).

The real host is not importable in this repo, so these tests reconstruct exactly the
surface the plugin is allowed to touch — `register_platform`, `register_tool`,
`on_unload`, `get_config`, `build_source`, `_mark_connected`, `handle_message` setting
`_gateway_accepted` — and assert the plugin's behaviour through it. That is also how the
ack rule is pinned: a host that does NOT set the flag must never produce an ack.
"""
from aimail.contract import AGENT_TOOLSET_NAME, BINDING_FILE
import asyncio
import types

import pytest

import aimail_base as base
from hermes_plugin import PLATFORM_NAME, is_connected, register
from hermes_plugin import identity as identity_mod
from hermes_plugin import tools as tools_mod
from hermes_plugin.adapter import build_adapter_class
from hermes_plugin.runtime import HermesRuntime, render_inbound
from v2 import thread as thread_mod

ASID = "asid_0123456789abcdef0123456789abcdef"


# ── fakes that mirror the measured host surface ─────────────────────────────────

class FakeMessageEvent:
    def __init__(self, *, text, message_type=None, source=None, internal=False,
                 allow_gateway_control=False, metadata=None, message_id=None):
        self.text = text
        self.source = source
        self.internal = internal
        self.metadata = metadata or {}
        self._gateway_accepted = False


class FakeMessageType:
    TEXT = "text"


class FakeSendResult:
    def __init__(self, ok=True, detail=""):
        self.ok = ok
        self.detail = detail


fake_event_module = types.SimpleNamespace(MessageEvent=FakeMessageEvent,
                                          MessageType=FakeMessageType,
                                          SendResult=FakeSendResult)


class FakeBaseAdapter:
    """The slice of BasePlatformAdapter the plugin uses."""

    def __init__(self, config=None):
        self.config = config
        self.connected = False
        self.handled = []

    def build_source(self, **kwargs):
        return types.SimpleNamespace(**kwargs)

    def _mark_connected(self, *, listener_base=None):
        # Host signature (measured): a keyword-only listener hint, no boolean flag.
        self.connected = True

    def _mark_disconnected(self):
        self.connected = False

    async def handle_message(self, event):          # host: accepted only when routed
        self.handled.append(event)
        if getattr(self, "accept_next", True):
            event._gateway_accepted = True


class FakeCtx:
    def __init__(self):
        self.platforms = []
        self.tools = []
        self.unload = []
        self.config = {}

    def register_platform(self, **kwargs):
        self.platforms.append(kwargs)

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def on_unload(self, cb):
        self.unload.append(cb)

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    @property
    def profile_name(self):
        return "verifier"

    @property
    def plugin_id(self):
        return "aimail"


@pytest.fixture(autouse=True)
def fake_gateway(monkeypatch):
    """Install the host modules the plugin imports lazily (absent in this repo)."""
    import sys

    gateway = types.ModuleType("gateway")
    platforms = types.ModuleType("gateway.platforms")
    base_mod = types.ModuleType("gateway.platforms.base")
    base_mod.BasePlatformAdapter = FakeBaseAdapter
    base_mod.SendResult = FakeSendResult
    event_mod = types.ModuleType("gateway.platforms.event")
    event_mod.MessageEvent = FakeMessageEvent
    event_mod.MessageType = FakeMessageType
    platforms.base = base_mod
    platforms.event = event_mod
    gateway.platforms = platforms
    for name, mod in (("gateway", gateway), ("gateway.platforms", platforms),
                      ("gateway.platforms.base", base_mod),
                      ("gateway.platforms.event", event_mod)):
        monkeypatch.setitem(sys.modules, name, mod)
    yield


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "aimail"))
    monkeypatch.setenv(identity_mod.ENV_PROFILE_HOME[0], str(tmp_path / "profile"))
    monkeypatch.setenv("AIMAIL_HARDWARE_FINGERPRINT", "fp-test")
    monkeypatch.delenv("AIMAIL_AGENT_SYSTEM_ID", raising=False)
    return tmp_path / "profile"

@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "aimail"))
    monkeypatch.setenv(identity_mod.ENV_PROFILE_HOME[0], str(tmp_path / "profile"))
    monkeypatch.setenv("AIMAIL_HARDWARE_FINGERPRINT", "fp-test")
    monkeypatch.delenv("AIMAIL_AGENT_SYSTEM_ID", raising=False)
    monkeypatch.setattr("gateway.platforms.base", types.SimpleNamespace(), raising=False)
    return tmp_path / "profile"


def _seed_binding(system_id="system-1", email="a@test.local", **over):
    cfg = {"email": email, "gateway_url": "http://gw", "system_id": system_id,
           "api_key": "sk-x", "agent_system_id": ASID}
    cfg.update(over)
    base.save_agent_config(email.split("@")[0], cfg, system_id)
    return cfg


# ── register(): platform + tools + unload ──────────────────────────────────────

def test_register_wires_platform_tools_and_unload(home):
    ctx = FakeCtx()
    register(ctx)
    assert [p["name"] for p in ctx.platforms] == [PLATFORM_NAME]
    platform = ctx.platforms[0]
    assert platform["trusted_inbound"] is True          # service-type inbound
    assert callable(platform["adapter_factory"])
    assert ctx.tools, "no tools registered"
    assert "aimail_address" in [t["name"] for t in ctx.tools]
    assert all(t["toolset"] == AGENT_TOOLSET_NAME for t in ctx.tools)
    assert ctx.unload, "no unload hook (streams would outlive the plugin)"


def test_tool_adapters_accept_both_host_call_conventions():
    """Hermes calls `handler(args_dict)` POSITIONALLY (measured); keyword calls also work.

    The keyword-only wrapper shipped first raised "takes 0 positional arguments but 1 was
    given" on every real call, so both shapes are pinned here.
    """
    specs = {s["name"]: s for s in tools_mod.tool_specs()}
    assert specs, "no tools projected from the shared spec"
    handler = specs["search_mail"]["handler"]
    # Results must be TEXT: the host rejects a dict with
    # "tool_result_contract / unsupported result type: dict" (also measured).
    for call in (lambda: handler({"query": "probe"}),
                 lambda: handler(query="probe"),
                 lambda: handler(args={"query": "probe"})):
        out = call()
        assert isinstance(out, str) and out.startswith("{"), out[:80]

def test_register_platform_reports_connected_without_waiting_for_a_binding(home):
    """The platform must be up BEFORE activation (the welcome mail has to arrive)."""
    ctx = FakeCtx()
    register(ctx)
    assert is_connected(None, ctx) is True
    _seed_binding(agent_system_id=identity_mod.agent_system_id(ctx))
    assert is_connected(None, ctx) is True

# ── thread identity ────────────────────────────────────────────────────────────

def test_thread_root_follows_references_then_in_reply_to_then_message_id():
    assert thread_mod.thread_root({"references": ["<r1@x>", "<r2@x>"],
                                   "in_reply_to": "<i@x>", "message_id": "<m@x>"}) == "r1@x"
    assert thread_mod.thread_root({"in_reply_to": "<i@x>", "message_id": "<m@x>"}) == "i@x"
    assert thread_mod.thread_root({"message_id": "<m@x>"}) == "m@x"
    assert thread_mod.thread_root({"headers": {}}) == ""


def test_chat_id_is_deterministic_and_session_safe():
    one = thread_mod.chat_id_for("root-1")
    two = thread_mod.chat_id_for("root-1")
    other = thread_mod.chat_id_for("root-2")
    assert one == two and one != other
    assert one.startswith(thread_mod.CHAT_ID_PREFIX)
    assert len(one) <= 32 and all(c.isalnum() or c in ":_-" for c in one)


# ── adapter: no listener, ack comes from the host flag ─────────────────────────

def test_adapter_connects_without_binding_a_port(home):
    Adapter = build_adapter_class(FakeBaseAdapter, event_module=fake_event_module)
    adapter = Adapter(None)
    adapter.runtime = types.SimpleNamespace(reconcile=lambda: [], stop=lambda: None)
    assert asyncio.run(adapter.connect()) is True
    assert adapter.connected is True
    assert not hasattr(adapter, "port"), "the adapter must not own a listener"
    asyncio.run(adapter.disconnect())
    assert adapter.connected is False


def test_adapter_deliver_returns_host_acceptance(home):
    Adapter = build_adapter_class(FakeBaseAdapter, event_module=fake_event_module)
    adapter = Adapter(None)
    assert asyncio.run(adapter.adeliver(text="hello", chat_id="aimail:abc")) is True
    assert adapter.handled[-1].text == "hello"
    assert adapter.handled[-1].metadata == {}


def test_adapter_deliver_reports_refusal_when_host_does_not_accept(home):
    Adapter = build_adapter_class(FakeBaseAdapter, event_module=fake_event_module)
    adapter = Adapter(None)
    adapter.accept_next = False
    assert asyncio.run(adapter.adeliver(text="hello", chat_id="aimail:abc")) is False


# ── runtime: preprocessing, ack, refusal ──────────────────────────────────────

class FakeBridge:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.calls = []

    def deliver(self, *, text, chat_id, meta=None):
        self.calls.append({"text": text, "chat_id": chat_id, "meta": meta or {}})
        return self.accepted


def test_runtime_deliver_accepts_and_mentions_the_thread(home):
    bridge = FakeBridge()
    rt = HermesRuntime(bridge, agent_home=str(home), agent_system_id=ASID)
    payload = {"subject": "Hello", "from": "x@ext", "body": "body text",
               "message_id": "<m1@ext>"}
    result = rt.deliver({"email": "a@test.local", "gateway_url": "http://gw"},
                        {"id": 1, "email": "a@test.local", "headers": {}, "body": payload})
    assert result["status"] == "accepted"
    call = bridge.calls[0]
    assert "Hello" in call["text"] and "body text" in call["text"]
    assert call["chat_id"] == thread_mod.chat_id_for("m1@ext")
    assert call["meta"]["aimail_email"] == "a@test.local"



def test_runtime_keeps_a_reply_to_our_reply_in_the_same_session(home):
    """P4/C8 through the real runtime: the alias decides the session identity.

    Sequence: an external mail opens the thread, we answer (``send_mail`` records the
    alias), the external party answers OUR answer — its ``references[0]`` is our own
    Message-ID, which must still resolve to the session the conversation started in.
    """
    from pathlib import Path

    from v2 import outbound

    bridge = FakeBridge()
    rt = HermesRuntime(bridge, agent_home=str(home), agent_system_id=ASID)
    binding_dir = str(Path(home) / "aimail" / "systems" / "system-1" / "a_test.local")
    cfg = {"email": "a@test.local", "gateway_url": "http://gw", "system_id": "system-1",
           "config_path": str(Path(binding_dir) / BINDING_FILE)}

    first = {"subject": "Question", "from": "ext@outside", "body": "q",
             "message_id": "<a@ext>"}
    rt.deliver(cfg, {"id": 1, "email": "a@test.local", "headers": {}, "body": first})
    root = outbound.resolve_root({"message_id": "a@ext"}, {}, binding_dir)
    assert outbound.record(binding_dir, "<out-1@aimail.test>", root) is True

    back = {"subject": "Re: Question", "from": "ext@outside", "body": "follow-up",
            "message_id": "<b@ext>",
            "references": ["<out-1@aimail.test>", "<a@ext>"]}
    rt.deliver(cfg, {"id": 2, "email": "a@test.local", "headers": {}, "body": back})

    assert len(bridge.calls) == 2
    assert bridge.calls[1]["chat_id"] == bridge.calls[0]["chat_id"]  # same session
    # And without the alias it would NOT have been — the mechanism is what fixes it.
    assert thread_mod.chat_id_for("<out-1@aimail.test>") != bridge.calls[0]["chat_id"]
    assert bridge.calls[1]["meta"]["aimail_thread_root"] == "a@ext"


def test_runtime_opens_a_new_session_for_a_new_thread(home):
    """P4 anti-regression: an unrelated mail must NOT join the earlier session."""
    bridge = FakeBridge()
    rt = HermesRuntime(bridge, agent_home=str(home), agent_system_id=ASID)
    rt.deliver({}, {"id": 1, "email": "a@test.local", "headers": {},
                    "body": {"subject": "one", "body": "1", "message_id": "<m1@ext>"}})
    rt.deliver({}, {"id": 2, "email": "a@test.local", "headers": {},
                    "body": {"subject": "two", "body": "2", "message_id": "<m2@ext>"}})
    assert len(bridge.calls) == 2
    assert bridge.calls[0]["chat_id"] != bridge.calls[1]["chat_id"]
def test_runtime_raises_when_host_refuses_so_nothing_is_acked(home):
    """A refusal must not return normally: the client acks every normal return."""
    bridge = FakeBridge(accepted=False)
    rt = HermesRuntime(bridge, agent_home=str(home), agent_system_id=ASID)
    with pytest.raises(RuntimeError):
        rt.deliver({}, {"id": 2, "email": "a@test.local", "headers": {},
                        "body": {"subject": "s", "body": "b", "message_id": "<m2@x>"}})


def test_runtime_consumed_pipeline_is_accepted_without_a_session(home, monkeypatch):
    """ping/pong is handled by the pipeline: accepted (no re-fetch), but no session."""
    bridge = FakeBridge()
    rt = HermesRuntime(bridge, agent_home=str(home), agent_system_id=ASID)
    monkeypatch.setattr("hermes_plugin.runtime._base.process_inbound_mail",
                        lambda payload, headers: None)
    result = rt.deliver({}, {"id": 3, "email": "a@test.local", "headers": {},
                             "body": {"subject": "ping", "body": "ping"}})
    assert result["status"] == "accepted"
    assert bridge.calls == []


def test_runtime_reconcile_arms_and_stops(home, monkeypatch):
    bridge = FakeBridge()
    rt = HermesRuntime(bridge, agent_home=str(home), agent_system_id=ASID)
    armed = []

    def _fake_reconcile(**kwargs):
        armed.append(kwargs["agent_system_id"])
        handle = types.SimpleNamespace(started=True, stop=lambda: armed.append("stopped"))
        return [handle]

    monkeypatch.setattr("hermes_plugin.runtime._system_pull.start_reconcile", _fake_reconcile)
    handles = rt.reconcile()
    assert armed[0] == ASID and len(handles) == 1
    rt.stop()
    assert "stopped" in armed


def test_render_inbound_marks_external_provenance():
    text = render_inbound({"subject": "s", "from": "x@ext", "body": "b"})
    assert text.startswith("[AIMail inbound]")
    assert "subject: s" in text and "from: x@ext" in text and text.rstrip().endswith("b")

"""agent-level runtime helpers — the code a generic Python host calls.

Scenario 1 (address-code mailbox, no CLI, no adapter) must not make the agent
write glue: the SDK ships the agent-level toolset, the skill install and the
one-call pull runtime. This file pins:

1. ``agent_toolset`` = exactly the tools an agent-scope mailbox can execute
   (board_* excluded — no board membership/token; measured HTTP 401);
   every entry carries its handler.
2. ``install_agent_skill`` copies the shipped skill into
   ``<skills>/<skill name>/SKILL.md`` and is idempotent.
3. ``start_agent_level`` injects the agent context, starts one loop per
   agent-scope binding, defaults the inbound chain to the shared
   ``process_inbound_mail`` pipeline, and refuses to guess when the process
   holds several mailboxes and no ``on_email`` is given.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "pysdk")  # repo pysdk wins over any cli/ shadow

import aimail_agent as A  # noqa: E402
import aimail_base as base  # noqa: E402
import aimail_contract as contract  # noqa: E402
from aimail_mcp_server import TOOLS  # noqa: E402

REPO_SKILL = Path(__file__).resolve().parents[2] / "resources" / "skills" / "SKILL.md"
SID = "shared_addr_abc123"
ADDR = "agent.demo@example.test"

PULL_ENVS = ("AIMAIL_PULL", "AIMAIL_PULL_INTERVAL_MS", "AIMAIL_PULL_LIMIT",
             "AIMAIL_AGENT_EMAIL", "AIMAIL_AGENT_ID", "AIMAIL_SYSTEM_ID")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Private AIMAIL_HOME + cleared pull/context env + reset injection point."""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "aimail"))
    for name in PULL_ENVS:
        monkeypatch.delenv(name, raising=False)
    saved = (base._ACTIVE_AGENT_CONFIG, base._CONFIG_LOADER)
    # set_agent_context writes these with setdefault ⇒ snapshot + restore them,
    # otherwise the ids leak into later tests (deerflow mcp env asserts default).
    saved_env = {k: os.environ.get(k) for k in
                 ("AIMAIL_AGENT_EMAIL", "AIMAIL_AGENT_ID", "AIMAIL_SYSTEM_ID")}
    base._ACTIVE_AGENT_CONFIG, base._CONFIG_LOADER = None, None
    yield
    base._ACTIVE_AGENT_CONFIG, base._CONFIG_LOADER = saved
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _binding(sid: str, addr: str, **over) -> dict:
    cfg = {"agent_id": addr.split("@")[0], "system_id": sid, "email": addr,
           "api_key": "raw-agent-key", "gateway_url": "http://127.0.0.1:1"}
    cfg.update(over)
    p = base.aimail_home() / "systems" / sid / addr / contract.BINDING_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return cfg


class FakeClient:
    """Starts no thread work: optionally feeds one mail, then returns stats."""

    def __init__(self, feed=None):
        self.feed = feed
        self.seen = []

    def start_polling(self, on_email, interval=30.0, limit=20, stop_event=None):
        self.seen.append({"interval": interval, "limit": limit})
        if self.feed is not None:
            on_email(self.feed)
        return {"rounds": 1, "pulled": 1 if self.feed else 0,
                "acked": 0, "errors": 0}


# ── 1. toolset ─────────────────────────────────────────────────────────────

def test_agent_toolset_is_the_agent_level_subset():
    tools = A.agent_toolset()
    names = sorted(t["name"] for t in tools)
    assert names == sorted(A.AGENT_TOOL_NAMES)
    assert len(names) == len(set(names))
    # board tools are included: an address-code mailbox can be a board MEMBER
    # (the invite carries the token); it just cannot create boards.
    assert sorted(n for n in names if n.startswith("board_")) == [
        "board_heartbeat", "board_members", "board_roles", "board_status",
        "board_task_list", "board_task_show"]
    for tool in tools:
        assert callable(tool["handler"]), tool["name"]
        assert tool.get("inputSchema"), tool["name"]
    # the agent-level toolset is the full registry (board included)
    assert sorted(x["name"] for x in TOOLS) == names


# ── 2. skill install ───────────────────────────────────────────────────────

def test_install_agent_skill_is_idempotent(tmp_path):
    skills = tmp_path / "skills"
    first = A.install_agent_skill(str(skills))
    want = skills / contract.AGENT_SKILL_NAME / "SKILL.md"
    assert first["installed"] is True
    assert first["path"] == str(want)
    assert want.read_text(encoding="utf-8") == REPO_SKILL.read_text(encoding="utf-8")
    second = A.install_agent_skill(str(skills))
    assert second["installed"] is False


def test_install_agent_skill_custom_subdir(tmp_path):
    out = A.install_agent_skill(str(tmp_path / "s"), subdir="mailer")
    assert out["path"] == str(tmp_path / "s" / "mailer" / "SKILL.md")


# ── 3. one-call runtime ────────────────────────────────────────────────────

def test_start_agent_level_defaults_to_the_shared_inbound_chain(monkeypatch):
    cfg = _binding(SID, ADDR)
    seen = {}

    def fake_process(payload, headers):
        seen["payload"], seen["headers"] = payload, headers
        return {"ok": True}

    monkeypatch.setattr(base, "process_inbound_mail", fake_process)
    client = FakeClient(feed={"body": {"subject": "hi"}, "headers": {"x": "1"}})

    rt = A.start_agent_level(client_for=lambda _cfg: client, log=lambda _l: None)

    assert rt["skill"] is None
    assert len(rt["handles"]) == 1
    assert [t["name"] for t in rt["tools"]] == [t["name"] for t in A.agent_toolset()]
    # default inbound chain = the shared pipeline (same one push feeds)
    assert seen["payload"] == {"subject": "hi"}
    # activation-grade context injection, so module-level tools work
    assert base._load_profile_config() == cfg
    for h in rt["handles"]:
        h.stop()


def test_default_chain_hands_enriched_to_on_agent(monkeypatch):
    """The SDK runs the shared pipeline; the host only gets the enriched turn."""
    _binding(SID, ADDR)
    monkeypatch.setattr(base, "process_inbound_mail",
                        lambda payload, headers: {"message_id": "m-1"})
    got = {}
    client = FakeClient(feed={"body": {}, "headers": {}})
    rt = A.start_agent_level(
        on_agent=lambda enriched, cfg, mail: got.update(enriched=enriched),
        client_for=lambda _cfg: client, log=lambda _l: None)
    assert got["enriched"] == {"message_id": "m-1"}
    for h in rt["handles"]:
        h.stop()


def test_default_chain_skips_the_turn_when_the_pipeline_consumed_the_frame(monkeypatch):
    _binding(SID, ADDR)
    monkeypatch.setattr(base, "process_inbound_mail", lambda payload, headers: None)
    calls = []
    client = FakeClient(feed={"body": {}, "headers": {}})
    rt = A.start_agent_level(on_agent=lambda *a: calls.append(a),
                             client_for=lambda _cfg: client, log=lambda _l: None)
    assert calls == []
    for h in rt["handles"]:
        h.stop()


def test_start_agent_level_requires_on_email_for_multiple_mailboxes():
    _binding(SID, ADDR)
    _binding(SID, "other@example.test")
    with pytest.raises(ValueError):
        A.start_agent_level(client_for=lambda _cfg: FakeClient(),
                            log=lambda _l: None)


def test_start_agent_level_installs_skill_when_asked(tmp_path):
    _binding(SID, ADDR)
    rt = A.start_agent_level(skills_dir=str(tmp_path / "skills"),
                             on_email=lambda cfg, mail: None,
                             client_for=lambda _cfg: FakeClient(),
                             log=lambda _l: None)
    assert rt["skill"]["installed"] is True
    assert Path(rt["skill"]["path"]).is_file()
    for h in rt["handles"]:
        h.stop()

def test_setup_command_installs_skill_and_reports_the_toolset(tmp_path, capsys):
    rc = A.main(["setup", "--skills-dir", str(tmp_path / "skills")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["activate_tool_visible"] is True
    assert "aimail_address" in out["toolset"]
    assert len(out["toolset"]) == 15
    assert (tmp_path / "skills" / contract.AGENT_SKILL_NAME / "SKILL.md").is_file()

class _HotAddClient:
    def __init__(self):
        self.started = 0

    def start_polling(self, cb, interval=30.0, limit=20, stop_event=None):
        self.started += 1
        while stop_event is not None and not stop_event.is_set():
            stop_event.wait(0.05)
        return {"pulled": 0, "acked": 0, "errors": 0}


def test_runtime_starts_with_no_binding_and_hot_adds_the_activated_one(tmp_path, monkeypatch):
    """The normal agent-level order is: host boots → activation happens later.

    Starting with zero bindings must be fine, and the binding that activation
    persists must be picked up by the *same* process (no second restart).
    """
    home = tmp_path / "home"
    monkeypatch.setattr(base, "AIMAIL_HOME", home, raising=False)
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    monkeypatch.delenv("AIMAIL_PULL", raising=False)
    cfg = {
        "agent_id": "hotadd", "email": "hotadd@a1.local",
        "gateway_url": "http://127.0.0.1:1", "domain": "a1.local",
        "system_id": "shared_addr_a1", "api_key": "k", "scope": "agent",
    }

    client = _HotAddClient()
    rt = A.start_agent_level(on_agent=lambda *a: None,
                             client_for=lambda _c: client,
                             log=lambda _l: None)
    assert rt["handles"] == [], "a fresh process has no binding to poll"

    saved = base.save_agent_config("hotadd", cfg, cfg["system_id"])
    base._fire_binding_created(dict(cfg, config_path=str(saved)))
    assert len(rt["handles"]) == 1, "activation must hot-add the pull handle"
    assert rt["handles"][0].email == cfg["email"]
    assert client.started == 1

    # idempotent: the same binding must not start a second loop
    base._fire_binding_created(dict(cfg, config_path=str(saved)))
    assert len(rt["handles"]) == 1
    assert client.started == 1

    rt["stop"]()
    assert rt["handles"] == []

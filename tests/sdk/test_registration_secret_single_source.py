"""The **re-registration** branch of defect #10 (two keys): the binding is the single source of truth (2026-10-02 E1+E2).

Root cause (2026-10-02, read-only evidence + three rounds of reproduction /tmp/d10-repro.py):
  A single `aimail install` for deerflow registers **3 times**
  (`cli/platforms.json` install_steps = sdk_install → register_default → register_all),
  while `pysdk/deer-flow/manage.py` calls `secrets.token_hex(32)` to **mint a new one** each time;
  `register_agent_email` exists branch was originally "parameter priority" ⇒ new value PUT overwrites gateway
  (`storage.rs:693` full overwrite), but binding is only persisted when api_key/activation_code is obtained
  (`manage.py:224-235`) ⇒ **gateway = key #3 / binding = key #1** ⇒ D2B digests on both sides do not match.
  Historical reading: 13 reds all in deerflow, other four platforms 56 green.

Fix (two layers, not contradictory):
  E1 core `aimail_base.register_agent_email` exists branch: **binding priority**, caller's value only adopted when
     Adopted when the binding lacks a secret; if the binding file exists but lacks a secret ⇒ write back to the binding first, then PUT (completing the source of truth).
  E2 caller `pysdk/deer-flow/manage.py`: reuse if binding exists, only mint new when missing (no longer creates new values).

Relationship to the six contracts of Card B (`228ebaa`): ①④ write binding values as-is ③ neither side has it ⇒ supplement now ⑤ no binding ⇒ do not write
⑥ no binding + parameter ⇒ write as-is —— this file only adds the quadrant "binding has value + caller provides another".
"""
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _d in (ROOT / "pysdk",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import aimail_base as core  # noqa: E402
import aimail_tools  # noqa: E402
import aimail_contract as _c  # noqa: E402

SID = "sys-1"
EMAIL = "agent.acme@gw.test"
BINDING_SECRET = "b" * 64
CALLER_SECRET = "c" * 64
URL = f"http://127.0.0.1:39100{_c.INBOUND_PATH}"
MGR = "m@d.tm"


@pytest.fixture(autouse=True)
def _writes(monkeypatch):
    """Record every write issued by the registration chain —— the only observable metric in this file."""
    _FakeClient.writes = []
    _FakeClient.registers = []
    monkeypatch.setattr(aimail_tools, "_GatewayClient", _FakeClient)
    return _FakeClient.writes


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    return tmp_path


class _FakeClient:
    """Gateway semantics: first register accepts secret; re-register returns "already exists"; PUT full overwrite."""

    writes: list = []
    registers: list = []
    last_email: str = EMAIL

    def __init__(self, *a, **k):
        pass

    def register_email(self, **kw):
        _FakeClient.registers.append(kw)
        _FakeClient.last_email = kw.get("email") or EMAIL
        return {"status": "0", "error": "address already exists"}

    def list_system_domains(self, sid):
        # Domain list returns based on addresses registered in this round (gateway finds row by email —— in root cause test address is derived)
        return [{"id": "7", "domain": getattr(_FakeClient, "last_email", EMAIL)}]

    def update_system_domain(self, domain_id, webhook_url="", webhook_secret=""):
        _FakeClient.writes.append((domain_id, webhook_url, webhook_secret))
        return {"status": 200}

    def activate_address(self, code, **kw):
        return {"success": True, "raw_key": "k" * 64}


def _write_binding(home: pathlib.Path, *, secret: str, email: str = EMAIL,
                   sid: str = SID, agent_id: str = "default") -> pathlib.Path:
    d = home / "systems" / sid / email.replace("@", "_")
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"email": email, "system_id": sid, "domain": "gw.test",
           "api_key": "k" * 64, "webhook_url": URL, "agent_id": agent_id}
    if secret:
        cfg["webhook_secret"] = secret
    p = d / _c.BINDING_FILE
    p.write_text(json.dumps(cfg))
    return p


# ── E1: binding has value ⇒ caller providing another key is not allowed to rewrite gateway ────────────────────────────
def test_reregistration_keeps_the_binding_secret(home):
    """RED proof first: before fix PUT carried CALLER_SECRET ⇒ gateway/binding two keys."""
    _write_binding(home, secret=BINDING_SECRET)

    core.register_agent_email(_FakeClient(), SID, EMAIL, webhook_url=URL,
                              webhook_secret=CALLER_SECRET, manager_address=MGR)

    assert _FakeClient.writes == [("7", URL, BINDING_SECRET)], (
        "binding is the single source — a fresh caller value must not overwrite the "
        "gateway (that is exactly the deerflow install re-registration divergence)")


# ── E1 backfill: binding file exists but lacks secret ⇒ caller's value written to binding first then PUT ───────────────
def test_caller_secret_is_carried_into_an_existing_binding_first(home):
    bp = _write_binding(home, secret="")

    core.register_agent_email(_FakeClient(), SID, EMAIL, webhook_url=URL,
                              webhook_secret=CALLER_SECRET, manager_address=MGR)

    assert len(_FakeClient.writes) == 1
    _, url, sec = _FakeClient.writes[0]
    assert url == URL and sec == CALLER_SECRET
    assert json.loads(bp.read_text()).get("webhook_secret") == CALLER_SECRET, (
        "binding must be backfilled with THE secret that was sent — otherwise the "
        "gateway gets a key the binding never had (the other half of the split)")


# ── E2: deerflow re-registration no longer mints new —— caller reuses the one in binding ───────────────────
def test_deerflow_reregister_reuses_the_binding_secret(home, monkeypatch):
    DOMAIN = "gw.test"
    gw_cfg = home / "systems" / SID / "aimail_gateway.json"
    gw_cfg.parent.mkdir(parents=True, exist_ok=True)
    gw_cfg.write_text(json.dumps({"gateway_url": "http://127.0.0.1:34401",
                                  "admin_key": "AK", "domain": DOMAIN,
                                  "system_name": "", "manager_address": MGR}))

    manage = _load_manage()
    email = manage.email_for_agent("default", DOMAIN, "")
    _write_binding(home, secret=BINDING_SECRET, email=email)

    rc = manage.register_agents(manager=MGR, system_id=SID, agent="default")
    assert rc == 0

    assert _FakeClient.registers, "register_email must have been called"
    got = _FakeClient.registers[0].get("webhook_secret")
    assert got == BINDING_SECRET, (
        "deerflow must reuse the binding secret on re-registration instead of minting "
        f"a fresh one (got {str(got)[:12]}…, want {BINDING_SECRET[:12]}…)")
    assert _FakeClient.writes and _FakeClient.writes[0][2] == BINDING_SECRET, (
        "the gateway write must carry the same single secret")


def _load_manage():
    path = ROOT / "pysdk" / "deer-flow" / "manage.py"
    spec = importlib.util.spec_from_file_location("df_manage_under_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

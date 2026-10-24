"""Defect 10 (2026-09-28, CLI gate L2 "via bridge + J4d repair" 401) —— registration write must carry
binding secret, and **must not wipe out cloud-stored secret when secret is missing**.

Measured lesion chain:
  repair ladder last step (bridge entry alignment) → `register_agent_email(..., webhook_url=url)`
    ← **no webhook_secret**
      → exists branch `update_system_domain(id, webhook_url, "")`
        → aimail_tools.py `update_system_domain` body `if webhook_secret:` is false
          ⇒ **key omitted** ⇒ gateway full overwrite (storage.rs `webhook_url=?1, webhook_secret=?2`)
            ⇒ registration side secret = NULL ⇒ signature side empty signature vs verification side 64-hex ⇒ 401 bad_signature.

Measured digests on both sides (released 0.1.28): verification side len=64 sha256=22d94b163553… vs signature side secret=EMPTY.

SDK de-bridging (owner decision 2026-09-28) removed that alignment step (`ensure_bridge_routes_for_system`
→ `_align_registrations_to_bridge`): **registration chain became the only write path**, Phase 0 rules therefore
must be guarded on it as-is. Rule = **binding webhook_secret is source of truth**; any registration write must carry it.
Assertions in this file:
  ⓪ Alignment step and its bridge symbol have been retired along with de-bridging (symbol-level ratchet);
  ① Registration chain **must carry** bound secret (url+secret written as a pair);
  ③ Cannot obtain secret (caller omitted + binding also missing) ⇒ **generate one on the spot and carry it** (2026-10-01 owner plan A/B;
  ④ Caller omits secret ⇒ complete from binding (single source of truth);
  ⑤ Caller omits + binding does not exist at all ⇒ **do not write** (must not erase);
  ⑥ Both values provided ⇒ write as a pair as-is (no behavior change).
③/⑤ are the double insurance for "missing secret must not overwrite"; removing either ⇒ this file turns red (see report for red-green bidirectional).
"""
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
SECRET = "s" * 64
BRIDGE_URL = f"http://127.0.0.1:38081{_c.HERMES_INBOUND_PATH}"
LOCAL_URL = f"http://127.0.0.1:39100{_c.INBOUND_PATH}"
GW = {"gateway_url": "https://gw.test", "admin_key": "AK"}

# owner ruling 2026-09-30: "env can be a fallback but cannot be empty, error directly if not satisfied" —— registration chain does not accept
# manager='' ⇒ each call site in this file must provide manager (satisfy new precondition), otherwise it will first hit
# ManagerRequiredError, unable to even test "whether write carries secret". The contract guarded by this file is
# **secret written as a pair** (orthogonal to manager): assertions unchanged word for word (no test deletion, no criteria relaxation),
# only supplementing input parameters according to the new contract.
MGR = "m@d.tm"


class _FakeClient:
    """Records every registration write (id, url, secret) — the only observable."""

    writes: list = []

    def __init__(self, url="", key=""):
        self.url, self.key = url, key

    # register_email: the address ALREADY EXISTS ⇒ the idempotent update branch
    # (exactly the branch the defect lived in).
    def register_email(self, **kw):
        return {"status": "0", "error": "address already exists"}

    def list_system_domains(self, sid):
        return [{"id": "7", "domain": EMAIL}]

    def update_system_domain(self, domain_id, webhook_url="", webhook_secret=""):
        _FakeClient.writes.append((domain_id, webhook_url, webhook_secret))
        return {"status": 200}


@pytest.fixture(autouse=True)
def _writes(monkeypatch):
    _FakeClient.writes = []
    monkeypatch.setattr(aimail_tools, "_GatewayClient", _FakeClient)
    return _FakeClient.writes


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    return tmp_path


def _write_binding(home, email=EMAIL, secret=SECRET, sid=SID) -> pathlib.Path:
    d = home / "systems" / sid / email.replace("@", "_")
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"email": email, "system_id": sid, "domain": "gw.test",
           "api_key": "k" * 64, "webhook_url": LOCAL_URL}
    if secret:
        cfg["webhook_secret"] = secret
    p = d / _c.BINDING_FILE
    p.write_text(json.dumps(cfg))
    return p


# ── ⓪ Bridge symbol retirement: write path only has registration chain ──────────────────────────────────────────
def test_the_bridge_align_step_is_retired():
    """The defect's carrier is gone; a resurrection must be visible as a red here."""
    for sym in ("ensure_bridge_routes_for_system", "_align_registrations_to_bridge",
                "ensure_bridge_route", "register_bridge_route"):
        assert not hasattr(core, sym), f"retired bridge symbol {sym} is back in aimail_base"


# ── ① Registration chain must carry bound secret (url+secret written as a pair) ────────────────────────────────
def test_registration_write_carries_the_binding_secret(home):
    """RED/GREEN anchor: before fix, what was recorded here was (..., URL, "") ⇒ cloud secret = NULL."""
    _write_binding(home)

    core.register_agent_email(_FakeClient(), SID, EMAIL, webhook_url=LOCAL_URL,
                              manager_address=MGR)

    assert _FakeClient.writes == [("7", LOCAL_URL, SECRET)], (
        "url+secret must be written as a pair — a url-only write nulls the cloud secret")


# ── ③ Cannot obtain secret ⇒ no write is sent (must not erase) ─────────────────────────────────
def test_write_carries_a_provisioned_secret_when_the_binding_has_none(home):
    """2026-10-01 owner ruling (plan A/B): binding missing secret ⇒ **generate one on the spot and carry it** to gateway
    (single source of truth=binding ⇒ gateway adopts the same one ⇒ D2B both sides digest same source, fix "two keys").

    ⚠ Old assertion `writes == []` (no write sent) **directly conflicts** with this ruling ⇒ rewrite and rename according to new contract;
    **deep intent preserved as-is**: any write must carry **non-empty secret**, never url-only PUT
    (that would erase cloud secret to NULL ⇒ permanent 401; see file header lesion chain).
    Basis: owner 2026-10-01 "A and B are not contradictory, both can be required" + "if missing, generate one and PUT sync".

    Before the fix: reaching the exists branch of register_agent_email ⇒ the PUT body has no secret key ⇒
    gateway overwrites stored cloud secret with NULL ⇒ permanent 401."""
    bp = _write_binding(home, secret="")

    core.register_agent_email(_FakeClient(), SID, EMAIL, webhook_url=LOCAL_URL,
                              manager_address=MGR)

    assert len(_FakeClient.writes) == 1, "exactly one write, carrying a provisioned secret"
    _did, _url, _sec = _FakeClient.writes[0]
    assert _url == LOCAL_URL, "carried url must be the caller's"
    assert _sec and len(_sec) == 64, "every write must carry a non-empty secret (never url-only)"
    assert json.loads(bp.read_text()).get("webhook_secret") == _sec, \
        "binding and the write must share ONE secret (single source: binding)"


# ── ④ Registration chain: caller omits secret ⇒ complete from binding ─────────────────────────────────
def test_register_chain_falls_back_to_the_binding_secret(home):
    _write_binding(home)

    core.register_agent_email(_FakeClient(), SID, EMAIL, webhook_url=BRIDGE_URL,
                              manager_address=MGR)

    assert _FakeClient.writes == [("7", BRIDGE_URL, SECRET)]


# ── ⑤ Registration chain: caller omits + binding also missing ⇒ do not write ────────────────────────────────
def test_register_chain_never_issues_a_secretless_write(home):
    assert _FakeClient.writes == []

    core.register_agent_email(_FakeClient(), SID, "ghost@gw.test", webhook_url=BRIDGE_URL,
                              manager_address=MGR)

    assert _FakeClient.writes == [], "no binding secret ⇒ leave the cloud row untouched"


# ── ⑥ Baseline invariant: both values provided ⇒ write as a pair as-is (no behavior change) ──────────────────────────
def test_register_chain_still_writes_the_pair_verbatim(home):
    core.register_agent_email(_FakeClient(), SID, EMAIL,
                              webhook_url=BRIDGE_URL, webhook_secret="x" * 64,
                              manager_address=MGR)
    assert _FakeClient.writes == [("7", BRIDGE_URL, "x" * 64)]

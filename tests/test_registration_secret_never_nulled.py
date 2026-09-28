"""第 10 缺陷 (2026-09-28, CLI 门禁 L2「经桥 + J4d repair」401) —— 注册写入必须携带
绑定 secret,且**缺 secret 时不得把云端已存 secret 抹空**。

实测病灶链:
  repair 阶梯最后一步 A2 桥入口对齐
    cli/repair.py:971 `_repair_inbound_routes` → pysdk/aimail_base.py:2033
    `ensure_bridge_routes_for_system` → :2068 `_align_registrations_to_bridge`
      → :2089 `register_agent_email(..., webhook_url=url)`   ← **无 webhook_secret**
        → exists 分支 `update_system_domain(id, webhook_url, "")`
          → aimail_tools.py `update_system_domain` body 里 `if webhook_secret:` 不成立
            ⇒ **键被省略** ⇒ 网关全量覆写 (storage.rs `webhook_url=?1, webhook_secret=?2`)
              ⇒ 注册侧 secret = NULL ⇒ 签名侧空签名 vs 验签侧 64-hex ⇒ 401 bad_signature。

两侧摘要实测(已发布 0.1.28): 验签侧 len=64 sha256=22d94b163553… vs 签名侧 secret=EMPTY。

规则(用户裁决)= **绑定 webhook_secret 即真源**;任何注册写入必须携带它。本文件的断言:
  ① 对齐路径**必带**绑定 secret(url+secret 成对写);
  ② 老绑定无 secret ⇒ 就地自供(幂等)后仍成对写;
  ③ **取不到 secret ⇒ 一条写都不发**(宁可不写,也不抹空);
  ④ 注册链的差集:调用方省略 secret ⇒ 从绑定补全(真源);
  ⑤ 调用方省略 + 绑定也没有 ⇒ **不写**(不得抹空)。
③/⑤ 是"缺 secret 不得覆写"的双保险;去掉任一条 ⇒ 本文件变红(红绿双向见 README/报告)。
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
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
GW = {"gateway_url": "https://gw.test", "admin_key": "AK"}


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
           "api_key": "k" * 64, "webhook_url": f"http://127.0.0.1:39100{_c.INBOUND_PATH}"}
    if secret:
        cfg["webhook_secret"] = secret
    p = d / _c.BINDING_FILE
    p.write_text(json.dumps(cfg))
    return p


# ── ① 对齐路径必带绑定 secret ────────────────────────────────────────────────
def test_bridge_align_carries_the_binding_secret(home):
    """RED/GREEN anchor: 修复前这里记录到的是 ("7", BRIDGE_URL, "") ⇒ 云端 secret = NULL。"""
    _write_binding(home)
    rows = [{"email": EMAIL, "system_id": SID, "webhook_secret": SECRET}]

    n = core._align_registrations_to_bridge(SID, GW, rows, BRIDGE_URL)

    assert n == 1
    assert _FakeClient.writes == [("7", BRIDGE_URL, SECRET)], (
        "url+secret must be written as a pair — a url-only write nulls the cloud secret")


# ── ①' 自供(老绑定无 secret)后仍成对写,且与落盘真源一致 ───────────────────────
def test_bridge_align_provisions_a_missing_secret_then_writes_both(home):
    p = _write_binding(home, secret="")           # 升级前的老绑定: 没有 webhook_secret
    cfg = json.loads(p.read_text())
    cfg["_config_path"] = str(p)                  # iter_agentmail_configs 注入的内部字段
    assert "webhook_secret" not in cfg

    n = core._align_registrations_to_bridge(SID, GW, [cfg], BRIDGE_URL)

    assert n == 1 and len(_FakeClient.writes) == 1
    _id, url, secret = _FakeClient.writes[0]
    assert url == BRIDGE_URL
    assert len(secret) == 64 and secret
    assert json.loads(p.read_text())["webhook_secret"] == secret, (
        "the provisioned secret must land in the binding (single source of truth)")


# ── ③ 取不到 secret ⇒ 一条写都不发(不得抹空) ─────────────────────────────────
def test_bridge_align_writes_nothing_when_no_secret_exists(home):
    """防御纵深: 绑定既无 secret 又无落盘位置(无法自供) ⇒ 跳过该绑定。

    修复前: 走到 register_agent_email 的 exists 分支 ⇒ PUT body 无 secret 键 ⇒
    网关把云端已存 secret 覆写成 NULL ⇒ 永久 401。"""
    rows = [{"email": EMAIL, "system_id": SID}]   # 内存构造, 无 _config_path

    n = core._align_registrations_to_bridge(SID, GW, rows, BRIDGE_URL)

    assert n == 0
    assert _FakeClient.writes == [], "a url-only write must never be issued without a secret"


# ── ④ 注册链: 调用方省略 secret ⇒ 从绑定补全 ─────────────────────────────────
def test_register_chain_falls_back_to_the_binding_secret(home):
    _write_binding(home)

    core.register_agent_email(_FakeClient(), SID, EMAIL, webhook_url=BRIDGE_URL)

    assert _FakeClient.writes == [("7", BRIDGE_URL, SECRET)]


# ── ⑤ 注册链: 调用方省略 + 绑定也没有 ⇒ 不写 ────────────────────────────────
def test_register_chain_never_issues_a_secretless_write(home):
    assert _FakeClient.writes == []

    core.register_agent_email(_FakeClient(), SID, "ghost@gw.test", webhook_url=BRIDGE_URL)

    assert _FakeClient.writes == [], "no binding secret ⇒ leave the cloud row untouched"


# ── 基线不变式: 两个值都给 ⇒ 原样成对写(无行为变化) ──────────────────────────
def test_register_chain_still_writes_the_pair_verbatim(home):
    core.register_agent_email(_FakeClient(), SID, EMAIL,
                              webhook_url=BRIDGE_URL, webhook_secret="x" * 64)
    assert _FakeClient.writes == [("7", BRIDGE_URL, "x" * 64)]

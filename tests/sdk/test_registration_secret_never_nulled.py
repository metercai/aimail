"""第 10 缺陷 (2026-09-28, CLI 门禁 L2「经桥 + J4d repair」401) —— 注册写入必须携带
绑定 secret,且**缺 secret 时不得把云端已存 secret 抹空**。

实测病灶链:
  repair 阶梯最后一步(桥入口对齐) → `register_agent_email(..., webhook_url=url)`
    ← **无 webhook_secret**
      → exists 分支 `update_system_domain(id, webhook_url, "")`
        → aimail_tools.py `update_system_domain` body 里 `if webhook_secret:` 不成立
          ⇒ **键被省略** ⇒ 网关全量覆写 (storage.rs `webhook_url=?1, webhook_secret=?2`)
            ⇒ 注册侧 secret = NULL ⇒ 签名侧空签名 vs 验签侧 64-hex ⇒ 401 bad_signature。

两侧摘要实测(已发布 0.1.28): 验签侧 len=64 sha256=22d94b163553… vs 签名侧 secret=EMPTY。

SDK 去桥化(owner 裁决 2026-09-28)删掉了那次对齐步(`ensure_bridge_routes_for_system`
→ `_align_registrations_to_bridge`): **注册链成了唯一的写入路径**,Phase 0 的规则因此
必须原样守在它身上。规则 = **绑定 webhook_secret 即真源**;任何注册写入必须携带它。
本文件的断言:
  ⓪ 对齐步与其桥符号已随去桥化一并退役(符号级棘轮);
  ① 注册链**必带**绑定 secret(url+secret 成对写);
  ③ 取不到 secret(调用方省略 + 绑定也没有)⇒ **就地补一把并携带**(2026-10-01 owner 方案A/B;
  ④ 调用方省略 secret ⇒ 从绑定补全(真源);
  ⑤ 调用方省略 + 绑定根本不存在 ⇒ **不写**(不得抹空);
  ⑥ 两个值都给 ⇒ 原样成对写(无行为变化)。
③/⑤ 是"缺 secret 不得覆写"的双保险;去掉任一条 ⇒ 本文件变红(红绿双向见报告)。
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

# owner 裁决 2026-09-30:「env 可兜底但不可为空,不满足直接报错」—— 注册链不接受
# manager='' ⇒ 本文件各调用点必须给出 manager(满足新前置),否则先撞
# ManagerRequiredError, 连"写入是否带 secret"都测不到。本文件守护的契约是
# **secret 成对写**(与 manager 正交): 断言一字未改(不删测试、不放宽判据),
# 只按新契约补齐入参。
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


# ── ⓪ 桥符号退役: 写入路径只剩注册链 ──────────────────────────────────────────
def test_the_bridge_align_step_is_retired():
    """The defect's carrier is gone; a resurrection must be visible as a red here."""
    for sym in ("ensure_bridge_routes_for_system", "_align_registrations_to_bridge",
                "ensure_bridge_route", "register_bridge_route"):
        assert not hasattr(core, sym), f"retired bridge symbol {sym} is back in aimail_base"


# ── ① 注册链必带绑定 secret(url+secret 成对写) ────────────────────────────────
def test_registration_write_carries_the_binding_secret(home):
    """RED/GREEN anchor: 修复前这里记录到的是 (..., URL, "") ⇒ 云端 secret = NULL."""
    _write_binding(home)

    core.register_agent_email(_FakeClient(), SID, EMAIL, webhook_url=LOCAL_URL,
                              manager_address=MGR)

    assert _FakeClient.writes == [("7", LOCAL_URL, SECRET)], (
        "url+secret must be written as a pair — a url-only write nulls the cloud secret")


# ── ③ 取不到 secret ⇒ 一条写都不发(不得抹空) ─────────────────────────────────
def test_write_carries_a_provisioned_secret_when_the_binding_has_none(home):
    """2026-10-01 owner 裁决(方案A/B): 绑定缺 secret ⇒ **就地补一把并携带**到网关
    (单一真源=绑定 ⇒ 网关采纳同一把 ⇒ D2B 两侧摘要同源, 修"两把钥匙")。

    ⚠ 旧断言 `writes == []`(一条写都不发)与该裁决**正面冲突** ⇒ 按新契约改写并换名;
    **深层意图原样保留**: 任何写都必须携带**非空 secret**, 永不 url-only PUT
    (那会把云端 secret 抹成 NULL ⇒ 永久 401; 见文件头病灶链)。
    依据: owner 2026-10-01「A 和 B 不矛盾, 都可以要」+「缺则补一把并 PUT 同步」。

    修复前: 走到 register_agent_email 的 exists 分支 ⇒ PUT body 无 secret 键 ⇒
    网关把云端已存 secret 覆写成 NULL ⇒ 永久 401。"""
    bp = _write_binding(home, secret="")

    core.register_agent_email(_FakeClient(), SID, EMAIL, webhook_url=LOCAL_URL,
                              manager_address=MGR)

    assert len(_FakeClient.writes) == 1, "exactly one write, carrying a provisioned secret"
    _did, _url, _sec = _FakeClient.writes[0]
    assert _url == LOCAL_URL, "carried url must be the caller's"
    assert _sec and len(_sec) == 64, "every write must carry a non-empty secret (never url-only)"
    assert json.loads(bp.read_text()).get("webhook_secret") == _sec, \
        "binding and the write must share ONE secret (single source: binding)"


# ── ④ 注册链: 调用方省略 secret ⇒ 从绑定补全 ─────────────────────────────────
def test_register_chain_falls_back_to_the_binding_secret(home):
    _write_binding(home)

    core.register_agent_email(_FakeClient(), SID, EMAIL, webhook_url=BRIDGE_URL,
                              manager_address=MGR)

    assert _FakeClient.writes == [("7", BRIDGE_URL, SECRET)]


# ── ⑤ 注册链: 调用方省略 + 绑定也没有 ⇒ 不写 ────────────────────────────────
def test_register_chain_never_issues_a_secretless_write(home):
    assert _FakeClient.writes == []

    core.register_agent_email(_FakeClient(), SID, "ghost@gw.test", webhook_url=BRIDGE_URL,
                              manager_address=MGR)

    assert _FakeClient.writes == [], "no binding secret ⇒ leave the cloud row untouched"


# ── ⑥ 基线不变式: 两个值都给 ⇒ 原样成对写(无行为变化) ──────────────────────────
def test_register_chain_still_writes_the_pair_verbatim(home):
    core.register_agent_email(_FakeClient(), SID, EMAIL,
                              webhook_url=BRIDGE_URL, webhook_secret="x" * 64,
                              manager_address=MGR)
    assert _FakeClient.writes == [("7", BRIDGE_URL, "x" * 64)]

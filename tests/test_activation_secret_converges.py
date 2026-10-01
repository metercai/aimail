"""2026-10-01 owner 方案A(激活站): 激活本地生成的 webhook_secret 必须与网关对齐。

病灶(实测 L2 原文): 注册侧 sha256=31ad2cfe5140 vs 本地绑定 sha256=e6b42e73e32b —— 第 10 缺陷。
机制: `activate-address-code` 的 body 只有 {code, email_address}(aimail_tools.py:429)，网关
自己签发一把；`activate_address_code_persist` 在 **其后** 本地生成另一把(:516-518)且**从不回传**
⇒ 两把永久不等。B(`228ebaa`) 修的是 register 路径，本流程不经过它 ⇒ 实测仍不等。
本测试: 网关侧预置一把不同的 secret ⇒ 断言激活收尾必须把它对齐成**绑定这一把**(单一真源=绑定)。
"""
import logging
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _d in (ROOT / "pysdk",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import aimail_tools  # noqa: E402

_REAL = aimail_tools._GatewayClient  # 导入期抓真类(autouse fixture 会顶掉模块名)

EMAIL = "agent.converge@demo.test"
GATEWAY_OLD_SECRET = "a" * 64


class _FakeGateway:
    """网关替身: 已持有一把**不同**的 secret(预置), 记录所有 update 写入。"""
    writes: list = []

    def __init__(self, url="", key=""):
        self.url, self.key = url, key

    def list_system_domains(self, sid):
        return [{"id": "9", "domain": EMAIL}]

    def update_system_domain(self, domain_id, webhook_url="", webhook_secret=""):
        _FakeGateway.writes.append((domain_id, webhook_url, webhook_secret))
        return {"status": 200}


def _real_host():
    """真 `_GatewayClient` 实例(:74, 被测方法就挂在它身上), 实例级遮蔽激活调用。"""
    h = _REAL.__new__(_REAL)          # 跳过 __init__(签名与被测逻辑无关)
    h.gateway_url = "https://gw.test"
    h.activate_address_code = lambda code, ea: {
        "success": True,
        "system_id": "sys-1",
        "email_address": (ea or "").strip().lower(),
        "raw_key": "k" * 64,
        "expires_at": ""}
    return h


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    _FakeGateway.writes = []
    monkeypatch.setattr(aimail_tools, "_GatewayClient", _FakeGateway)


def _load_binding(home):
    hits = list(pathlib.Path(home).glob("**/agentmail.json"))
    assert hits, "binding (agentmail.json) must be persisted under tmp HOME"
    import json
    return json.loads(hits[0].read_text())


def test_activation_converges_gateway_secret_to_the_binding_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    _real_host().activate_address_code_persist("CODE-1", EMAIL)

    binding = _load_binding(tmp_path)
    local = str(binding.get("webhook_secret") or "")
    assert local and len(local) == 64, "binding must carry a 64-hex local secret"

    # 网关那把(预置的不同值)必须被对齐成绑定这一把: 恰好一次写、带非空 secret、值相等。
    assert len(_FakeGateway.writes) == 1, (
        "expected exactly one gateway sync write, got %r" % (_FakeGateway.writes,))
    _did, _url, _gwsec = _FakeGateway.writes[0]
    assert _gwsec and len(_gwsec) == 64, "sync write must carry a non-empty secret (never url-only)"
    assert _gwsec == local, (
        "gateway must adopt THE binding secret (single source); "
        "gateway=%r binding=%r" % ((_gwsec or "")[:12], local[:12]))


def test_activation_keeps_an_existing_binding_secret_untouched(tmp_path, monkeypatch):
    """已有 secret ⇒ 复用不覆盖(不变), 但仍要把对齐动作做完(幂等收敛)。"""
    monkeypatch.setenv("HOME", str(tmp_path))

    h = _real_host()
    # 第一次跑: 生成并落盘
    h.activate_address_code_persist("CODE-1", EMAIL)
    first = _load_binding(tmp_path)["webhook_secret"]
    _FakeGateway.writes.clear()

    # 第二次跑(同地址): existing ⇒ 复用同一把
    h.activate_address_code_persist("CODE-1", EMAIL)
    second = _load_binding(tmp_path)["webhook_secret"]
    assert second == first, "existing binding secret must be reused (no churn)"
    if _FakeGateway.writes:
        assert _FakeGateway.writes[-1][2] == first, "any sync must carry that same secret"


# ── F1(2026-10-02 卡A 收尾): 看不见行 ⇒ 不再把激活打死 ──────────────────────
class _FakeGatewayNoRow(_FakeGateway):
    """纯地址级激活现场: 该地址在网关**没有** system_domains 行
    (gw 日志只有 domain_created + address_code_activated) ⇒ list 回 [];
    `_request` 可被设成 401/403 以复现 agent-scope key 被 http.rs:744-747 拒之门外。"""

    status = 0

    def list_system_domains(self, sid):
        return []

    def _request(self, method, path):
        return {"status": _FakeGatewayNoRow.status}


def test_activation_continues_when_the_gateway_has_no_visible_row(tmp_path, monkeypatch):
    """F1 RED/GREEN: 修复前这里 raise ⇒ **地址级激活整体失败** ⇒ E2-pull 连红 7 轮。"""
    monkeypatch.setattr(aimail_tools, "_GatewayClient", _FakeGatewayNoRow)
    monkeypatch.setenv("HOME", str(tmp_path))
    _FakeGatewayNoRow.status = 0

    res = _real_host().activate_address_code_persist("CODE-1", EMAIL)

    assert res.get("success") is True, res
    binding = _load_binding(tmp_path)
    assert len(str(binding.get("webhook_secret") or "")) == 64, \
        "binding must still be persisted even when the gateway side cannot be aligned"
    assert _FakeGateway.writes == [], "nothing to write without a visible row"


def test_activation_continues_and_calls_out_the_scope(tmp_path, monkeypatch, caplog):
    """F1: 403 ⇒ 不抛, 且告警必须点名 scope(响亮 ≠ 静默)。"""
    monkeypatch.setattr(aimail_tools, "_GatewayClient", _FakeGatewayNoRow)
    monkeypatch.setenv("HOME", str(tmp_path))
    _FakeGatewayNoRow.status = 403

    with caplog.at_level(logging.WARNING):
        res = _real_host().activate_address_code_persist("CODE-1", EMAIL)

    assert res.get("success") is True, res
    assert "insufficient scope" in caplog.text, caplog.text[-800:]


def test_activation_still_fails_loudly_when_the_visible_row_cannot_be_written(
        tmp_path, monkeypatch):
    """卡A 原意不动: 行可见但 PUT 坏了 ⇒ 必须 raise(绝不留两把钥匙)。"""
    monkeypatch.setenv("HOME", str(tmp_path))

    class _Broken(_FakeGateway):
        def update_system_domain(self, *a, **k):
            raise OSError("wire down")

    monkeypatch.setattr(aimail_tools, "_GatewayClient", _Broken)

    with pytest.raises(RuntimeError, match="activation secret sync to gateway failed"):
        _real_host().activate_address_code_persist("CODE-1", EMAIL)

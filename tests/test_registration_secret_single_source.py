"""第 10 缺陷(两把钥匙)的**重注册**分支: 绑定是唯一真源(2026-10-02 E1+E2)。

定因(2026-10-02, 只读取证 + 三轮复现 /tmp/d10-repro.py):
  一次 `aimail install` 对 deerflow 会注册 **3 次**
  (`cli/platforms.json` install_steps = sdk_install → register_default → register_all),
  而 `pysdk/deer-flow/manage.py` 每次都 `secrets.token_hex(32)` **新铸一把**;
  `register_agent_email` 的 exists 分支原为"参数优先" ⇒ 新值 PUT 覆写网关
  (`storage.rs:693` 全量覆写), 绑定却只在拿到 api_key/activation_code 时才落盘
  (`manage.py:224-235`) ⇒ **网关=第 3 把 / 绑定=第 1 把** ⇒ D2B 两侧摘要不等。
  历史读数: 13 红全在 deerflow, 其余四平台 56 绿。

修法(两层, 不矛盾):
  E1 核心 `aimail_base.register_agent_email` exists 分支: **绑定优先**, 调用方的值只在
     绑定缺 secret 时采用; 绑定文件在但缺 secret ⇒ 先回写绑定再 PUT(真源补齐)。
  E2 调用方 `pysdk/deer-flow/manage.py`: 有绑定就复用, 只在缺失时新铸(不再制造新值)。

与卡B(`228ebaa`)六条契约的关系: ①④绑定值照写 ③两边都没有⇒现补 ⑤无绑定⇒不写
⑥无绑定+参数⇒原样写 —— 本文件只新增"绑定有值 + 调用方另给一把"这一象限。
"""
import importlib.util
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
BINDING_SECRET = "b" * 64
CALLER_SECRET = "c" * 64
URL = f"http://127.0.0.1:39100{_c.INBOUND_PATH}"
MGR = "m@d.tm"


@pytest.fixture(autouse=True)
def _writes(monkeypatch):
    """记录注册链发出的每一次写 —— 本文件唯一可观测量。"""
    _FakeClient.writes = []
    _FakeClient.registers = []
    monkeypatch.setattr(aimail_tools, "_GatewayClient", _FakeClient)
    return _FakeClient.writes


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    return tmp_path


class _FakeClient:
    """网关语义: 首次 register 收下 secret; 再注册回 "already exists"; PUT 全量覆写。"""

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
        # 域列表按本轮注册的地址回(网关按 email 找行 —— 定因测试里地址是派生出来的)
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


# ── E1: 绑定有值 ⇒ 调用方另给一把也不许改写网关 ────────────────────────────
def test_reregistration_keeps_the_binding_secret(home):
    """RED 先证: 修复前 PUT 带的是 CALLER_SECRET ⇒ 网关/绑定两把钥匙。"""
    _write_binding(home, secret=BINDING_SECRET)

    core.register_agent_email(_FakeClient(), SID, EMAIL, webhook_url=URL,
                              webhook_secret=CALLER_SECRET, manager_address=MGR)

    assert _FakeClient.writes == [("7", URL, BINDING_SECRET)], (
        "binding is the single source — a fresh caller value must not overwrite the "
        "gateway (that is exactly the deerflow install re-registration divergence)")


# ── E1 回填: 绑定文件在但缺 secret ⇒ 调用方的值先落绑定再 PUT ───────────────
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


# ── E2: deerflow 重注册不再新铸 —— 调用方复用绑定里的那把 ───────────────────
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

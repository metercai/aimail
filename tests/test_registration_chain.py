"""注册链单元测试:register_agent_email 四步(register→exists 更新→activate)。

fake client 替代 _GatewayClient(无网络),锁定:
1) 新地址(created)→ activate_address 拿 api_key;
2) 已存在(exists)→ 走 list_system_domains/update_system_domain 幂等更新;
3) 参数透传(generate_code=True/webhook/manager);
4) 失败非 exists → RuntimeError。

发布前自动回归面:SDK 注册链语义被破坏即 L0 红,不许带问题上线。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pysdk"))

import aimail_base  # noqa: E402


class FakeClient:
    def __init__(self):
        self.calls = []
        self.register_result = {"status": "created", "activation_code": "code-1"}
        self.domains = []
        self.activate_result = {"success": True, "raw_key": "k" * 64}

    def register_email(self, **kw):
        self.calls.append(("register_email", kw))
        return self.register_result

    def list_system_domains(self, system_id):
        self.calls.append(("list_system_domains", {"system_id": system_id}))
        return self.domains

    def update_system_domain(self, domain_id, webhook_url, webhook_secret):
        self.calls.append(("update_system_domain", {"id": domain_id, "url": webhook_url,
                                                    "secret": webhook_secret}))

    def activate_address(self, code, **kw):
        self.calls.append(("activate_address", {"code": code, **kw}))
        return self.activate_result


def test_new_registration_created_path():
    c = FakeClient()
    out = aimail_base.register_agent_email(
        c, "sys-1", "agent.a@d.tm",
        webhook_url="http://127.0.0.1:9/in", webhook_secret="s3",
        manager_address="m@d.tm")
    # 四步:register_email(generate_code) → activate_address
    kinds = [x[0] for x in c.calls]
    assert kinds[0] == "register_email" and kinds[-1] == "activate_address"
    reg = c.calls[0][1]
    assert reg["generate_code"] is True
    assert reg["email"] == "agent.a@d.tm"
    assert reg["webhook_url"] == "http://127.0.0.1:9/in"
    assert reg["webhook_secret"] == "s3"
    assert reg["manager_address"] == "m@d.tm"
    assert out["api_key"] == "k" * 64
    assert out["activation_code"] == "code-1"


def test_exists_path_updates_webhook():
    c = FakeClient()
    c.register_result = {"status": "409", "error": "address already exists"}
    # 域列表按网关结构:domain 项含域名;当前匹配语义 domain==email 时更新
    c.domains = [{"id": "dom-9", "domain": "agent.a@d.tm"}]
    out = aimail_base.register_agent_email(c, "sys-1", "agent.a@d.tm",
                                           webhook_url="http://x/in", webhook_secret="n")
    kinds = [x[0] for x in c.calls]
    assert "update_system_domain" in kinds
    upd = next(x[1] for x in c.calls if x[0] == "update_system_domain")
    assert upd["id"] == "dom-9"
    assert upd["url"] == "http://x/in"
    # exists 路径不激活 → api_key 空
    assert out["api_key"] == ""
    assert out["activation_code"] == ""


def test_non_exists_error_raises():
    c = FakeClient()
    c.register_result = {"status": "500", "error": "boom"}
    try:
        aimail_base.register_agent_email(c, "sys-1", "agent.a@d.tm")
    except RuntimeError as e:
        assert "register failed" in str(e)
        return
    raise AssertionError("expected RuntimeError")


def test_activate_failure_returns_empty_api_key():
    c = FakeClient()
    c.activate_result = {"success": False}
    out = aimail_base.register_agent_email(c, "sys-1", "agent.a@d.tm")
    assert out["api_key"] == ""
    assert out["activation_code"] == "code-1"

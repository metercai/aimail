"""裸 pysdk（未注入平台指针）的降级契约。

契约：定位不到网关配置时，入站预处理必须**降级而不是整条失败** —— 邮件照常
处理（角色 prompt 照样注入、继续投递给 agent），只丢掉网关相关富化（B1 联系
画像、附件下载）。真正的配置问题由适配层启动自检 check_profile_pointer() 报出，
所以降级不会静默。
"""
import json

import aimail_base as ab

AGENT_EMAIL = "agent1@token.tm"
SYSTEM_ID = "system-test"


def _bare(tmp_path, monkeypatch):
    """沙箱：AIMAIL_HOME=tmp + 注入 agent config，但**不**提供 .agentmail 指针。"""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    monkeypatch.setattr(ab, "_CONFIG_LOADER", lambda: {"email": AGENT_EMAIL, "system_id": SYSTEM_ID})
    monkeypatch.setattr(ab, "_PROFILE_DIR_RESOLVER", None)
    for key in (
        "AIMAIL_GATEWAY_URL",
        "AIMAIL_ADMIN_KEY",
        "AIMAIL_PRODUCT_CODE",
        "AIMAIL_BRIDGE_URL",
        "AIMAIL_WEBHOOK_HOST",
    ):
        monkeypatch.delenv(key, raising=False)


def test_gateway_config_unresolvable_returns_none(tmp_path, monkeypatch):
    _bare(tmp_path, monkeypatch)

    assert ab._load_gateway_config() is None


def test_inbound_preprocessing_degrades_but_still_processes(tmp_path, monkeypatch):
    _bare(tmp_path, monkeypatch)
    role_dir = tmp_path / "systems" / SYSTEM_ID / "board" / "role_prompt"
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "whoami.md").write_text("asker: {{INQUIRY_SENDER}}", encoding="utf-8")

    # 关键：不抛 RuntimeError（此前 B1 步骤无参调配置解析会直接炸掉整条入站）
    r = ab.preprocess_mail_payload(
        {
            "mail_id": "m-1",
            "message_id": "<mid-1@token.tm>",
            "subject": "[WHOAMI] who are you",
            "body": "who are you",
            "to": [AGENT_EMAIL],
            "from": "boss@corp.com",
        },
        {},
    )

    assert r is not None
    assert r["_whoami_prompt"] == "asker: boss@corp.com"
    assert "_preprocess_error" not in r


def test_check_profile_pointer_flags_missing_then_ok(tmp_path, monkeypatch):
    _bare(tmp_path, monkeypatch)
    assert ab.check_profile_pointer() is False

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / ".agentmail").write_text(
        json.dumps({"system_id": SYSTEM_ID, "email": AGENT_EMAIL}), encoding="utf-8"
    )
    monkeypatch.setattr(ab, "_PROFILE_DIR_RESOLVER", lambda: str(profile_dir))

    assert ab.check_profile_pointer() is True


def test_check_profile_pointer_tolerates_broken_resolver(tmp_path, monkeypatch):
    """自检本身绝不抛：解析器抛异常/指针损坏都按"未安装"处理。"""
    _bare(tmp_path, monkeypatch)

    def _boom():
        raise OSError("no home")

    monkeypatch.setattr(ab, "_PROFILE_DIR_RESOLVER", _boom)
    assert ab.check_profile_pointer() is False

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / ".agentmail").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(ab, "_PROFILE_DIR_RESOLVER", lambda: str(profile_dir))
    assert ab.check_profile_pointer() is False

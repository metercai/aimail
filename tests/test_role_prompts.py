"""whoami / role_calibrator 角色注入契约(Python 侧,与 tssdk preprocess.test.ts 同型)。

覆盖两条入口各自的角色 prompt 注入:
  - 主题以 [WHOAMI] 开头(大小写不敏感) → _whoami_prompt(熟人身份卡片问询)
  - 主题含 "update persona"            → _role_prompt(role_calibrator 角色校准)
两者都是 early-return:注入后立即返回,不会被后续 board role prompt 覆盖。
"""
import json

import aimail_base as ab

AGENT_EMAIL = "agent1@token.tm"
SYSTEM_ID = "system-test"


def _mail(subject: str) -> dict:
    return {
        "mail_id": "m-1",
        "message_id": "<mid-1@token.tm>",
        "subject": subject,
        "body": "please identify yourself",
        "to": [AGENT_EMAIL],
        "from": "boss@corp.com",
    }


def _role_prompt_dir(tmp_path, monkeypatch):
    """沙箱: AIMAIL_HOME 指向 tmp,注入 agent config 与 profile 指针(均适配层同一注入点)。"""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    cfg = {"email": AGENT_EMAIL, "system_id": SYSTEM_ID}
    monkeypatch.setattr(ab, "_CONFIG_LOADER", lambda: cfg)
    # B1 步骤按平台契约经 .agentmail 指针定位 system_id(preprocess 里不带参数调用),
    # 缺指针会直接抛错 —— 所以沙箱必须提供指针,而不是绕过它。
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / ".agentmail").write_text(
        json.dumps({"system_id": SYSTEM_ID, "email": AGENT_EMAIL}), encoding="utf-8"
    )
    monkeypatch.setattr(ab, "_PROFILE_DIR_RESOLVER", lambda: str(profile_dir))
    role_dir = tmp_path / "systems" / SYSTEM_ID / "board" / "role_prompt"
    role_dir.mkdir(parents=True, exist_ok=True)
    return role_dir


def test_whoami_subject_injects_rendered_prompt(tmp_path, monkeypatch):
    role_dir = _role_prompt_dir(tmp_path, monkeypatch)
    (role_dir / "whoami.md").write_text(
        "Identity for {{AGENTMAIL_ADDRESS}}; asker: {{INQUIRY_SENDER}}; subject: {{INQUIRY_SUBJECT}}",
        encoding="utf-8",
    )

    r = ab.preprocess_mail_payload(_mail("[WHOAMI] who are you"), {})

    assert r["_whoami_prompt"] == (
        f"Identity for {AGENT_EMAIL}; asker: boss@corp.com; subject: [WHOAMI] who are you"
    )
    # early-return: board role prompt 不得覆盖,也未建立 a2a 会话
    assert "_role_prompt" not in r
    assert "_a2a_session_key" not in r


def test_whoami_marker_is_case_insensitive(tmp_path, monkeypatch):
    role_dir = _role_prompt_dir(tmp_path, monkeypatch)
    (role_dir / "whoami.md").write_text("asker: {{INQUIRY_SENDER}}", encoding="utf-8")

    r = ab.preprocess_mail_payload(_mail("[whoami] lower"), {})

    assert r["_whoami_prompt"] == "asker: boss@corp.com"


def test_update_persona_subject_injects_role_calibrator_prompt(tmp_path, monkeypatch):
    role_dir = _role_prompt_dir(tmp_path, monkeypatch)
    (role_dir / "role_calibrator.md").write_text(
        "Draft a persona for {{AGENTMAIL_ADDRESS}}; inquiry: {{INQUIRY_SUBJECT}}", encoding="utf-8"
    )

    r = ab.preprocess_mail_payload(_mail("Please update persona for support"), {})

    assert r["_role_prompt"] == (
        f"Draft a persona for {AGENT_EMAIL}; inquiry: Please update persona for support"
    )
    assert "_whoami_prompt" not in r


def test_missing_role_file_does_not_fake_a_prompt(tmp_path, monkeypatch):
    _role_prompt_dir(tmp_path, monkeypatch)  # 角色文件目录存在但文件缺失

    r = ab.preprocess_mail_payload(_mail("[WHOAMI] nobody home"), {})

    assert "_whoami_prompt" not in r
    assert "_role_prompt" not in r

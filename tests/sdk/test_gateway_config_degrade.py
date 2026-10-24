"""Degradation contract for bare pysdk (no platform pointer injected).

Contract: when the gateway configuration cannot be located, inbound preprocessing must **degrade rather than fail entirely** —— email is processed as usual
processed (role prompt is still injected, continues to be delivered to the agent), only gateway-related enrichment is dropped (B1 contact
profile, attachment download). Real config issues are reported by adapter layer startup self-check check_profile_pointer(),
So degradation will not be silent.
"""
import json

import aimail_base as ab

AGENT_EMAIL = "agent1@token.tm"
SYSTEM_ID = "system-test"


def _bare(tmp_path, monkeypatch):
    """Sandbox: AIMAIL_HOME=tmp + inject agent config, but **do not** provide .agentmail pointer."""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    monkeypatch.setattr(ab, "_CONFIG_LOADER", lambda: {"email": AGENT_EMAIL, "system_id": SYSTEM_ID})
    monkeypatch.setattr(ab, "_PROFILE_DIR_RESOLVER", None)
    for key in (
        "AIMAIL_GATEWAY_URL",
        "AIMAIL_ADMIN_KEY",
        "AIMAIL_PRODUCT_CODE",
        "AIMAIL_BRIDGE_URL",
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

    # Key: do not raise RuntimeError (previously B1 step calling config parser without args would directly blow up the entire inbound)
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
    """The self-check itself never raises: a parser exception / corrupt pointer is all treated as "not installed"."""
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

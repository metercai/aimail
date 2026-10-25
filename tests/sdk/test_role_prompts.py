"""whoami / role_calibrator role injection contract (Python side, isomorphic to tssdk preprocess.test.ts).

Covers role prompt injection for each of the two entry points:
  - Subject starts with [WHOAMI] (case-insensitive) → _whoami_prompt (familiar identity card inquiry)
  - Subject contains "update persona"            → _role_prompt(role_calibrator role calibration)
Both are early-return: return immediately after injection, will not be overridden by subsequent board role prompt.
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
    """Sandbox: AIMAIL_HOME points to tmp, inject agent config and profile pointer (both at the same injection point in the adaptation layer)."""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    cfg = {"email": AGENT_EMAIL, "system_id": SYSTEM_ID}
    monkeypatch.setattr(ab, "_CONFIG_LOADER", lambda: cfg)
    # B1 step locates system_id via the .agentmail pointer per platform contract (called without arguments in preprocess),
    # missing pointer raises error directly —— so sandbox must provide pointer, not bypass it.
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
    # early-return: board role prompt must not overwrite, and no a2a session established
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


def test_welcome_marker_plus_labels_injects_role_calibrator(tmp_path, monkeypatch):
    """After 2026-09-22 merge: welcome email = **subject marker + body three tags hit simultaneously** ⇒ inject Role_Calibrator.

    Gateway-generated subject: "Welcome to AIMail World, {agent}, since {date}!" (personalized suffix does not affect recognition).
    """
    role_dir = _role_prompt_dir(tmp_path, monkeypatch)
    (role_dir / "role_calibrator.md").write_text(
        "Draft a persona for {{agent_email}}", encoding="utf-8"
    )
    m = _mail("Welcome to AIMail World, agent1, since 2026-09-22!")
    m["body"] = (
        "To verify that the full delivery path works end to end, please reply-all with:\n"
        "  persona: <who you are>\n"
        "  signature: <your signature>\n"
        "  current_time: <2026-09-22 21:05 UTC>\n"
    )
    r = ab.preprocess_mail_payload(m, {})
    assert r is not None and r.get("_role_prompt"), "welcome email must inject Role_Calibrator"


def test_welcome_marker_without_labels_uses_default_prompt(tmp_path, monkeypatch):
    """Only subject marker hit, missing body three tags ⇒ **do not** inject Role_Calibrator (fall back to default prompt, not silent)."""
    role_dir = _role_prompt_dir(tmp_path, monkeypatch)
    (role_dir / "role_calibrator.md").write_text(
        "Draft a persona for {{agent_email}}", encoding="utf-8"
    )
    m = _mail("Welcome to AIMail World, agent1, since 2026-09-22!")
    m["body"] = "no labels here"
    r = ab.preprocess_mail_payload(m, {})
    assert r is not None and not r.get("_role_prompt")


def test_missing_role_file_does_not_fake_a_prompt(tmp_path, monkeypatch):
    _role_prompt_dir(tmp_path, monkeypatch)  # role file directory exists but file missing

    r = ab.preprocess_mail_payload(_mail("[WHOAMI] nobody home"), {})

    assert "_whoami_prompt" not in r
    assert "_role_prompt" not in r


# ── common.md channel gating (defect 2, ruling 2026-10-25) ───────────────────
# Channel 1 (a2a board) keeps the common.md fallback; channels 2/3/4
# (X-AIMail-Prompt header / local prompt_rules / whoami+Role_Calibrator)
# must NOT fall back to common.md — missing file ⇒ ERROR log + no injection.

def test_board_channel_missing_role_uses_common_md(tmp_path, monkeypatch):
    """Channel 1 (board) KEPT: named role missing ⇒ common.md is used (behavior unchanged)."""
    role_dir = _role_prompt_dir(tmp_path, monkeypatch)
    (role_dir / "common.md").write_text("COMMON for {{BOARD_ROLE}}", encoding="utf-8")

    m = _mail("task update")
    m["board_id"] = "B-2"
    m["board_role"] = "missing-role"
    r = ab.preprocess_mail_payload(m, {})

    assert r.get("_role_prompt") == "COMMON for missing-role"
    assert r.get("_a2a_session_key") == "a2a:B-2:boss@corp.com"


def test_whoami_channel_missing_role_no_common_no_inject(tmp_path, monkeypatch, caplog):
    """Channel 4 (whoami): common.md must NOT be absorbed — no _whoami_prompt + ERROR log."""
    role_dir = _role_prompt_dir(tmp_path, monkeypatch)
    (role_dir / "common.md").write_text("COMMON-MARKER", encoding="utf-8")

    with caplog.at_level("ERROR", logger="aimail_base"):
        r = ab.preprocess_mail_payload(_mail("[WHOAMI] nobody home"), {})

    assert "_whoami_prompt" not in r
    assert "_role_prompt" not in r
    errs = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert any("role file missing" in rec.getMessage() and "whoami" in rec.getMessage() for rec in errs), \
        f"expected an ERROR naming the missing role: {[r.getMessage() for r in errs]}"


def test_role_calibrator_channel_missing_role_no_common_no_inject(tmp_path, monkeypatch, caplog):
    """Channel 4 (Role_Calibrator): missing file ⇒ no _role_prompt + ERROR (drift made visible, not masked)."""
    role_dir = _role_prompt_dir(tmp_path, monkeypatch)
    (role_dir / "common.md").write_text("COMMON-MARKER", encoding="utf-8")

    with caplog.at_level("ERROR", logger="aimail_base"):
        r = ab.preprocess_mail_payload(_mail("Please update persona for support"), {})

    assert "_role_prompt" not in r
    assert any("role file missing" in rec.getMessage() and "role_calibrator" in rec.getMessage()
               for rec in caplog.records if rec.levelname == "ERROR")


def test_header_channel_missing_role_no_common_falls_through(tmp_path, monkeypatch, caplog):
    """Channel 2 (X-AIMail-Prompt header): missing file ⇒ no _role_prompt + ERROR + chain continues to L5 (none)."""
    role_dir = _role_prompt_dir(tmp_path, monkeypatch)
    (role_dir / "common.md").write_text("COMMON-MARKER", encoding="utf-8")

    m = _mail("a plain question")
    with caplog.at_level("ERROR", logger="aimail_base"):
        r = ab.preprocess_mail_payload(m, {"X-AIMail-Prompt": "missinghdr"})

    assert "_role_prompt" not in r
    assert any("role file missing" in rec.getMessage() and "missinghdr" in rec.getMessage()
               for rec in caplog.records if rec.levelname == "ERROR")


def test_prompt_rules_channel_missing_role_no_common_next_rule(tmp_path, monkeypatch, caplog):
    """Channel 3 (local prompt_rules): rule hits but its file is missing ⇒ no injection, next rule (none) ⇒ no _role_prompt."""
    role_dir = _role_prompt_dir(tmp_path, monkeypatch)
    (role_dir / "common.md").write_text("COMMON-MARKER", encoding="utf-8")
    cfg = {
        "email": AGENT_EMAIL,
        "system_id": SYSTEM_ID,
        "prompt_rules": [
            {"name": "10_missing", "file": "ghostfile", "subject": ["hello"]},
        ],
    }
    monkeypatch.setattr(ab, "_CONFIG_LOADER", lambda: cfg)

    m = _mail("hello there")
    with caplog.at_level("ERROR", logger="aimail_base"):
        r = ab.preprocess_mail_payload(m, {})

    assert "_role_prompt" not in r
    assert any("role file missing" in rec.getMessage() and "ghostfile" in rec.getMessage()
               for rec in caplog.records if rec.levelname == "ERROR")


def test_build_ctx_toolset_list_empty_without_provider():
    """No host provider ⇒ TOOLSET_LIST is empty. No fallback to the AGENT_TOOL_NAMES
    constant (2026-10-25 ruling: host actual state only, empty when undetermined)."""
    monkeypatch_none_toolset()
    ctx = ab.build_ctx({}, {})
    assert ctx["TOOLSET_LIST"] == ""


def test_build_ctx_toolset_list_from_host_provider():
    """Host provider is the single source of the value: a registered subset is
    reflected verbatim (proves the value is NOT the static constant)."""
    import aimail_agent as A
    subset = list(A.AGENT_TOOL_NAMES[:3])
    ab._TOOLSET_PROVIDER = lambda config: subset
    try:
        ctx = ab.build_ctx({}, {})
    finally:
        ab._TOOLSET_PROVIDER = None
    assert ctx["TOOLSET_LIST"] == ", ".join(subset)


def test_build_ctx_toolset_list_empty_when_host_says_unregistered():
    """Toolset not registered on this host ⇒ provider returns [] ⇒ empty (no constant fallback)."""
    ab._TOOLSET_PROVIDER = lambda config: []
    try:
        ctx = ab.build_ctx({}, {})
    finally:
        ab._TOOLSET_PROVIDER = None
    assert ctx["TOOLSET_LIST"] == ""


def monkeypatch_none_toolset():
    """Ensure no toolset provider leaks in from other tests."""
    ab._TOOLSET_PROVIDER = None

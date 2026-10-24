"""prompt_rules contract (Python side, isomorphic to tssdk prompt-rules.test.ts).

Ruling 2026-09-23:
  ① Fields=subject/body/sender/recipient contain match (case-insensitive);
     Multiple keywords within a field=OR, between fields=AND
  ② name={10-99 sequence}_{filename}; actual loading depends on rule["file"]
  ③ Chain order fixed: [WHOAMI] > welcome > board > X-AIMail-Prompt header >
     Local rules (name alphabetical order, stop at first match); matched but file missing (including common fallback also absent)
     ⇒ WARN continue; no layer blocks delivery
  ④ Key prompt_rules (stored in agentmail.json, only for that agent)
  ⑦ header is a gateway extension point: dual source payload.headers / HTTP header parameter, case-insensitive
"""
import json
import logging

import aimail_base as ab

AGENT_EMAIL = "agent1@token.tm"
SYSTEM_ID = "system-test"


def _mail(subject="quarterly review", body="please review the numbers", **over):
    p = {
        "mail_id": "m-1",
        "message_id": "<mid-1@token.tm>",
        "subject": subject,
        "body": body,
        "to": [AGENT_EMAIL],
        "from": "boss@corp.com",
    }
    p.update(over)
    return p


def _sandbox(tmp_path, monkeypatch, rules=None):
    """Sandbox: AIMAIL_HOME→tmp, inject agent config (including prompt_rules) and profile pointer."""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    cfg = {"email": AGENT_EMAIL, "system_id": SYSTEM_ID}
    if rules is not None:
        cfg["prompt_rules"] = rules
    monkeypatch.setattr(ab, "_CONFIG_LOADER", lambda: cfg)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / ".agentmail").write_text(
        json.dumps({"system_id": SYSTEM_ID, "email": AGENT_EMAIL}), encoding="utf-8"
    )
    monkeypatch.setattr(ab, "_PROFILE_DIR_RESOLVER", lambda: str(profile_dir))
    role_dir = tmp_path / "systems" / SYSTEM_ID / "board" / "role_prompt"
    role_dir.mkdir(parents=True, exist_ok=True)
    return role_dir


# ── ① Matching matrix: OR within field / AND between fields / case / absence and bad type ──────────

def test_field_or_within_and_across_fields():
    rule = {"name": "10_a", "file": "a",
            "subject": ["[incident]", "告警"], "sender": ["ops@x.com"]}
    assert ab.prompt_rule_matches(rule, "[INCIDENT] cluster down", "", "ops@x.com", "")
    assert ab.prompt_rule_matches(rule, "集群有告警", "", "OPS@X.COM", "")   # 字段内 or + 大小写
    assert not ab.prompt_rule_matches(rule, "plain mail", "", "ops@x.com", "")   # subject not hit
    assert not ab.prompt_rule_matches(rule, "[incident]", "", "boss@corp.com", "")  # and between fields


def test_absent_field_skips_empty_list_skips_bad_type_fails():
    absent = {"name": "10_a", "file": "a", "sender": ["boss@"]}
    assert ab.prompt_rule_matches(absent, "anything at all", "", "boss@corp.com", "")
    empty_ok = {"name": "10_a", "file": "a", "subject": [], "sender": ["boss@"]}
    assert ab.prompt_rule_matches(empty_ok, "anything", "", "boss@corp.com", "")  # empty table=not provided
    bad_type = {"name": "10_a", "file": "a", "subject": {"k": 1}}
    assert not ab.prompt_rule_matches(bad_type, "x", "", "", "")                  # bad type=no hit


# ── loader: filter bad items + name alphabetical order (ruling ②③) ────────────────────────

def test_loader_filters_bad_items_and_orders_by_name(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, rules=[
        {"name": "12_incident", "file": "incident", "subject": ["x"]},
        {"name": "10_audit", "file": "audit", "body": ["a"]},
        {"name": "09_bad_serial", "file": "bad", "subject": ["x"]},   # serial <10
        {"name": "13_nofile", "subject": ["x"]},                      # missing file
        {"name": "14_off", "file": "off", "subject": ["x"], "enabled": False},
        {"name": "15_nofield", "file": "nf"},                         # no fields at all
        {"name": "16_badtype", "file": "bt", "subject": 123},         # field bad type
        "not-an-object",
    ])
    assert [r["name"] for r in ab.read_prompt_rules()] == ["10_audit", "12_incident"]


def test_loader_no_config_or_no_key_is_empty_not_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "_CONFIG_LOADER", None)
    assert ab.read_prompt_rules() == []
    _sandbox(tmp_path, monkeypatch, rules=None)  # cfg has no prompt_rules key
    assert ab.read_prompt_rules() == []


# ── Chain order integration (③⑦): header dual source / board suppression / stop at first hit / continue on missing file ──

def test_header_layer_wins_over_local_rules(tmp_path, monkeypatch):
    role_dir = _sandbox(tmp_path, monkeypatch, rules=[
        {"name": "10_local", "file": "local_role", "subject": ["quarterly"]}])
    (role_dir / "hdr_role.md").write_text("HDR {{INQUIRY_SUBJECT}}", encoding="utf-8")
    (role_dir / "local_role.md").write_text("LOCAL", encoding="utf-8")

    r = ab.preprocess_mail_payload(_mail(), {"X-AIMail-Prompt": "hdr_role"})
    assert r["_role_prompt"] == "HDR quarterly review"   # L4 > L5


def test_header_also_read_from_payload_headers(tmp_path, monkeypatch):
    role_dir = _sandbox(tmp_path, monkeypatch, rules=[])
    (role_dir / "hdr_role.md").write_text("HDR-VIA-PAYLOAD", encoding="utf-8")

    r = ab.preprocess_mail_payload(
        _mail(headers={"X-aimail-prompt": "hdr_role"}), {})
    assert r["_role_prompt"] == "HDR-VIA-PAYLOAD"        # Email header source (gateway extension point)


def test_header_missing_file_warns_and_falls_through(tmp_path, monkeypatch, caplog):
    role_dir = _sandbox(tmp_path, monkeypatch, rules=[
        {"name": "10_local", "file": "local_role", "subject": ["quarterly"]}])
    (role_dir / "local_role.md").write_text("LOCAL", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=ab.logger.name):
        r = ab.preprocess_mail_payload(_mail(), {"X-AIMail-Prompt": "ghost"})
    assert r["_role_prompt"] == "LOCAL"
    assert any("X-AIMail-Prompt" in rec.message for rec in caplog.records)


def test_board_role_blocks_custom_layers(tmp_path, monkeypatch):
    role_dir = _sandbox(tmp_path, monkeypatch, rules=[
        {"name": "10_local", "file": "local_role", "subject": ["quarterly"]}])
    (role_dir / "worker.md").write_text("WORKER {{BOARD_ID}}", encoding="utf-8")
    (role_dir / "local_role.md").write_text("LOCAL", encoding="utf-8")

    r = ab.preprocess_mail_payload(
        _mail(board_id="B-7", board_role="worker"), {"X-AIMail-Prompt": "ghost"})
    assert r["_role_prompt"] == "WORKER B-7"             # board(L3) suppresses L4/L5
    assert r["_a2a_session_key"] == "a2a:B-7:boss@corp.com"


def test_name_order_first_hit(tmp_path, monkeypatch):
    role_dir = _sandbox(tmp_path, monkeypatch, rules=[
        {"name": "12_incident", "file": "incident", "subject": ["quarterly"]},
        {"name": "10_audit", "file": "audit", "subject": ["quarterly"]},
    ])
    (role_dir / "audit.md").write_text("AUDIT", encoding="utf-8")
    (role_dir / "incident.md").write_text("INCIDENT", encoding="utf-8")

    r = ab.preprocess_mail_payload(_mail(), {})
    assert r["_role_prompt"] == "AUDIT"                  # 10 < 12, stop at first hit


def test_matched_rule_missing_file_warns_and_next_rule(tmp_path, monkeypatch, caplog):
    role_dir = _sandbox(tmp_path, monkeypatch, rules=[
        {"name": "10_ghost", "file": "ghost", "subject": ["quarterly"]},
        {"name": "12_real", "file": "real", "subject": ["quarterly"]},
    ])
    (role_dir / "real.md").write_text("REAL", encoding="utf-8")  # ghost none of the three levels (no common)

    with caplog.at_level(logging.WARNING, logger=ab.logger.name):
        r = ab.preprocess_mail_payload(_mail(), {})
    assert r["_role_prompt"] == "REAL"
    assert any("10_ghost" in rec.message and "missing" in rec.message
               for rec in caplog.records)


def test_builtins_preempt_custom_rules(tmp_path, monkeypatch):
    role_dir = _sandbox(tmp_path, monkeypatch, rules=[
        {"name": "10_local", "file": "local_role", "subject": ["who", "welcome"]}])
    (role_dir / "local_role.md").write_text("LOCAL", encoding="utf-8")
    (role_dir / "role_calibrator.md").write_text("CALIBRATOR", encoding="utf-8")
    (role_dir / "whoami.md").write_text("WHOAMI", encoding="utf-8")

    # L1 [WHOAMI] early return
    r = ab.preprocess_mail_payload(_mail(subject="[WHOAMI] who?"), {})
    assert r.get("_whoami_prompt") == "WHOAMI"
    assert "_role_prompt" not in r
    # L2 welcome (subject marker + three tags) early return
    body = ("persona: P\nsignature: S\ncurrent_time: 2026-09-23\n")
    r = ab.preprocess_mail_payload(
        _mail(subject="Welcome to AIMail World, a1, since 2026-09-23!", body=body), {})
    assert r["_role_prompt"] == "CALIBRATOR"

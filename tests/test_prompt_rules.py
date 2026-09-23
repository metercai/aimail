"""prompt_rules 契约(Python 侧, 与 tssdk prompt-rules.test.ts 同型)。

裁决 2026-09-23:
  ① 字段=subject/body/sender/recipient 包含匹配(大小写不敏感);
     字段内多关键字=或, 字段间=且
  ② name={10-99 序号}_{filename}; 实际加载看 rule["file"]
  ③ 链序固定: [WHOAMI] > welcome > board > X-AIMail-Prompt header >
     本地规则(name 字母序, 首中即止); 命中但文件缺失(含 common 兜底都无)
     ⇒ WARN 续走; 任何一层不阻断送达
  ④ 键 prompt_rules(存 agentmail.json, 仅该 agent)
  ⑦ header 是网关扩展点: 双源 payload.headers / HTTP 头参数, 大小写不敏感
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
    """沙箱: AIMAIL_HOME→tmp, 注入 agent 配置(含 prompt_rules)与 profile 指针。"""
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


# ── ① 匹配矩阵: 字段内或 / 字段间且 / 大小写 / 缺席与坏型 ──────────

def test_field_or_within_and_across_fields():
    rule = {"name": "10_a", "file": "a",
            "subject": ["[incident]", "告警"], "sender": ["ops@x.com"]}
    assert ab.prompt_rule_matches(rule, "[INCIDENT] cluster down", "", "ops@x.com", "")
    assert ab.prompt_rule_matches(rule, "集群有告警", "", "OPS@X.COM", "")   # 字段内 or + 大小写
    assert not ab.prompt_rule_matches(rule, "plain mail", "", "ops@x.com", "")   # subject 未中
    assert not ab.prompt_rule_matches(rule, "[incident]", "", "boss@corp.com", "")  # 字段间 and


def test_absent_field_skips_empty_list_skips_bad_type_fails():
    absent = {"name": "10_a", "file": "a", "sender": ["boss@"]}
    assert ab.prompt_rule_matches(absent, "anything at all", "", "boss@corp.com", "")
    empty_ok = {"name": "10_a", "file": "a", "subject": [], "sender": ["boss@"]}
    assert ab.prompt_rule_matches(empty_ok, "anything", "", "boss@corp.com", "")  # 空表=未给
    bad_type = {"name": "10_a", "file": "a", "subject": {"k": 1}}
    assert not ab.prompt_rule_matches(bad_type, "x", "", "", "")                  # 坏型=不命中


# ── loader: 过滤坏项 + name 字母序(裁决②③) ────────────────────────

def test_loader_filters_bad_items_and_orders_by_name(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, rules=[
        {"name": "12_incident", "file": "incident", "subject": ["x"]},
        {"name": "10_audit", "file": "audit", "body": ["a"]},
        {"name": "09_bad_serial", "file": "bad", "subject": ["x"]},   # 序号 <10
        {"name": "13_nofile", "subject": ["x"]},                      # 缺 file
        {"name": "14_off", "file": "off", "subject": ["x"], "enabled": False},
        {"name": "15_nofield", "file": "nf"},                         # 无任何字段
        {"name": "16_badtype", "file": "bt", "subject": 123},         # 字段坏型
        "not-an-object",
    ])
    assert [r["name"] for r in ab.read_prompt_rules()] == ["10_audit", "12_incident"]


def test_loader_no_config_or_no_key_is_empty_not_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "_CONFIG_LOADER", None)
    assert ab.read_prompt_rules() == []
    _sandbox(tmp_path, monkeypatch, rules=None)  # cfg 无 prompt_rules 键
    assert ab.read_prompt_rules() == []


# ── 链序集成(③⑦): header 双源 / board 压制 / 首中即止 / 缺文件续走 ──

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
    assert r["_role_prompt"] == "HDR-VIA-PAYLOAD"        # 邮件头源(网关扩展点)


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
    assert r["_role_prompt"] == "WORKER B-7"             # board(L3) 压制 L4/L5
    assert r["_a2a_session_key"] == "a2a:B-7:boss@corp.com"


def test_name_order_first_hit(tmp_path, monkeypatch):
    role_dir = _sandbox(tmp_path, monkeypatch, rules=[
        {"name": "12_incident", "file": "incident", "subject": ["quarterly"]},
        {"name": "10_audit", "file": "audit", "subject": ["quarterly"]},
    ])
    (role_dir / "audit.md").write_text("AUDIT", encoding="utf-8")
    (role_dir / "incident.md").write_text("INCIDENT", encoding="utf-8")

    r = ab.preprocess_mail_payload(_mail(), {})
    assert r["_role_prompt"] == "AUDIT"                  # 10 < 12, 首中即止


def test_matched_rule_missing_file_warns_and_next_rule(tmp_path, monkeypatch, caplog):
    role_dir = _sandbox(tmp_path, monkeypatch, rules=[
        {"name": "10_ghost", "file": "ghost", "subject": ["quarterly"]},
        {"name": "12_real", "file": "real", "subject": ["quarterly"]},
    ])
    (role_dir / "real.md").write_text("REAL", encoding="utf-8")  # ghost 三级都没有(无 common)

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

    # L1 [WHOAMI] 早返回
    r = ab.preprocess_mail_payload(_mail(subject="[WHOAMI] who?"), {})
    assert r.get("_whoami_prompt") == "WHOAMI"
    assert "_role_prompt" not in r
    # L2 welcome(主题标记 + 三标签) 早返回
    body = ("persona: P\nsignature: S\ncurrent_time: 2026-09-23\n")
    r = ab.preprocess_mail_payload(
        _mail(subject="Welcome to AIMail World, a1, since 2026-09-23!", body=body), {})
    assert r["_role_prompt"] == "CALIBRATOR"

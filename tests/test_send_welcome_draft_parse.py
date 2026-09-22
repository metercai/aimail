"""S4 接线:_parse_draft_from_reply 按 outbound 快照布局解析草案(cli/send_welcome.py)。

真源布局(pysdk/aimail_tools.py::_save_outbound_snapshot):
  {AIMAIL_HOME}/mail/{cleaned_addr}/yyyymm/out-{safe_mid}.json
  payload 含 direction=outbound / subject / body。meta 常写,不受 save_raw_snapshots 控制。
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "cli"))

_spec = importlib.util.spec_from_file_location("send_welcome", REPO / "cli" / "send_welcome.py")
assert _spec is not None and _spec.loader is not None
sw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sw)


def _snapshot(home: Path, addr: str, yyyymm: str, mid: str, subject: str, body: str) -> None:
    cleaned = sw._clean_agent_dir_name(addr)  # 真源清洗规则: 点→下划线
    d = home / "mail" / cleaned / yyyymm
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "@.-_" else "_" for c in mid)
    (d / f"out-{safe}.json").write_text(json.dumps({
        "message_id": mid, "direction": "outbound", "subject": subject, "body": body,
    }, ensure_ascii=False), encoding="utf-8")


WELCOME_REPLY_BODY = (
    "Welcome to the AIMail world!\n\n"
    "persona: Diligent Ops Agent managing deployments\n"
    "signature: — Ops Agent · AIMail\n"
    "current_time: 2026-09-23 10:30 UTC\n"
)


def test_welcome_reply_snapshot_parses_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "AIMAIL_HOME", tmp_path)
    _snapshot(tmp_path, "agent@test.com", "202609", "<m-1@x>",
              "Re: Welcome to AIMail World, agent, since 2026-09-23!", WELCOME_REPLY_BODY)
    got = sw._parse_draft_from_reply("agent@test.com")
    assert got["persona"] == "Diligent Ops Agent managing deployments"
    assert got["signature"] == "— Ops Agent · AIMail"
    assert got["current_time"] == "2026-09-23 10:30 UTC"
    assert got["source"].endswith(".json")


def test_missing_section_returns_empty(tmp_path, monkeypatch):
    """缺 signature 段 ⇒ 空 dict(上层明确报错, 不猜不编造)。"""
    monkeypatch.setattr(sw, "AIMAIL_HOME", tmp_path)
    _snapshot(tmp_path, "agent@test.com", "202609", "<m-2@x>",
              "Re: Welcome to AIMail World", "persona: only persona here\n")
    assert sw._parse_draft_from_reply("agent@test.com") == {}


def test_plain_outbound_mail_skipped(tmp_path, monkeypatch):
    """非草案 outbound(无主题标记、无三标签)⇒ 跳过, 不污染解析。"""
    monkeypatch.setattr(sw, "AIMAIL_HOME", tmp_path)
    _snapshot(tmp_path, "agent@test.com", "202609", "<m-3@x>",
              "quarterly report", "see attached numbers\n")
    assert sw._parse_draft_from_reply("agent@test.com") == {}


def test_newer_incomplete_falls_back_to_older_complete(tmp_path, monkeypatch):
    """最新快照缺段、更早的完整 ⇒ 用完整那份。"""
    monkeypatch.setattr(sw, "AIMAIL_HOME", tmp_path)
    _snapshot(tmp_path, "agent@test.com", "202609", "<m-old@x>",
              "Re: Welcome to AIMail World", WELCOME_REPLY_BODY)
    _snapshot(tmp_path, "agent@test.com", "202609", "<m-new@x>",
              "Re: Welcome to AIMail World", "persona: partial\n")
    # 保证 mtime 严格递增(目录名 = 真源清洗: @ → _)
    d = tmp_path / "mail" / "agent_test.com" / "202609"
    old_file = d / "out-_m-old@x_.json"
    os.utime(old_file, (time.time() - 10, time.time() - 10))
    got = sw._parse_draft_from_reply("agent@test.com")
    assert got and got["persona"] == "Diligent Ops Agent managing deployments"


def test_no_mail_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "AIMAIL_HOME", tmp_path)
    assert sw._parse_draft_from_reply("nobody@test.com") == {}

"""Local mail search baseline (aimail_tools search_mail + snapshot index).

Pins the 2026-09-05 contract: inbound/outbound snapshots are gated by a
SINGLE switch (save_raw_snapshots, default ON); the snapshot file lands on
disk FIRST, then an incremental FTS5 (trigram) index row is upserted;
search_mail matches subject/body/md-escaped attachment text with
scope/time/sender filters, newest first, idempotent by message id,
no backfill of pre-existing snapshots, and a file-scan fallback when the
index db is unavailable.

Fixtures run against a throwaway AIMAIL_HOME (tmp_path) with a fixed agent
identity, so the tests are hermetic — no real profile, no network.
"""
import json
import os
import sys
import sqlite3

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysdk"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysdk", "hermes"))

import aimail_tools  # noqa: E402

AGENT = "agent.probe@test.local"
CFG = {
    "gateway_url": "http://127.0.0.1:9",
    "api_key": "k",
    "email": AGENT,
    "save_raw_snapshots": True,
}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    monkeypatch.setattr(aimail_tools, "_resolve_agent_email", lambda: AGENT)
    monkeypatch.setattr(aimail_tools, "_load_profile_config", lambda: dict(CFG))
    yield tmp_path


def _store_inbound(mid, subject="", body="", sender="boss@corp.com",
                   to=None, att_src=None, extra=None):
    payload = {
        "subject": subject,
        "body": body,
        "sender": sender,
        "to": to or [AGENT],
        "recipients": {"to": to or [AGENT], "cc": []},
        "my_amail_addr": AGENT,
        "ts": "2026-09-01T10:00:00",
    }
    if extra:
        payload.update(extra)
    aimail_tools.store_inbound_message(
        mid, ["t"], AGENT, preprocessed_payload=payload,
        attachment_sources={os.path.basename(p): p for p in (att_src or [])},
    )
    return payload


def _store_outbound(mid, subject="", body="", sender=AGENT, to="peer@corp.com",
                    cc=None, att_paths=None):
    aimail_tools._save_outbound_snapshot(
        mid, AGENT, sender, to, subject, body, cc or [], att_paths or [],
        [], "<prev>", "[t]",
    )


def _index_rowcount(home) -> int:
    db = sqlite3.connect(str(home / "mail" / "agent.probe_test.local" / ".search" / "index.db"))
    try:
        return db.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    finally:
        db.close()


# ── hits ─────────────────────────────────────────────────────────

def test_hits_subject_body_attachment(home, tmp_path):
    note = tmp_path / "note.md"
    note.write_text("Sales target for Q3 is 5 million units", encoding="utf-8")
    _store_inbound("in-1", subject="Quarterly report", body="please review the numbers",
                   att_src=[str(note)])
    _store_outbound("out-1", subject="Re: report", body="thanks, saw it")

    assert aimail_tools.search_mail(query="quarterly")["count"] == 1
    body_hit = aimail_tools.search_mail(query="numbers")
    assert body_hit["count"] == 1 and body_hit["results"][0]["matched_in"] == "body"
    att_hit = aimail_tools.search_mail(query="5 million")
    assert att_hit["count"] == 1 and att_hit["results"][0]["matched_in"] == "attachment"
    assert "5 million" in att_hit["results"][0]["snippet"]


def test_hits_chinese_body_and_attachment(home):
    _store_inbound("in-zh", subject="季度报告", body="销量目标已经达成")
    assert aimail_tools.search_mail(query="销量")["count"] == 1
    assert aimail_tools.search_mail(query="季度")["results"][0]["matched_in"] == "subject"


def test_query_and_requires_all_words(home):
    _store_inbound("in-2", subject="alpha beta", body="gamma")
    assert aimail_tools.search_mail(query="alpha beta")["count"] == 1
    # words may hit DIFFERENT columns (subject/body/att) — AND is row-level
    assert aimail_tools.search_mail(query="alpha gamma")["count"] == 1
    assert aimail_tools.search_mail(query="alpha delta")["count"] == 0
    assert aimail_tools.search_mail(query="alpha")["count"] == 1


# ── filters ──────────────────────────────────────────────────────

def test_scope_filter(home):
    _store_inbound("in-3", subject="scope probe")
    _store_outbound("out-3", subject="scope probe")
    assert aimail_tools.search_mail(query="scope probe", scope="inbound")["count"] == 1
    assert aimail_tools.search_mail(query="scope probe", scope="outbound")["count"] == 1
    assert aimail_tools.search_mail(query="scope probe")["count"] == 2


def test_time_window_filter(home):
    _store_inbound("in-t1", subject="old one", extra={"ts": "2026-01-05T08:00:00"})
    _store_inbound("in-t2", subject="new one", extra={"ts": "2026-09-05T08:00:00"})
    assert aimail_tools.search_mail(query="one", since="2026-09-01")["count"] == 1
    assert aimail_tools.search_mail(query="one", until="2026-02-01")["count"] == 1
    assert aimail_tools.search_mail(query="one", since="2026-03-01", until="2026-08-01")["count"] == 0


def test_from_filter_substring(home):
    _store_inbound("in-4", subject="from filter", sender="Manager.Boss@corp.com")
    assert aimail_tools.search_mail(query="from filter", from_="manager.boss")["count"] == 1
    assert aimail_tools.search_mail(query="from filter", from_="other")["count"] == 0


def test_browse_empty_query_sorted_newest_first(home):
    _store_inbound("in-5", subject="older", extra={"ts": "2026-01-01T00:00:00"})
    _store_inbound("in-6", subject="newer", extra={"ts": "2026-09-01T00:00:00"})
    res = aimail_tools.search_mail()
    assert res["count"] == 2 and res["results"][0]["subject"] == "newer"
    assert aimail_tools.search_mail(scope="outbound")["count"] == 0


# ── idempotency / switch / backfill ─────────────────────────────

def test_repeated_store_idempotent(home):
    _store_inbound("in-dup", subject="dup subject", body="dup body")
    _store_inbound("in-dup", subject="dup subject", body="dup body")
    assert aimail_tools.search_mail(query="dup")["count"] == 1
    assert _index_rowcount(home) == 1


def test_switch_off_no_index_note(home, monkeypatch):
    monkeypatch.setattr(aimail_tools, "_load_profile_config",
                        lambda: {**CFG, "save_raw_snapshots": False})
    _store_inbound("in-off", subject="never stored", body="x")
    # no snapshot file, no index row
    assert not list((home / "mail" / "agent.probe_test.local").glob("*/*.json"))
    res = aimail_tools.search_mail(query="never")
    assert res["count"] == 0 and "note" in res


def test_no_backfill_of_preexisting_snapshots(home):
    # an old snapshot sitting in yyyymm/ before the index feature existed
    old_dir = home / "mail" / "agent.probe_test.local" / "202605"
    old_dir.mkdir(parents=True)
    (old_dir / "in-OLD123.json").write_text(json.dumps(
        {"subject": "ancient", "body": "pre-index content", "sender": "old@corp.com",
         "to": [AGENT]}), encoding="utf-8")
    assert aimail_tools.search_mail(query="ancient")["count"] == 0


# ── fallback scan (index unavailable) ────────────────────────────

def test_fallback_scan_when_index_missing(home, tmp_path, monkeypatch):
    note = tmp_path / "plan.md"
    note.write_text("launch checklist v2", encoding="utf-8")
    _store_inbound("in-fb", subject="fallback subject", body="fallback body",
                   att_src=[str(note)])
    _store_outbound("out-fb", subject="outbound too", body="fallback out")
    # kill the index db → ordered scan of snapshot files must answer
    idx = home / "mail" / "agent.probe_test.local" / ".search"
    import shutil
    shutil.rmtree(idx, ignore_errors=True)
    monkeypatch.setattr(aimail_tools, "_open_search_index", lambda: None)
    assert aimail_tools.search_mail(query="fallback")["count"] == 2
    assert aimail_tools.search_mail(query="launch checklist")["count"] == 1
    assert aimail_tools.search_mail(query="outbound too", scope="outbound")["count"] == 1
    assert aimail_tools.search_mail(query="fallback", scope="inbound")["count"] == 1


# ── validation ───────────────────────────────────────────────────

def test_validation_errors(home):
    assert aimail_tools.search_mail(scope="x")["error_code"] == "INVALID_SCOPE"
    assert aimail_tools.search_mail(since="2026/01/01")["error_code"] == "INVALID_DATE"
    assert aimail_tools.search_mail(until="2026-01-02", since="2026-01-03")["error_code"] == "INVALID_DATE"
    res = aimail_tools.search_mail(limit=9999, scope="outbound")
    assert res["success"] is True  # limit clamped, not an error

"""System-identity default resolution baseline (runtime_core).

Pins the platform-agnostic default chain that replaced the hardcoded
"default platform = ~/.hermes" pattern (2026-09-05): single-system
directory scan and 5-platform pointer scan must be unambiguous —
zero or several candidates ⇒ '' (caller asks for --system-id).
"""
import json
import os

from runtime_core import single_system_sid, platform_pointer_sid, resolve_system_id


def _mk_system(aimail_home, sid):
    d = aimail_home / "systems" / sid
    (d / "aimail_gateway.json").parent.mkdir(parents=True, exist_ok=True)
    (d / "aimail_gateway.json").write_text('{"gateway_url": "https://x"}')
    return d


def test_single_system_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    _mk_system(tmp_path, "system-abc")
    assert single_system_sid() == "system-abc"


def test_single_system_scan_ambiguous(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    _mk_system(tmp_path, "system-abc")
    _mk_system(tmp_path, "system-def")
    assert single_system_sid() == ""  # two systems ⇒ don't guess


def test_single_system_scan_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    assert single_system_sid() == ""


def test_explicit_sid_wins_over_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    _mk_system(tmp_path, "system-abc")
    assert resolve_system_id("system-explicit") == "system-explicit"


def test_env_sid_second(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSTEM_ID", "system-env")
    assert resolve_system_id("") == "system-env"
    monkeypatch.delenv("SYSTEM_ID")
    monkeypatch.setenv("AIMAIL_SYSTEM_ID", "system-env2")
    assert resolve_system_id("") == "system-env2"


def test_agent_home_pointer(tmp_path, monkeypatch):
    monkeypatch.delenv("SYSTEM_ID", raising=False)
    monkeypatch.delenv("AIMAIL_SYSTEM_ID", raising=False)
    ptr_dir = tmp_path / "fake-platform"
    ptr_dir.mkdir()
    (ptr_dir / ".agentmail").write_text(json.dumps({"system_id": "system-ptr"}))
    assert resolve_system_id("", str(ptr_dir)) == "system-ptr"


def test_platform_pointer_scan(tmp_path, monkeypatch):
    monkeypatch.delenv("SYSTEM_ID", raising=False)
    monkeypatch.delenv("AIMAIL_SYSTEM_ID", raising=False)
    # fake $HOME with a single platform pointer
    fake_home = tmp_path / "home"
    (fake_home / ".openclaw").mkdir(parents=True)
    (fake_home / ".openclaw" / ".agentmail").write_text(
        json.dumps({"system_id": "system-oc"}))
    assert platform_pointer_sid(home=fake_home) == "system-oc"


def test_platform_scan_multiple_hits_returns_first(tmp_path, monkeypatch):
    # matches check_status _detect_default_sid semantics: registry order,
    # first platform with a pointer wins (openclaw before hermes)
    monkeypatch.delenv("SYSTEM_ID", raising=False)
    monkeypatch.delenv("AIMAIL_SYSTEM_ID", raising=False)
    fake_home = tmp_path / "home2"
    (fake_home / ".pi").mkdir(parents=True)
    (fake_home / ".pi" / ".agentmail").write_text(
        json.dumps({"system_id": "system-pi"}))
    (fake_home / ".openclaw").mkdir(parents=True)
    (fake_home / ".openclaw" / ".agentmail").write_text(
        json.dumps({"system_id": "system-oc"}))
    assert platform_pointer_sid(home=fake_home) == "system-oc"  # registry order


def test_no_pointer_returns_empty(tmp_path):
    assert platform_pointer_sid(home=tmp_path / "nonexistent") == ""

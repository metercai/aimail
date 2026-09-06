"""install target two-way resolution (runtime_core, 2026-09-06).

`aimail install` now accepts EITHER --home OR --system-id: the missing
side is resolved from the local system config (system_home round-trips
with system_id). Ambiguity (several systems claim one home, or no
config) resolves to '' — the caller asks for the explicit argument.
"""
import json
import os

from runtime_core import (
    sid_from_system_home,
    system_home_from_sid,
)


def _mk_system(aimail_home, sid, system_home):
    d = aimail_home / "systems" / sid
    (d / "aimail_gateway.json").parent.mkdir(parents=True, exist_ok=True)
    (d / "aimail_gateway.json").write_text(
        json.dumps({"gateway_url": "https://x", "system_home": str(system_home)}))
    return d


def test_sid_to_home_resolves(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    home = tmp_path / ".hermes"
    _mk_system(tmp_path, "shared-token-abc", home)
    assert system_home_from_sid("shared-token-abc") == str(home)


def test_sid_to_home_unknown_sid(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    assert system_home_from_sid("no-such-system") == ""


def test_home_to_sid_unique_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    home = tmp_path / "hermes-root"
    _mk_system(tmp_path, "system-aaa", home)
    _mk_system(tmp_path, "system-bbb", tmp_path / "other")
    assert sid_from_system_home(str(home)) == "system-aaa"


def test_home_to_sid_ambiguous_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    home = tmp_path / "shared-root"
    _mk_system(tmp_path, "system-aaa", home)
    _mk_system(tmp_path, "system-bbb", home)  # two systems claim one home
    assert sid_from_system_home(str(home)) == ""


def test_home_to_sid_no_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    _mk_system(tmp_path, "system-aaa", tmp_path / "elsewhere")
    assert sid_from_system_home(str(tmp_path / "nowhere")) == ""


def test_home_to_sid_empty_input(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    assert sid_from_system_home("") == ""

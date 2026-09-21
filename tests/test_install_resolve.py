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


# ── 指针优先(2026-09-21 生产实证)────────────────────────────────────────
# 同一平台根被多个系统声明时(换系统重装不放开旧 system_home / e2e 夹具常驻),
# 纯扫描判成"歧义 → ''" ⇒ ensure-system 回落到 .env 里已消耗的码, 宿主插件
# 报出误导性的 "no aimail system yet — Invalid activation code"。
# 平台根自己写的 .agentmail 才是权威归属声明。

def _mk_ptr(home, sid):
    home.mkdir(parents=True, exist_ok=True)
    (home / ".agentmail").write_text(json.dumps({"system_id": sid}))


def test_pointer_wins_over_ambiguous_claimants(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    home = tmp_path / "dsh-root"
    _mk_system(tmp_path, "system-old", home)
    _mk_system(tmp_path, "system-new", home)      # 两个都声明同一 home
    _mk_ptr(home, "system-new")                   # 平台自己说绑的是 new
    assert sid_from_system_home(str(home)) == "system-new"


def test_stale_pointer_falls_back_to_scan(tmp_path, monkeypatch):
    """指针指向本机不存在的系统(陈旧指针) ⇒ 不返回它, 退回扫描。"""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    home = tmp_path / "dsh-root"
    _mk_system(tmp_path, "system-aaa", home)
    _mk_ptr(home, "system-gone")
    assert sid_from_system_home(str(home)) == "system-aaa"

"""CLI↔网关 key 类别/落盘契约回归（2026-09-21 生产实测发现的两条）。

P1: `_downgrade_to_agent_admin_key` 曾把 **scope 名**当 **category** 传
    (`category="agent_admin"`) —— 网关只接受 `platform|system|domain|agent|bridge`,
    该调用在生产与本地 advanced 上**必然**失败, setup 于是静默保留权限更大的
    system key（最小权限降级从未发生）。

P2: cli/README.md:121 承诺"原始 key 存 .system_raw_key/{sid}_admin.key", 但
    激活+降级路径**没实现** —— 降级后系统级凭据丢失, 管理级操作(repair/address/
    key 轮换)无从认证。同时"传入的 key 已是 agent 级"时会触发误导性的降级失败告警。

锁四件事:
  1. category 必须是网关接受的 `agent`, scopes 仍是 ["agent_admin"]
  2. 原始系统 key 先落盘(0600), 再降级
  3. 已是 agent 级的 key → 跳过降级(不发无效请求)
  4. 真失败时优雅降级(返回原 key)且 error 级告警(提权不可静默)
"""
import json
import logging
import stat
from pathlib import Path

import setup_system as ss


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "home"))


def test_category_is_agent_and_raw_key_persisted(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    seen = {}

    def fake_create_api_key(gw, ak, system_id, email, scopes, category):
        seen.update(scopes=scopes, category=category, email=email)
        return {"raw_key": "agentkey", "status": 200}

    monkeypatch.setattr(ss, "create_api_key", fake_create_api_key)
    monkeypatch.setattr(ss, "whoami", lambda *a, **k: {"scopes": ["system_admin"]})
    cfg = tmp_path / "aimail_gateway.json"
    cfg.write_text(json.dumps({"admin_key": "syskey"}))
    monkeypatch.setattr(ss, "gateway_config_path", lambda sid: cfg)

    out = ss._downgrade_to_agent_admin_key("https://gw", "syskey", "shared-default-abc", "mgr@x.tm")

    assert seen["category"] == "agent", "网关只接受 platform|system|domain|agent|bridge"
    assert seen["scopes"] == ["agent_admin"]
    assert "@" in seen["email"]
    assert out == "agentkey" and json.loads(cfg.read_text())["admin_key"] == "agentkey"
    # P2: 原始系统 key 必须先落盘(0600), 否则降级后凭据丢失
    raw = tmp_path / "home" / ".system_raw_key" / "shared-default-abc_admin.key"
    assert raw.is_file(), "cli/README.md:121 承诺的 .system_raw_key 落盘未实现"
    assert raw.read_text().strip() == "syskey"
    assert stat.S_IMODE(raw.stat().st_mode) == 0o600


def test_already_agent_scoped_key_skips_downgrade(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    def _boom(*a, **k):
        raise AssertionError("已是 agent 级时不应再发 create_api_key")
    monkeypatch.setattr(ss, "create_api_key", _boom)
    monkeypatch.setattr(ss, "whoami", lambda *a, **k: {"scopes": ["agent_admin"]})
    out = ss._downgrade_to_agent_admin_key("https://gw", "agentkey", "sid", "mgr@x.tm")
    assert out == "agentkey"


def test_failure_returns_system_key_and_warns_loudly(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(ss, "whoami", lambda *a, **k: {"scopes": ["system_admin"]})
    monkeypatch.setattr(ss, "create_api_key",
                        lambda *a, **k: {"error": "invalid_category", "detail": "boom", "status": 400})
    records = []

    class _H(logging.Handler):
        def emit(self, record): records.append(record)

    ss.logger.addHandler(_H())
    ss.logger.setLevel(logging.DEBUG)
    out = ss._downgrade_to_agent_admin_key("https://gw", "syskey", "sid", "mgr@x.tm")
    assert out == "syskey"                      # 优雅降级: 功能可用
    assert any(r.levelno >= logging.ERROR for r in records), "提权降级必须 error 级告警"

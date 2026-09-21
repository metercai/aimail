"""CLI↔网关 key 类别契约回归（P1，2026-09-21 生产实测发现）。

背景: `cli/setup_system.py::_downgrade_to_agent_admin_key` 曾把 **scope 名**当
**category** 传给网关(`category="agent_admin"`) —— 而网关 keys.rs 只接受
`platform|system|domain|agent|bridge`，因此该调用在生产与本地 advanced 上**必然**
失败，setup 于是静默保留权限更大的 system key（最小权限降级从未发生）。

本测试锁三件事：
  1. category 必须是网关接受的 `agent`，scopes 仍是 ["agent_admin"]
  2. 失败时返回原始 key（优雅降级）且以 **error** 级别告警（提权不可静默）
  3. 成功时写回 cfg 的 admin_key 并返回新 key
"""
import json
import logging
from pathlib import Path

import pytest

import setup_system as ss


def test_category_is_agent_not_scope_name(monkeypatch, tmp_path):
    seen = {}

    def fake_create_api_key(gw, ak, system_id, email, scopes, category):
        seen.update(gw=gw, system_id=system_id, email=email, scopes=scopes, category=category)
        return {"raw_key": "newkey123", "status": 200}

    monkeypatch.setattr(ss, "create_api_key", fake_create_api_key)
    cfg = tmp_path / "aimail_gateway.json"
    cfg.write_text(json.dumps({"gateway_url": "https://gw", "admin_key": "syskey"}))
    monkeypatch.setattr(ss, "gateway_config_path", lambda sid: cfg)

    out = ss._downgrade_to_agent_admin_key("https://gw", "syskey", "shared-default-abc", "mgr@x.tm")

    assert seen["category"] == "agent", "网关只接受 platform|system|domain|agent|bridge"
    assert seen["scopes"] == ["agent_admin"]
    assert "@" in seen["email"], "category=agent 要求 email_address 含 '@'"
    assert out == "newkey123"
    assert json.loads(cfg.read_text())["admin_key"] == "newkey123"


def test_failure_returns_system_key_and_warns_loudly(monkeypatch, capsys):
    monkeypatch.setattr(ss, "create_api_key",
                        lambda *a, **k: {"error": "invalid_category", "detail": "boom", "status": 400})
    records = []

    class _H(logging.Handler):
        def emit(self, r): records.append(r)

    ss.logger.addHandler(_H())
    ss.logger.setLevel(logging.DEBUG)
    out = ss._downgrade_to_agent_admin_key("https://gw", "syskey", "sid", "mgr@x.tm")
    assert out == "syskey"                      # 优雅降级：功能可用
    assert any(r.levelno >= logging.ERROR for r in records), "提权降级必须 error 级告警"

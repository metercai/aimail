"""CLI↔网关 key 类别/落盘契约回归（2026-09-21 生产实测发现）。

P1: `_downgrade_to_agent_admin_key` 曾把 **scope 名**当 **category** 传
    (`category="agent_admin"`) —— 网关只接受 `platform|system|domain|agent|bridge`,
    该调用在生产与本地 advanced 上**必然**失败, setup 于是静默保留权限更大的
    system key（最小权限降级从未发生）。

P2: cli/README.md:121 承诺"原始 key 存 .system_raw_key/{sid}_admin.key", 但
    激活+降级路径**没实现**; 更糟的是"传入的已是 agent 级 key"(复用路径)会被误判成
    降级失败并可能把 agent key 当系统 key 落盘, 污染该契约。

锁五件事:
  1. category 必须是 `agent`, scopes 仍是 ["agent_admin"]
  2. 降级**成功**后才把原始系统 key 落盘(0600)
  3. 传入的 key 已是 agent 级(网关报 privilege level) → 视为"无需降级": 返原 key,
     **不落盘**、**不打 error**
  4. 其他真失败 → 优雅降级(返原 key) + error 级告警(提权不可静默) + 不落盘
  5. whoami 预检可用时同样跳过降级
"""
import json
import logging
import stat

import setup_system as ss


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "home"))
    # whoami 默认不可用(真实网关对某些 key/identity 组合取不到作用域) —— 判定以
    # create_api_key 的错误文本为准
    monkeypatch.setattr(ss, "whoami", lambda *a, **k: {})


def _cfg(monkeypatch, tmp_path, key="syskey"):
    cfg = tmp_path / "aimail_gateway.json"
    cfg.write_text(json.dumps({"admin_key": key}))
    monkeypatch.setattr(ss, "gateway_config_path", lambda sid: cfg)
    return cfg


def _raw(tmp_path, sid="shared-default-abc"):
    return tmp_path / "home" / ".system_raw_key" / f"{sid}_admin.key"


def test_category_is_agent_and_raw_key_persisted_on_success(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    seen = {}

    def fake_create(gw, ak, system_id, email, scopes, category):
        seen.update(scopes=scopes, category=category, email=email)
        return {"raw_key": "agentkey", "status": 200}

    monkeypatch.setattr(ss, "create_api_key", fake_create)
    cfg = _cfg(monkeypatch, tmp_path)
    out = ss._downgrade_to_agent_admin_key("https://gw", "syskey", "shared-default-abc", "mgr@x.tm")

    assert seen["category"] == "agent", "网关只接受 platform|system|domain|agent|bridge"
    assert seen["scopes"] == ["agent_admin"] and "@" in seen["email"]
    assert out == "agentkey" and json.loads(cfg.read_text())["admin_key"] == "agentkey"
    raw = _raw(tmp_path)
    assert raw.is_file() and raw.read_text().strip() == "syskey"
    assert stat.S_IMODE(raw.stat().st_mode) == 0o600


def test_already_agent_scoped_is_not_a_failure_and_not_persisted(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(ss, "create_api_key", lambda *a, **k: {
        "status": 403,
        "error": "forbidden",
        "detail": "Cannot create key at or above your privilege level Your max scope is 'agent_admin' (level 1), cannot create scopes at level 1 or above",
    })
    _cfg(monkeypatch, tmp_path, key="agentkey")
    records = []

    class _H(logging.Handler):
        def emit(self, record): records.append(record)
    ss.logger.addHandler(_H()); ss.logger.setLevel(logging.DEBUG)

    out = ss._downgrade_to_agent_admin_key("https://gw", "agentkey", "shared-default-abc", "mgr@x.tm")

    assert out == "agentkey"
    assert not _raw(tmp_path).exists(), "无需降级时不得写 .system_raw_key"
    assert not any(r.levelno >= logging.ERROR for r in records), "这不是错误, 不该打 error"


def test_real_failure_keeps_system_key_warns_loudly_and_does_not_persist(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(ss, "create_api_key",
                        lambda *a, **k: {"error": "invalid_category", "detail": "boom", "status": 400})
    _cfg(monkeypatch, tmp_path)
    records = []

    class _H(logging.Handler):
        def emit(self, record): records.append(record)
    ss.logger.addHandler(_H()); ss.logger.setLevel(logging.DEBUG)

    out = ss._downgrade_to_agent_admin_key("https://gw", "syskey", "shared-default-abc", "mgr@x.tm")

    assert out == "syskey"                                   # 优雅降级: 功能可用
    assert any(r.levelno >= logging.ERROR for r in records)   # 提权降级必须 error
    assert not _raw(tmp_path).exists()                        # 未成功不落盘


def test_whoami_precheck_skips_downgrade(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(ss, "whoami", lambda *a, **k: {"scopes": ["agent_admin"]})

    def _boom(*a, **k):
        raise AssertionError("whoami 已判明是 agent 级, 不应再发 create_api_key")
    monkeypatch.setattr(ss, "create_api_key", _boom)
    out = ss._downgrade_to_agent_admin_key("https://gw", "agentkey", "shared-default-abc", "mgr@x.tm")
    assert out == "agentkey" and not _raw(tmp_path).exists()

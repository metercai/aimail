"""CLI↔网关 key 类别/落盘契约回归（2026-09-21 生产实测发现，同日修正）。

P1（第一次）: `_downgrade_to_*` 曾把 **scope 名**当 **category** 传
    (`category="agent_admin"`) —— 网关只接受 `platform|system|domain|agent|bridge`，
    该调用在生产与本地 advanced 上**必然**失败，setup 于是静默保留权限更大的
    system key（最小权限降级从未发生）。

P1（第二次，修正后生产复现）: 改成 `category="agent"` + `email_address=manager`
    虽然"降级成功了"，但 manager 地址在**系统域外** ⇒ 网关
    `POST /admin/systems/{sid}/addresses` 内 `require_domain_match(key, <裸域>)`
    (`core/api/auth.rs:check_domain_access`) 恒拒：
    `403 API key email '…' does not match target '…' — cross-address access denied`
    ⇒ **任何地址注册都做不了**（register-cli / SDK `register_email` 全废）。
    正确形态 = 网关 `keys.rs` 明文支持的 **domain 类**：`category="domain"` +
    `email_address=<裸域>` + `scopes=["system"]`（system key 可降级为"单域"，
    `is_system_to_domain`）；身份收窄到一个裸域、且造不出 system/platform 级 key。

P2: cli/README 承诺"原始 key 存系统层 .system_raw_key.key(三层收口)"，但
    激活+降级路径**没实现**；更糟的是"传入的已是受限 key"（复用路径）会被误判成
    降级失败并可能把受限 key 当系统 key 落盘，污染该契约。

锁六件事：
  1. category 必须是 `domain`，scopes 必须是 `["system"]`，email **是裸域**(无 '@')
  2. **拿到即落盘**(2026-09-22 用户要求): 平台下发的/经 whoami 证明为系统级的 key
     立刻写 systems/{sid}/.system_raw_key.key(0600), 与随后降级是否成功**无关**;
     只有"确认是受限级"的 key 才不落盘(不污染该系统 key 契约)
  3. 传入的 key 已是 agent 级(网关报 privilege level) → 视为"无需降级": 返原 key，
     **不落盘**、**不打 error**
  4. 传入的 key 已是本域 domain key(whoami 可读) → 同样跳过，**不重复造 key**
     （同级别创建会被网关拒）
  5. 无域可用 → 不发 create_api_key、保留系统级 key（不制造域外身份的坏 key）
  6. 其他真失败 → 优雅降级(返原 key) + error 级告警(提权不可静默) + 不落盘
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
    # 三层收口(2026-09-23): 系统层单文件
    return tmp_path / "home" / "systems" / sid / ".system_raw_key.key"


def test_category_is_domain_with_bare_domain_identity(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    seen = {}

    def fake_create(gw, ak, system_id, email, scopes, category):
        seen.update(scopes=scopes, category=category, email=email)
        return {"raw_key": "domainkey", "status": 200}

    monkeypatch.setattr(ss, "create_api_key", fake_create)
    cfg = _cfg(monkeypatch, tmp_path)
    out = ss._downgrade_to_domain_admin_key(
        "https://gw", "syskey", "shared-default-abc", "aimail.token.tm")

    assert seen["category"] == "domain", "网关只接受 platform|system|domain|agent|bridge"
    assert seen["scopes"] == ["system"], "domain 类 key 靠 system scope 才有域级管理权"
    # 身份必须是**裸域**: 带 '@' 的域外身份会被 require_domain_match 拒(403)
    assert seen["email"] == "aimail.token.tm" and "@" not in seen["email"]
    assert out == "domainkey" and json.loads(cfg.read_text())["admin_key"] == "domainkey"
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

    out = ss._downgrade_to_domain_admin_key(
        "https://gw", "agentkey", "shared-default-abc", "aimail.token.tm")

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

    out = ss._downgrade_to_domain_admin_key(
        "https://gw", "syskey", "shared-default-abc", "aimail.token.tm")

    assert out == "syskey"                                   # 优雅降级: 功能可用
    assert any(r.levelno >= logging.ERROR for r in records)   # 提权降级必须 error
    assert not _raw(tmp_path).exists()                        # 未成功不落盘


def test_whoami_precheck_skips_downgrade_for_agent_key(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    # whoami 的真实字段(网关 core/api/whoami.rs): scope / category / email / system_id
    monkeypatch.setattr(ss, "whoami", lambda *a, **k: {
        "scope": "agent_admin", "category": "agent_admin", "email": "agent@x.local"})

    def _boom(*a, **k):
        raise AssertionError("whoami 已判明是受限级, 不应再发 create_api_key")
    monkeypatch.setattr(ss, "create_api_key", _boom)
    out = ss._downgrade_to_domain_admin_key(
        "https://gw", "agentkey", "shared-default-abc", "aimail.token.tm")
    assert out == "agentkey" and not _raw(tmp_path).exists()


def test_whoami_precheck_skips_when_already_domain_scoped(monkeypatch, tmp_path):
    """已是本域 domain key(whoami 报 system scope + 裸域 email) ⇒ 不重复造 key。

    同级别创建会被网关拒("Cannot create key at or above your privilege level"),
    每次 reset/repair 都重试会刷 error 日志且造成 key 轮换。
    """
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(ss, "whoami", lambda *a, **k: {
        "scope": "system", "category": "domain", "email": "aimail.token.tm"})

    def _boom(*a, **k):
        raise AssertionError("已是最小权限形态, 不应再发 create_api_key")
    monkeypatch.setattr(ss, "create_api_key", _boom)
    out = ss._downgrade_to_domain_admin_key(
        "https://gw", "domainkey", "shared-default-abc", "aimail.token.tm")
    assert out == "domainkey" and not _raw(tmp_path).exists()


def test_stored_domain_key_is_reused_instead_of_rotated(monkeypatch, tmp_path):
    """配置里已存**本域** domain key ⇒ 复用它, 不重新收窄(2026-09-26 幂等修复)。

    修复前: 显式 `-k <系统级 key>` 的重复安装每次都造一把新 domain key ⇒ 配置漂移
    (b1「二次安装 cfg 不变」实测红) + 网关侧累积历史 key。判据走 whoami 并要求域一致。
    """
    _env(monkeypatch, tmp_path)

    def _whoami(_gw, key, *a, **k):
        # 传入的系统级 key vs 配置里已存的 domain 级 key —— 两者形状必须不同,
        # 否则测不出"复用已存 key"这条判据。
        if key == "stored-domain-key":
            return {"scope": "system", "category": "domain", "email": "aimail.token.tm"}
        return {"scope": "system", "category": "system", "email": ""}

    monkeypatch.setattr(ss, "whoami", _whoami)
    _cfg(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise AssertionError("已有本域 domain key ⇒ 不得再造/轮换")
    monkeypatch.setattr(ss, "create_api_key", _boom)

    out = ss._downgrade_to_domain_admin_key(
        "https://gw", "syskey", "shared-default-abc", "aimail.token.tm",
        existing_key="stored-domain-key")
    assert out == "stored-domain-key", "复用的应是配置里那把(不是传入的系统级 key)"
    assert json.loads((tmp_path / "aimail_gateway.json").read_text())["admin_key"] \
        == "stored-domain-key", "配置里仍是原 domain key"
    assert _raw(tmp_path).read_text().strip() == "syskey", \
        "传入的系统级 key 仍按 2026-09-22 契约落盘(与复用本域 key 互不冲突)"


def test_no_domain_keeps_system_key_without_creating_anything(monkeypatch, tmp_path):
    """没有域可收窄(本地/无域系统) ⇒ 不发 create_api_key, 保留系统级 key。"""
    _env(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise AssertionError("无域时不应造 domain key")
    monkeypatch.setattr(ss, "create_api_key", _boom)
    _cfg(monkeypatch, tmp_path)
    out = ss._downgrade_to_domain_admin_key("https://gw", "syskey", "shared-default-abc", "")
    assert out == "syskey" and not _raw(tmp_path).exists()


def test_whoami_system_level_key_is_persisted_immediately(monkeypatch, tmp_path):
    """拿到即落盘(其一): whoami 证明是系统级(空 email + system scope) ⇒ 立刻落盘。

    2026-09-22 用户要求: "不管什么情况, 拿到后立刻落盘"。此前只在降级**成功**分支落盘,
    降级失败/早退时系统级 key 只剩云端哈希 ⇒ 本地永久不可得(pi 系统即如此)。
    """
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(ss, "whoami", lambda *a, **k: {
        "scope": "system", "category": "system", "email": ""})
    monkeypatch.setattr(ss, "create_api_key",
                        lambda *a, **k: {"error": "boom", "detail": "transient", "status": 500})
    _cfg(monkeypatch, tmp_path)

    out = ss._downgrade_to_domain_admin_key(
        "https://gw", "syskey", "shared-default-abc", "aimail.token.tm")

    assert out == "syskey", "降级失败也返回系统级 key(功能仍可用)"
    raw = _raw(tmp_path)
    assert raw.is_file() and raw.read_text().strip() == "syskey", "系统级 key 必须立刻落盘"
    assert stat.S_IMODE(raw.stat().st_mode) == 0o600


def test_init_system_persists_raw_key_before_downgrade():
    """拿到即落盘(其二, **结构化契约**): 激活分支的落盘调用必须**先于**降级调用。

    功能级验证需要 mock 整条激活响应(收益低); 这里直接锁源码顺序 —— 契约本身就直白:
    平台下发的系统级 key 必须在"拿到"当口落盘, 而不管随后降级是否成功/是否早退。
    """
    import inspect

    src = inspect.getsource(ss.init_system)
    i_persist = src.find("_persist_system_raw_key(")
    i_downgrade = src.find("_downgrade_to_domain_admin_key(")
    assert i_persist != -1, "激活分支必须把平台下发的系统级 key 落盘"
    assert i_downgrade != -1, "激活分支仍会尝试最小权限降级"
    assert i_persist < i_downgrade, "落盘必须先于降级(拿到即落盘)"

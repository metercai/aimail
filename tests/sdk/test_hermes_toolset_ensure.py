"""hermes 工具集 ensure 链单元测试(J4e 根因回归锁, 2026-09-30)。

J4e 根因(二选一定死 = **(i) 跳过**, 非写错目录):
  iso16 L2 日志 86 行 `registered:0`(install 自报) 与 557 行
  `ensure_profile_config: ['config.yaml missing (/opt/data/config.yaml) — skipped']`
  是同一个缺口的两次现身 ——
    · 首次 install: config.yaml 还不存在 ⇒ ensure 直接 return(不写);
    · 之后的 install: `.agentmail` 指针同 system_id ⇒ register_emails 整段跳过  contract-allowed: 文字说明(非代码)引用契约指针文件名
      (registered:0) ⇒ ensure 根本不被调用。
  两次都不会写 platform_toolsets ⇒ webhook 会话回退默认工具集 ⇒
  agent 写好回信也发不出(J4e sent=1 reply=0)。
写/读同源: 写入目标 = HERMES_HOME/<profile>/config.yaml, 读取目标 = check 的
`yaml_toolsets` glob(cli/platforms.json → platforms/hermes/health_checks[3]),
两者是同一个文件(本组测试用真实 glob 断言, 不靠"看起来一样")。

覆盖:
  1. 工具集未注册 ⇒ ensure 后已注册(config 缺失也补);
  2. 已注册 ⇒ 不重复写(幂等: 无改动、文件字节不变);
  3. 写入路径与读取路径同源(真 glob);
  4. 注册标记(pointer 已匹配 ⇒ registered:0)不再短路 ensure。
"""
import glob
import json
import os
import sys
from pathlib import Path

import yaml
import pytest

_REPO = Path(__file__).resolve().parent.parent
_PATHS = [str(_REPO / _d) for _d in ("cli", "pysdk", os.path.join("pysdk", "hermes"))]


def _scope_paths():
    """把 repo 的 cli/pysdk/pysdk/hermes 挂上 sys.path，返回撤销函数（用完即撤）。"""
    added = [p for p in _PATHS if p not in sys.path]
    for p in reversed(added):
        sys.path.insert(0, p)

    def _undo():
        for p in added:
            while p in sys.path:
                sys.path.remove(p)
    return _undo


_undo = _scope_paths()
import ensure_config  # noqa: E402
import aimail_contract as _contract  # noqa: E402 — 契约常量单一真源(值 = 清单)
_undo()


# aimail_base 的 6 个平台注入点（真源: pysdk/hermes/aimail_hermes.py 模块级
# `core._X = ...` 一段）。import register_profiles ⇒ import aimail_hermes ⇒
# 这 6 个全局被**永久写入**，不随 sys.path 撤销而恢复。
_CORE_INJECTION_POINTS = (
    "_PROFILE_DIR_RESOLVER", "_CONFIG_LOADER", "_PERSONAS_PROVIDER",
    "_SOUL_PROVIDER", "_SKILLS_PROVIDER", "_BOARD_GATEWAY_SINK",
)
_core_snapshot: dict = {}   # import 前快照 → 中和后清空（fixture teardown 兜底）


def _restore_core_injections():
    """把 aimail_base 注入点还原成 import register_profiles 之前的值。"""
    global _core_snapshot
    if not _core_snapshot:
        return
    import aimail_base  # noqa: F401 — 此时必已在 sys.modules(快照即由它而来)
    for _attr, _val in _core_snapshot.items():
        setattr(aimail_base, _attr, _val)
    _core_snapshot = {}


def _register_profiles():
    """延迟导入 register_profiles，并**中和**它带进来的运行期全局副作用。

    二分证据（2026-09-30，本仓实跑）:
      · 只挂 sys.path                 ⇒ 两文件同跑 2 passed（无害）
      · 挂路径 + import ensure_config ⇒ 2 passed（无害）
      · 挂路径 + **import register_profiles** ⇒ 1 failed（复现）

    污染机制（2026-09-30 定死，探针 /tmp/probe_mech.py 实测输出）:
      import register_profiles ⇒ import aimail_hermes ⇒ 模块级执行
      `core._PROFILE_DIR_RESOLVER = _resolve_profile_dir; core._CONFIG_LOADER = _load_profile_config`
      (pysdk/hermes/aimail_hermes.py:834-835)，把 aimail_base 里默认为 None 的
      平台注入点**永久写上**。于是 `aimail_tools._aimail_dir()` 依赖的
      `resolve_system_id_for_email()` 第 3 步（平台指针兜底）开始命中本机真实
      `~/.hermes/.agentmail`：  contract-allowed: 文字说明(非代码)引用契约指针文件名
        BEFORE: sid=''  resolver=None  loader=None
        AFTER : sid='shared-token-8872d2bb'  resolver=<_resolve_profile_dir>  loader=<_load_profile_config>
      `.search/index.db` 于是落 `systems/shared-token-…/…/mail/`，而
      test_local_mail_search 断言的 `_leaf(home)` = `systems/_unassigned/…/mail/`
      从未被创建 ⇒ `sqlite3.OperationalError: unable to open database file`
      （`search_mail()["count"]==1` 仍过，因为写读同源、一起偏移）。
      导入期那串 `… registration failed: 'NoneType' object has no attribute 'register'`
      是 hermes 自己的 registry 为 None 的噪声，与本失败**无关**（测试直调
      aimail_tools.search_mail，不经 registry）。

    中和: import 前快照 6 个注入点，import 后立即还原（`_restore_core_injections`），
    fixture teardown 再兜底一次。只动测试侧，产品码一行不改。
    延迟到用例内导入: 收集期导入会让整个进程在任何用例跑之前就被注入。"""
    global _core_snapshot
    undo = _scope_paths()
    try:
        import aimail_base  # 挂好路径后先快照(此时注入点仍是干净值)
        _core_snapshot = {a: getattr(aimail_base, a, None)
                          for a in _CORE_INJECTION_POINTS}
        import register_profiles  # noqa: E402
        return register_profiles
    finally:
        _restore_core_injections()
        undo()

_TOOLSET = _contract.AGENT_TOOLSET_NAME  # 契约常量(清单 agent_skill_name / agent_toolset_name)


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _toolsets(cfg: dict) -> dict:
    return cfg.get("platform_toolsets") or {}


# ── 1. 工具集未注册 ⇒ ensure 后已注册 ──────────────────────────────────────
@pytest.fixture(autouse=True)
def _restore_hermes_env(monkeypatch):
    """双保险(2026-09-30 定死污染后加):
    1) register_emails() 会**直接写** os.environ(HERMES_PROFILE_DIR=…, 不走
       monkeypatch)。泄漏后后续测试读到指向已删除 tmp 目录的值。
       monkeypatch.delenv 会在用例结束后**还原原值**(含原本不存在的情况)。
    2) import register_profiles 注入的 aimail_base 平台注入点(见
       _register_profiles docstring 的 BEFORE/AFTER 证据)在用例结束后**再兜底
       还原一次** —— 正常路径下 _register_profiles 的 finally 已还原, 这里只防
       用例体内又发生注入。"""
    monkeypatch.delenv("HERMES_PROFILE_DIR", raising=False)
    yield
    _restore_core_injections()


def test_ensure_registers_toolset_when_config_exists_but_toolset_missing(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({"model": {"default": "m"}}), encoding="utf-8")

    changes = ensure_config.ensure_profile_config(tmp_path)

    cfg = _read(cfg_path)
    pt = _toolsets(cfg)
    assert _TOOLSET in (pt.get("webhook") or []), changes
    assert _TOOLSET in (pt.get("cli") or []), changes
    assert cfg["platforms"]["webhook"]["enabled"] is True
    # 既有键不被覆盖(幂等语义: 只补缺失)
    assert cfg["model"] == {"default": "m"}


def test_ensure_creates_config_yaml_when_missing(tmp_path):
    """首次 install 时 hermes 还没生成 config.yaml —— 不许再 "skipped"。"""
    cfg_path = tmp_path / "config.yaml"
    assert not cfg_path.exists()

    changes = ensure_config.ensure_profile_config(tmp_path)

    assert cfg_path.exists(), changes
    cfg = _read(cfg_path)
    pt = _toolsets(cfg)
    assert _TOOLSET in (pt.get("webhook") or []), changes
    assert _TOOLSET in (pt.get("cli") or []), changes
    assert cfg["platforms"]["webhook"]["enabled"] is True
    # 文件创建必须出现在 changes 里(可观测, 不静默)
    assert any(str(cfg_path) in c for c in changes)


# ── 2. 已注册 ⇒ 不重复写 ───────────────────────────────────────────────────
def test_ensure_is_idempotent_when_already_registered(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({"model": {"default": "m"}}), encoding="utf-8")
    first = ensure_config.ensure_profile_config(tmp_path)
    assert any("platform_toolsets" in c for c in first)

    before = cfg_path.read_bytes()
    second = ensure_config.ensure_profile_config(tmp_path)

    assert second == [], second
    assert cfg_path.read_bytes() == before


def test_ensure_keeps_existing_webhook_secret(tmp_path):
    """secret 变更会致 bridge 转发 HMAC 401 —— 已存在的必须原样保留。"""
    secret = "s" * 64
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "platforms": {"webhook": {"enabled": True, "port": 8644,
                                  "extra": {"port": 8644, "secret": secret}}},
    }), encoding="utf-8")

    ensure_config.ensure_profile_config(tmp_path)

    cfg = _read(cfg_path)
    assert cfg["platforms"]["webhook"]["extra"]["secret"] == secret
    assert _TOOLSET in (_toolsets(cfg).get("webhook") or [])


# ── 3. 写入路径与读取路径同源(真 glob, 非肉眼比对) ────────────────────────
def test_write_path_is_the_file_check_reads(tmp_path):
    reg = json.loads((_REPO / "cli" / "platforms.json").read_text(encoding="utf-8"))
    checks = reg["platforms"]["hermes"]["health_checks"]
    toolsets_check = next(c for c in checks if c.get("kind") == "yaml_toolsets")

    # 默认 profile: 写 <HERMES_HOME>/config.yaml
    ensure_config.ensure_profile_config(tmp_path)
    # 具名 profile: 写 <HERMES_HOME>/profiles/<name>/config.yaml
    named = tmp_path / "profiles" / "p1"
    named.mkdir(parents=True)
    ensure_config.ensure_profile_config(named)

    matched = set()
    for pattern in toolsets_check["configs"]:
        matched.update(glob.glob(pattern.replace("{home}", str(tmp_path))))

    for written in (tmp_path / "config.yaml", named / "config.yaml"):
        assert str(written) in matched, (
            f"写入的 {written} 不在 check 的读取 glob {toolsets_check['configs']} 里"
        )
        pt = _toolsets(_read(written))
        assert _TOOLSET in (pt.get("webhook") or []) or _TOOLSET in (pt.get("cli") or [])


# ── 4. 注册标记短路 ⇒ registered:0 之后配置照样补上(核心回归) ─────────────
def test_register_emails_ensures_even_when_pointer_matches(tmp_path, monkeypatch, capsys):
    register_profiles = _register_profiles()  # 延迟导入，见 _register_profiles 注释
    home = tmp_path / "hermes-home"
    home.mkdir()
    profiles = home / "profiles"
    named = profiles / "p1"
    named.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": "m"}}), encoding="utf-8")

    system_id = "sid-iso17"
    # 指针已匹配 ⇒ 旧逻辑整段跳过(iso16: registered:0)
    (home / _contract.POINTER_FILE).write_text(json.dumps({"system_id": system_id}))
    (named / _contract.POINTER_FILE).write_text(json.dumps({"system_id": system_id}))

    registered = []
    monkeypatch.setattr(register_profiles, "load_gateway_config",
                        lambda: {"system_id": system_id, "admin_key": "k"})
    monkeypatch.setattr(register_profiles.aimail_hermes, "_auto_register_email",
                        lambda name, profile_dir, cfg: registered.append(name))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILES_DIR", str(profiles))

    register_profiles.register_emails()

    out = capsys.readouterr().out
    # 注册幂等语义保持: 指针匹配 ⇒ 不重注册
    assert "registered:0" in out, out
    assert registered == []

    # 但配置补全必须真跑了(旧 bug: 这里什么都没写)
    for cfg_dir in (home, named):
        pt = _toolsets(_read(cfg_dir / "config.yaml"))
        assert _TOOLSET in (pt.get("webhook") or []), cfg_dir
        assert _TOOLSET in (pt.get("cli") or []), cfg_dir


def test_register_emails_creates_config_for_fresh_home(tmp_path, monkeypatch, capsys):
    """全新 HOME(无 config.yaml、无指针): install 一次就得让会话拿到工具集。"""
    register_profiles = _register_profiles()  # 延迟导入，见 _register_profiles 注释
    home = tmp_path / "fresh-home"
    home.mkdir()
    system_id = "sid-fresh"
    monkeypatch.setattr(register_profiles, "load_gateway_config",
                        lambda: {"system_id": system_id, "admin_key": "k"})
    monkeypatch.setattr(register_profiles.aimail_hermes, "_auto_register_email",
                        lambda name, profile_dir, cfg: None)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILES_DIR", str(home / "profiles"))

    register_profiles.register_emails()

    out = capsys.readouterr().out
    assert "registered:1" in out, out
    pt = _toolsets(_read(home / "config.yaml"))
    assert _TOOLSET in (pt.get("webhook") or []), pt
    assert _TOOLSET in (pt.get("cli") or []), pt

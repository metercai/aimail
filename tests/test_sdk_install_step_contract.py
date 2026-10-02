"""D5 (owner ruling 2026-10-02): dsh 装配步迁 sdk_install 的注册表与执行器契约。

- 注册表: platforms.json dsh install_steps[1] = sdk_install/fn=install_dsh,
  on_error=fail(保原 spawn "失败即中止"), needs_manager=false(装配步不注册、
  不写白名单 ⇒ P1 manager 硬门不适用)。
- 执行器: 两处新字段的语义(默认值向后兼容 hermes/deerflow 既有步)。
"""
import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "cli" / "aimail"
if str(_CLI.parent) not in sys.path:
    sys.path.insert(0, str(_CLI.parent))


def _load(name):
    loader = SourceFileLoader(name, str(_CLI))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


def test_registry_dsh_assembly_step_shape():
    cfg = json.loads((_REPO / "cli" / "platforms.json").read_text(encoding="utf-8"))
    plats = cfg["platforms"] if "platforms" in cfg else cfg
    st = plats["dsh"]["install_steps"][1]
    assert st["kind"] == "sdk_install"
    assert st["fn"] == "install_dsh"
    assert st["on_error"] == "fail"
    assert st["needs_manager"] is False
    # 步#1(dsh plugin add)仍是 CLI 安装步, 不动
    assert plats["dsh"]["install_steps"][0]["kind"] == "spawn"


def _run_step(tmp_path, monkeypatch, step, rc, *, manager=""):
    cli = _load(f"aimail_cli_d5_{str(abs(hash(str(tmp_path) + str(step.get('fn')))))[:10]}")
    calls = {"require_mgr": 0, "sdk": [], "ok": [], "warn": []}
    monkeypatch.setattr(cli, "_platform_def",
                        lambda p: {"install_steps": [step]})
    monkeypatch.setattr(cli, "_sdk_install",
                        lambda target, home, sid, fn="", manager="":
                        calls["sdk"].append((target, fn, manager)) or rc)
    monkeypatch.setattr(cli, "_require_mgr",
                        lambda *a, **k: calls.__setitem__(
                            "require_mgr", calls["require_mgr"] + 1) or "mgr@x.tm")
    monkeypatch.setattr(cli, "_ok", lambda m: calls["ok"].append(str(m)))
    monkeypatch.setattr(cli, "_warn", lambda m: calls["warn"].append(str(m)))

    def _fail(m, *a, **k):
        raise SystemExit(f"_fail: {m}")
    monkeypatch.setattr(cli, "_fail", _fail)
    ctx = {"home": str(tmp_path), "sid": "s1", "cfg": {"gateway_url": "https://g"},
           "manager": manager}
    cli._run_install_steps("dsh", ctx)
    return calls


def test_needs_manager_false_skips_the_p1_hard_gate(tmp_path, monkeypatch):
    step = {"kind": "sdk_install", "target": "dsh", "fn": "install_dsh",
            "on_error": "fail", "needs_manager": False, "ok_text": "assembled"}
    calls = _run_step(tmp_path, monkeypatch, step, 0)   # rc=0 + 空 manager 不得 _fail
    assert calls["require_mgr"] == 0, "装配步不得触发 manager 硬门"
    assert calls["sdk"] and calls["sdk"][0][:2] == ("dsh", "install_dsh")


def test_needs_manager_default_keeps_the_hard_gate(tmp_path, monkeypatch):
    step = {"kind": "sdk_install", "target": "hermes", "fn": "install_hermes",
            "ok_text": "x"}   # 无 needs_manager ⇒ 默认 true(hermes/deerflow 现状)
    calls = _run_step(tmp_path, monkeypatch, step, 0)
    assert calls["require_mgr"] == 1
    assert calls["sdk"][0][2] == "mgr@x.tm"   # manager 传给 SDK


def test_on_error_fail_aborts_install_on_nonzero_rc(tmp_path, monkeypatch):
    step = {"kind": "sdk_install", "target": "dsh", "fn": "install_dsh",
            "on_error": "fail", "needs_manager": False,
            "fail_hint": "assembly failed (exit {rc})"}
    try:
        _run_step(tmp_path, monkeypatch, step, 1)
    except SystemExit as e:
        assert "assembly failed (exit 1)" in str(e)
    else:
        raise AssertionError("on_error=fail + rc=1 必须中止(_fail)")


def test_without_on_error_nonzero_is_reported_not_fatal(tmp_path, monkeypatch):
    step = {"kind": "sdk_install", "target": "hermes", "fn": "install_hermes",
            "ok_text": "done"}
    calls = _run_step(tmp_path, monkeypatch, step, 1)   # 旧语义: 不中止
    assert any("see warnings" in m for m in calls["ok"])

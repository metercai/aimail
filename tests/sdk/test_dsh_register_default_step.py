"""dsh 必须补一个与 pi **同形**的 register_default 步(2026-09-29)。

背景:dsh 的 install_steps 原来只有 spawn / print / print —— 装完插件后**不注册**,
默认 agent 只能靠人工跑 `aimail address -a agent`;pi 已有 register_default,
`aimail install` 即装即注册。修 = 照抄 pi 的节点形状(kind / when / on_error /
warn_hint,含它已有的 on_error 与提示字段)补给 dsh,不自创结构。

判决项 = 两平台该节点的**字段集合相同**(形状一致),且排在插件确保步(spawn)之后
(先装插件才能注册)。
"""
import json
import pathlib

_REG = json.loads((pathlib.Path(__file__).resolve().parent.parent
                   / "cli" / "platforms.json").read_text())


def _steps(name: str) -> list:
    return _REG["platforms"][name]["install_steps"]


def _register_default(name: str) -> dict:
    hits = [s for s in _steps(name) if s.get("kind") == "register_default"]
    assert len(hits) == 1, f"{name} 应恰好有一个 register_default 步, 实际 {len(hits)}"
    return hits[0]


def test_dsh_has_register_default_step():
    st = _register_default("dsh")
    assert st["on_error"] == "warn"
    assert st["warn_hint"], "失败提示字段必须在(注册失败不许静默)"


def test_dsh_register_default_shape_matches_pi():
    """形状对齐:顶层字段集合与 when 子字段集合都必须和 pi 一致。"""
    dsh, pi = _register_default("dsh"), _register_default("pi")
    assert set(dsh) == set(pi), (
        f"register_default 节点字段不同: dsh={sorted(dsh)} pi={sorted(pi)}")
    assert set(dsh["when"]) == set(pi["when"])
    assert dsh["when"] == {"cfg_complete": True}, "cfg 未就绪时应跳过(与 pi 同条件)"
    assert dsh["on_error"] == pi["on_error"] == "warn"
    # 提示字段是模板形态:两平台都用 {sid} 占位
    assert "{sid}" in dsh["warn_hint"] and "{sid}" in pi["warn_hint"]


def test_dsh_register_default_runs_after_plugin_ensure():
    """确保步(spawn)必须在注册之前:插件没装上就注册必失败。"""
    kinds = [s.get("kind") for s in _steps("dsh")]
    assert "register_default" in kinds
    assert kinds.index("spawn") < kinds.index("register_default"), kinds

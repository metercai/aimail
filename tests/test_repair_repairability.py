"""repair 的可修复性收口契约测试(2026-09-20 用户定调)。

保证:
1) 每个 check 维度都在 REPAIRABILITY 清单里(不留"没交代"的项);
2) 未登记维度按 HINT 处理且给出原因(不静默);
3) 复检残留按 auto/hint 正确分组(缺陷 vs 需管理员介入);
4) 缺失的 pull 条目可本地创建(不依赖服务端)。
"""
import ast
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))

import repair  # noqa: E402


def _check_dimensions() -> set:
    """从 check_status.py 抽取字面量维度 (level, name)。"""
    src = (ROOT / "cli" / "check_status.py").read_text()
    tree = ast.parse(src)
    dims = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add" and len(node.args) >= 2):
            a, b = node.args[0], node.args[1]
            if isinstance(a, ast.Constant) and isinstance(b, ast.Constant):
                dims.add((a.value, b.value))
    return dims


def test_manifest_covers_every_check_dimension():
    dims = _check_dimensions()
    assert dims, "未解析到 check 维度(抽取逻辑失效?)"
    missing = sorted(d for d in dims if d not in repair.REPAIRABILITY)
    assert not missing, f"这些 check 维度未在收口清单里登记: {missing}"


def test_every_entry_is_wellformed():
    for dim, info in repair.REPAIRABILITY.items():
        assert info.get("kind") in ("auto", "hint"), dim
        if info["kind"] == "auto":
            assert isinstance(info.get("step"), int), f"{dim} 缺 step"
        else:
            assert info.get("why"), f"{dim} 是 hint 但没写 why(必须给提示原因)"


def test_unregistered_dimension_is_hint_with_reason():
    info = repair.repairability("nope", "unknown-dim")
    assert info["kind"] == "hint" and info.get("why")


def test_classify_residual_splits_defect_and_hint():
    residual = [
        {"level": "bridge", "check": "routes-entry", "detail": "x"},   # auto
        {"level": "gateway", "check": "health", "detail": "y"},        # hint
        {"level": "whatever", "check": "unregistered", "detail": "z"},  # 未登记 → hint
    ]
    defects, hints = repair.classify_residual(residual)
    assert [c["check"] for c, _ in defects] == ["routes-entry"]
    assert sorted(c["check"] for c, _ in hints) == ["health", "unregistered"]
    assert all(i.get("why") for _, i in hints)


def test_pull_entry_created_locally_when_missing(tmp_path, monkeypatch):
    """缺失 pull 条目 → 本地创建(aimail_url/admin_key/system_id 取自 gateway.json)。"""
    sid = "unit-sid"
    sysdir = tmp_path / "systems" / sid
    sysdir.mkdir(parents=True)
    (sysdir / "aimail_gateway.json").write_text(json.dumps({
        "admin_key": "AK-TEST", "gateway_url": "http://127.0.0.1:39999",
        "system_id": sid,
    }))
    bridge_cfg = tmp_path / "bridge.toml"
    bridge_cfg.write_text('bind = "127.0.0.1:38081"\nmode = "pull"\n\n[pull]\nsystems = []\n')
    monkeypatch.setattr(repair, "SYSTEMS_DIR", tmp_path / "systems")
    monkeypatch.setattr(repair, "BRIDGE_CFG", bridge_cfg)

    assert repair._repair_pull_entry_key(sid) is True
    txt = bridge_cfg.read_text()
    assert "AK-TEST" in txt and sid in txt and "39999" in txt
    # 幂等: 再跑一次不应重复写入
    assert repair._repair_pull_entry_key(sid) is False

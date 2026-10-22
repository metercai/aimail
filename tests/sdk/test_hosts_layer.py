# -*- coding: utf-8 -*-
"""tests/sdk/test_hosts_layer.py — Python 宿主识别层(移植 tssdk 三件套)。

覆盖全组合:
  registry:  键集合 / 未知 id ⇒ None
  detect:    单宿主命中 / 零命中 / 双宿主歧义 / 父目录候选根 / 指针≠marker
  activate:  verified(恰一信号) / mismatch 只 warn 不抛 / 未知 declared /
             多宿主共存 consistent / 空 home
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pysdk.hosts import activate, detect, registry  # noqa: E402


@pytest.fixture()
def hermes_home(tmp_path):
    """~/.hermes 形态:hermes-agent marker 在位。"""
    home = tmp_path / ".hermes"
    (home / "hermes-agent" / "gateway").mkdir(parents=True)
    return str(tmp_path)


@pytest.fixture()
def deerflow_home(tmp_path):
    """~/.deer-flow 形态:backend/app/gateway marker 在位。"""
    home = tmp_path / ".deer-flow"
    (home / "backend" / "app" / "gateway").mkdir(parents=True)
    return str(tmp_path)


def _make_marker_home(tmp_path, host_id):
    """按 registry 规格建一个 marker 在位的宿主树,返回父目录(home 的 parent)。"""
    spec = registry.get_host(host_id)
    assert spec is not None, f"registry 应有 {host_id}"
    home = tmp_path / spec["home_dir"]
    marker_path = home / spec["marker"]
    os.makedirs(marker_path, exist_ok=True)
    return str(tmp_path)


# ── registry ──────────────────────────────────────────────────────────────

def test_registry_keys_are_the_two_python_hosts():
    assert registry.host_ids() == ["deerflow", "hermes"]


def test_registry_fields_align_with_cli_host_table():
    """字段纪律:与 cli/src/hosts/{hermes,deerflow}.rs 逐字段一致(单真源)。"""
    h = registry.get_host("hermes")
    assert h is not None
    assert h["home_dir"] == ".hermes"
    assert h["marker"] == "hermes-agent"
    d = registry.get_host("deerflow")
    assert d is not None
    assert d["home_dir"] == ".deer-flow"
    assert d["marker"] == "backend/app/gateway"


def test_get_host_unknown_returns_none():
    assert registry.get_host("nope") is None
    assert registry.get_host("") is None


# ── detect ────────────────────────────────────────────────────────────────

def test_detect_single_host_hit(hermes_home):
    assert detect.detect_host(hermes_home) == "hermes"
    assert detect.detect_hosts_present(hermes_home) == ["hermes"]


def test_detect_zero_hits(tmp_path):
    assert detect.detect_host(str(tmp_path)) is None
    assert detect.detect_hosts_present(str(tmp_path)) == []


def test_detect_ambiguous_returns_none_for_single(tmp_path):
    """双宿主 marker 共存 ⇒ detect_host 给 None(歧义不猜),present 给全量。"""
    _make_marker_home(tmp_path, "hermes")
    _make_marker_home(tmp_path, "deerflow")
    assert detect.detect_host(str(tmp_path)) is None
    assert detect.detect_hosts_present(str(tmp_path)) == ["deerflow", "hermes"]


def test_detect_parent_dir_candidate_root(tmp_path):
    """调用方给父目录(~ 式)⇒ 候选根含 <parent>/.hermes 命中。"""
    parent = _make_marker_home(tmp_path, "hermes")
    assert detect.detect_host(parent) == "hermes"


def test_pointer_alone_is_not_a_marker_hit(tmp_path):
    """.agentmail 指针存在但 marker 不在 ⇒ 不算该宿主 present(指针是弱证据)。"""
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / ".agentmail").write_text("{}")
    assert detect.detect_hosts_present(str(tmp_path)) == []


# ── activate ──────────────────────────────────────────────────────────────

def test_activate_verified_on_exactly_one_signal(hermes_home, capsys):
    r = activate.activate_host("hermes", hermes_home)
    assert r["declared"] == "hermes"
    assert r["known"] is True
    assert r["detected"] == "hermes"
    assert r["present"] == ["hermes"]
    assert r["verified"] is True
    assert r["consistent"] is True
    assert "unknown declared" not in capsys.readouterr().err


def test_activate_mismatch_warns_but_never_raises(hermes_home, capsys):
    """declared=deerflow 但环境是 hermes ⇒ consistent=False,只 warn 不抛。"""
    r = activate.activate_host("deerflow", hermes_home)
    assert r["known"] is True
    assert r["detected"] == "hermes"
    assert r["verified"] is False
    assert r["consistent"] is False
    err = capsys.readouterr().err
    assert "not detected at" in err
    assert "present: ['hermes']" in err


def test_activate_unknown_declared_never_raises(tmp_path, capsys):
    r = activate.activate_host("nope", str(tmp_path))
    assert r["known"] is False
    assert r["verified"] is False
    assert r["consistent"] is False
    assert "unknown declared host" in capsys.readouterr().err


def test_activate_multi_host_still_consistent_for_declared(tmp_path):
    """双宿主共存 ⇒ declared 那个 consistent=True,verified=False(歧义不 verified)。"""
    _make_marker_home(tmp_path, "hermes")
    _make_marker_home(tmp_path, "deerflow")
    r = activate.activate_host("hermes", str(tmp_path))
    assert r["consistent"] is True
    assert r["verified"] is False
    assert r["present"] == ["deerflow", "hermes"]


def test_activate_empty_home_never_raises(tmp_path, capsys):
    r = activate.activate_host("hermes", "")
    assert r["present"] == []
    assert r["consistent"] is False
    assert r["verified"] is False


def test_activate_empty_declared_never_raises(hermes_home, capsys):
    r = activate.activate_host("", hermes_home)
    assert r["known"] is False
    assert r["consistent"] is False


# ── 单真源漂移守护 ────────────────────────────────────────────────────────

def test_sdk_ops_subdir_list_matches_registry():
    """sdk_ops.py 的 sys.path 挂载目录集必须 = 注册表 adapter_dir 集。

    sdk_ops 是 spawn 入口,自举保持最小依赖面(不 import 注册表),
    故目录集是硬编码 —— 本测试防"注册表加了宿主、sdk_ops 漏挂"的漂移。
    """
    import re

    # tests/sdk/test_hosts_layer.py → 仓根(与 conftest._REPO 同约定:三层 dirname)
    core = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = open(os.path.join(core, "pysdk", "sdk_ops.py")).read()
    m = re.search(r'for _sub in \(([^)]*)\):', src)
    assert m, "sdk_ops.py 的挂载循环丢失(格式变更?)"
    declared = set(re.findall(r'"([^"]+)"', m.group(1)))
    expected = set()
    for h in registry.host_ids():
        spec = registry.get_host(h)
        assert spec is not None
        expected.add(spec["adapter_dir"])
    assert declared == expected, f"sdk_ops 目录集 {declared} ≠ 注册表 adapter_dir {expected}"

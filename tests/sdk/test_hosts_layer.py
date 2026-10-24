# -*- coding: utf-8 -*-
"""tests/sdk/test_hosts_layer.py — Python host identification layer (ported tssdk trio).

Covers all combinations:
  registry:  key set / unknown id ⇒ None
  detect:    single host hit / zero hit / dual host ambiguity / parent directory candidate root / pointer≠marker
  activate:  verified (exactly one signal) / mismatch only warn no raise / unknown declared /
             multi-host coexistence consistent / empty home
"""
import os
import sys
import importlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pysdk.hosts import activate, detect, registry  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def hermes_home(tmp_path):
    """~/.hermes form: hermes-agent marker in place."""
    home = tmp_path / ".hermes"
    (home / "hermes-agent" / "gateway").mkdir(parents=True)
    return str(tmp_path)


@pytest.fixture()
def deerflow_home(tmp_path):
    """~/.deer-flow form: backend/app/gateway marker in place."""
    home = tmp_path / ".deer-flow"
    (home / "backend" / "app" / "gateway").mkdir(parents=True)
    return str(tmp_path)


def _make_marker_home(tmp_path, host_id):
    """Build a host tree with the marker in place per the registry spec; return the parent directory (home's parent)."""
    spec = registry.get_host(host_id)
    assert spec is not None, f"registry should have {host_id}"
    home = tmp_path / spec["home_dir"]
    marker_path = home / spec["marker"]
    os.makedirs(marker_path, exist_ok=True)
    return str(tmp_path)


# ── registry ──────────────────────────────────────────────────────────────

def test_registry_keys_are_the_two_python_hosts():
    assert registry.host_ids() == ["deerflow", "hermes"]


def test_registry_fields_align_with_cli_host_table():
    """Field discipline: consistent field-by-field with cli/src/hosts/{hermes,deerflow}.rs (single source of truth)."""
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
    """Dual host markers coexist ⇒ detect_host returns None (ambiguity not guessed), present returns full set."""
    _make_marker_home(tmp_path, "hermes")
    _make_marker_home(tmp_path, "deerflow")
    assert detect.detect_host(str(tmp_path)) is None
    assert detect.detect_hosts_present(str(tmp_path)) == ["deerflow", "hermes"]


def test_detect_parent_dir_candidate_root(tmp_path):
    """Caller gives parent directory (~ style) ⇒ candidate roots include <parent>/.hermes hit."""
    parent = _make_marker_home(tmp_path, "hermes")
    assert detect.detect_host(parent) == "hermes"


def test_pointer_alone_is_not_a_marker_hit(tmp_path):
    """POINTER_FILE pointer exists but marker not present ⇒ not counted as that host present (pointer is weak evidence)."""
    from aimail_contract import POINTER_FILE
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / POINTER_FILE).write_text("{}")
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
    """declared=deerflow but environment is hermes ⇒ consistent=False, only warn no raise."""
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
    """Dual host coexistence ⇒ declared one consistent=True, verified=False (ambiguity not verified)."""
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


# ── Single source of truth drift guard ────────────────────────────────────────────────────────

def test_sdk_ops_subdir_list_matches_registry():
    """sdk_ops.py sys.path mount directory set must = registry adapter_dir set.

    sdk_ops is the spawn entry, bootstrap keeps minimal dependency surface (no import registry),
    so directory set is hardcoded —— this test prevents drift where "registry added host, sdk_ops missed mount".
    """
    import re

    # tests/sdk/test_hosts_layer.py → repo root (same convention as conftest._REPO: three-level dirname)
    core = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = open(os.path.join(core, "pysdk", "sdk_ops.py")).read()
    m = re.search(r'for _sub in \(([^)]*)\):', src)
    assert m, "sdk_ops.py mount loop lost (format change?)"
    declared = set(re.findall(r'"([^"]+)"', m.group(1)))
    expected = set()
    for h in registry.host_ids():
        spec = registry.get_host(h)
        assert spec is not None
        expected.add(spec["adapter_dir"])
    assert declared == expected, f"sdk_ops directory set {declared} ≠ registry {expected}"


def _import_entry_fresh(name, adapter):
    """Force fresh execution of entry module (clear import cache —— _HOST_VERIFY at module top level
    calculated based on current env, cache would make subsequent tests get previous determination).
    Returns (module, cleanup)."""
    sys.path.insert(0, os.path.join(_REPO, "pysdk"))
    sys.path.insert(0, os.path.join(_REPO, "pysdk", adapter))
    sys.modules.pop(name, None)

    def _cleanup():
        sys.modules.pop(name, None)
        for p in (os.path.join(_REPO, "pysdk", adapter), os.path.join(_REPO, "pysdk")):
            if p in sys.path:
                sys.path.remove(p)
    return importlib.import_module(name), _cleanup


# ── entry wiring (activate on entry load)──
def _make_verified_home(tmp_path, host_id):
    """Build a host tree with the marker in place per the registry spec; return the host home (detection candidate root)."""
    spec = registry.get_host(host_id)
    assert spec is not None
    home = tmp_path / spec["home_dir"]
    marker_path = home / spec["marker"]
    marker_path.mkdir(parents=True, exist_ok=True)
    (marker_path / "x.txt").write_text("x")
    return str(home)


def test_entry_wiring_hermes_verified(tmp_path, monkeypatch):
    """hermes entry wiring: HERMES_HOME points to real hermes tree ⇒ import time _HOST_VERIFY
    Must be verified=True (activate on entry load, isomorphic to tssdk register-cli)."""
    monkeypatch.setenv("HERMES_HOME", _make_verified_home(tmp_path, "hermes"))
    m, cleanup = _import_entry_fresh("aimail_hermes", "hermes")
    try:
        assert m._HOST_VERIFY["verified"] is True
        assert m._HOST_VERIFY["detected"] == "hermes"
    finally:
        cleanup()


def test_entry_wiring_hermes_mismatch_never_raises(tmp_path, monkeypatch):
    """hermes entry installed on wrong host (empty home) ⇒ import does not raise (never raise),
    consistent=False + warn —— partial failure must be visible but not block the host."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))  # empty directory, no marker
    m, cleanup = _import_entry_fresh("aimail_hermes", "hermes")
    try:
        assert m._HOST_VERIFY["consistent"] is False
        assert m._HOST_VERIFY["verified"] is False
    finally:
        cleanup()


def test_entry_wiring_deerflow_verified(tmp_path, monkeypatch):
    """deerflow entry wiring: DEER_FLOW_HOME points to real deer-flow tree ⇒ verified=True."""
    monkeypatch.setenv("DEER_FLOW_HOME", _make_verified_home(tmp_path, "deerflow"))
    m, cleanup = _import_entry_fresh("aimail_deerflow", "deer-flow")
    try:
        assert m._HOST_VERIFY["verified"] is True
        assert m._HOST_VERIFY["detected"] == "deerflow"
    finally:
        cleanup()

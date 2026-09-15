"""Program-root layout + runtime payload (MCP) contract baseline.

Pins the 2026-09-15 layout decision: installed programs live under ONE root
(`~/.aimail/bin`): the program copy (`aimail-src/`, what PATH's `aimail`
points at) and the host runtime payload (`mcp/`, version-stamped). bootstrap
derives the payload from the same snapshot, so the two copies cannot drift —
and `check` reports it if they do (payload staleness, host reference breakage).
"""
import json
import os

import pytest

from check_status import Check, _check_payload, _check_payload_refs
import runtime_bundle
import runtime_core

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PYSDK = os.path.join(_REPO, "pysdk")


@pytest.fixture()
def prog(tmp_path, monkeypatch):
    """Program root pointed at a throwaway dir (nothing touches the real one)."""
    monkeypatch.delenv("AIMAIL_PROG_DIR", raising=False)
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "aimail"))
    return tmp_path / "aimail" / "bin"


# ── layout single source ─────────────────────────────────────────

def test_program_root_defaults_under_aimail_home(prog):
    assert runtime_core.program_root() == str(prog)
    assert runtime_core.toolkit_dir() == str(prog / "aimail-src")


def test_program_root_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMAIL_PROG_DIR", str(tmp_path / "custom"))
    assert runtime_core.program_root() == str(tmp_path / "custom")
    assert runtime_core.toolkit_dir() == str(tmp_path / "custom" / "aimail-src")


def test_mcp_payload_dir_follows_program_root(prog):
    # the payload is a sibling of the program copy, not a separate root
    assert runtime_bundle.payload_dir("mcp") == str(prog / "mcp")


def test_host_bundles_keep_their_own_dests(prog):
    # only the mcp payload lives in the program root; host bundles do not
    assert runtime_bundle.payload_dir("deer-flow").endswith("routers")


# ── payload state ────────────────────────────────────────────────

def test_snapshot_version_falls_back_to_pyproject(tmp_path):
    # bootstrap installs a tar snapshot (no .git): the stamp must carry the
    # package version, not a meaningless "dev"
    (tmp_path / "pysdk").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "aimailsdk"\nversion = "0.1.12rc2"\n', encoding="utf-8")
    assert runtime_bundle._source_version(str(tmp_path / "pysdk"), "repo") == "0.1.12rc2"


def test_payload_state_absent(tmp_path):
    st = runtime_bundle.payload_state("mcp", dest=str(tmp_path / "nothing"))
    assert st["present"] is False
    assert st["missing"] == [] and st["stale"] == []


def test_payload_state_fresh_then_stale_and_missing(tmp_path):
    dest = str(tmp_path / "payload")
    rc = runtime_bundle.install("mcp", dest=dest, source_root=_PYSDK)
    assert rc == 0

    st = runtime_bundle.payload_state("mcp", dest=dest)
    assert st["present"] is True
    assert st["missing"] == [] and st["stale"] == []
    assert "aimail_mcp_server.py" in st["files"]

    # drift: a payload file no longer matches the runtime the CLI itself uses
    with open(os.path.join(dest, "aimail_base.py"), "a", encoding="utf-8") as f:
        f.write("\n# drift\n")
    assert runtime_bundle.payload_state("mcp", dest=dest)["stale"] == ["aimail_base.py"]

    # missing: stamp declares it, the file is gone
    os.remove(os.path.join(dest, "aimail_tools.py"))
    st2 = runtime_bundle.payload_state("mcp", dest=dest)
    assert st2["missing"] == ["aimail_tools.py"]
    assert st2["stale"] == ["aimail_base.py"]


# ── check rows ───────────────────────────────────────────────────

def test_check_payload_row_reports_drift(prog, tmp_path, monkeypatch):
    dest = prog / "mcp"
    assert runtime_bundle.install("mcp", dest=str(dest), source_root=_PYSDK) == 0

    c = Check()
    _check_payload(c)
    assert [r["check"] for r in c.checks] == ["mcp-payload"]
    assert c.checks[0]["pass"] is True
    assert c.checks[0]["level"] == "runtime"
    assert f"v{runtime_bundle._source_version(_PYSDK, 'repo')}" in c.checks[0]["detail"]

    with open(dest / "aimail_base.py", "a", encoding="utf-8") as f:
        f.write("\n# drift\n")
    c2 = Check()
    _check_payload(c2)
    assert c2.checks[0]["pass"] is False
    assert "stale: aimail_base.py" in c2.checks[0]["detail"]
    assert "install mcp" in c2.checks[0]["fix"]


def test_check_payload_silent_when_not_installed(prog):
    # no payload on this machine = no row (never a phantom failure)
    c = Check()
    _check_payload(c)
    assert c.checks == []


def _registry(refs):
    return {"order": ["deerflow"], "platforms": {"deerflow": {"payload_refs": refs}}}


def test_check_payload_refs_flags_renamed_file(prog, tmp_path, monkeypatch):
    dest = prog / "mcp"
    assert runtime_bundle.install("mcp", dest=str(dest), source_root=_PYSDK) == 0
    home = tmp_path / "deer-flow"
    home.mkdir()
    cfg = home / "extensions_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"aimail": {"args": [
        str(dest / "aimail_mcp_server.py")]}}}), encoding="utf-8")
    monkeypatch.setattr("check_status._load_platform_registry",
                        lambda: _registry(["{home}/extensions_config.json"]))

    c = Check()
    _check_payload_refs(c, "deerflow", str(home))
    assert c.checks[0]["check"] == "host-payload-refs"
    assert c.checks[0]["pass"] is True

    # a stale host config still pointing at a pruned/renamed payload file
    cfg.write_text(json.dumps({"mcpServers": {"amail": {"args": [
        str(dest / "amail_mcp_server.py")]}}}), encoding="utf-8")
    c2 = Check()
    _check_payload_refs(c2, "deerflow", str(home))
    assert c2.checks[0]["pass"] is False
    assert "missing amail_mcp_server.py" in c2.checks[0]["detail"]


def test_check_payload_refs_silent_without_host_file(prog, tmp_path, monkeypatch):
    monkeypatch.setattr("check_status._load_platform_registry",
                        lambda: _registry(["{home}/extensions_config.json"]))
    c = Check()
    _check_payload_refs(c, "deerflow", str(tmp_path / "absent-home"))
    assert c.checks == []


def test_check_payload_refs_silent_for_platforms_without_refs(prog, tmp_path, monkeypatch):
    c = Check()
    _check_payload_refs(c, "hermes", str(tmp_path))
    assert c.checks == []

"""MCP server agent context parsing: pin system + loud fallback (2026-10-02, second half of J4e root cause).

 The original `_agent_ctx()` implementation only scanned all systems by agent_id (`aimail_base._scan_systems_for_agent`
 **first match** by directory order) — multiple systems under the same home often write the same agent_id (measured with this repo's fixtures:
journey system and shared domain system both have `agent_id=default`), first match may pick **another address**.
Installation side now writes `AIMAIL_SYSTEM_ID` into mcpServers.env (see
`tests/test_deerflow_mcp_env.py`), so server side must:

  * If this env exists ⇒ pin the system from installation time and then match;
  * If pinned system does not have it (binding moved / stale env) ⇒ **do not be silent**: fallback to original "all systems scan"
    behavior, and print one WARNING line to stderr (stdout carries MCP protocol frames, must not be mixed);
  * If this env does not exist ⇒ keep old behavior verbatim (zero regression).

Also pin down the positive criterion for J4e in the same file: binding is only found in the root pointed to by `AIMAIL_HOME` —— this is exactly
the root cause measured on 2026-10-02 (subprocess without AIMAIL_HOME ⇒ falls back to `~/.aimail` ⇒ throws
`agent 'default' not registered`)。
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "pysdk")  # ensure repo pysdk wins over any cli/ shadow

import aimail_base as base  # noqa: E402
import aimail_mcp_server as mcp  # noqa: E402
from aimail_contract import BINDING_FILE  # noqa: E402 — contract single source: forbid literals (hits ratchet)

AGENT = "default"


@pytest.fixture(autouse=True)
def _isolate_agent_context(monkeypatch):
    """set_agent_context mutates module globals + setdefaults into os.environ — isolate all of it."""
    saved_cfg = base._ACTIVE_AGENT_CONFIG
    saved_loader = base._CONFIG_LOADER
    saved_env = {k: v for k, v in os.environ.items() if k.startswith("AIMAIL_")}
    for k in saved_env:
        monkeypatch.delenv(k, raising=False)
    yield
    base._ACTIVE_AGENT_CONFIG = saved_cfg
    base._CONFIG_LOADER = saved_loader
    for k in [k for k in os.environ if k.startswith("AIMAIL_")]:
        os.environ.pop(k, None)
    os.environ.update(saved_env)


def _mk_home(root: Path, *systems: str) -> Path:
    """Create systems/{sid}/{addr}/binding file per shared layout (contract single source BINDING_FILE; agent_id same name, addresses differ)."""
    for sid in systems:
        addr = root / "systems" / sid / f"agent_{sid}".replace("-", "_")
        addr.mkdir(parents=True)
        (addr / BINDING_FILE).write_text(json.dumps({
            "email": f"agent@{sid}.test",
            "gateway_url": "http://127.0.0.1:34401",
            "domain": f"{sid}.test",
            "system_id": sid,
            "agent_id": AGENT,
        }))
    return root


def test_context_found_when_home_is_correct(tmp_path, monkeypatch):
    """Positive criterion (= J4e's counterfactual line B): AIMAIL_HOME points at the root holding the binding ⇒ resolution succeeds."""
    home = _mk_home(tmp_path / "aimail-home", "sys-a")
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    monkeypatch.setenv("AIMAIL_AGENT_ID", AGENT)

    assert mcp._agent_ctx() == AGENT
    assert base._ACTIVE_AGENT_CONFIG["email"] == "agent@sys-a.test"


def test_context_raises_without_aimail_home(tmp_path, monkeypatch):
    """Root cause reproduction (= counterfactual A line): no binding in the home root ⇒ original RuntimeError message."""
    real_home = _mk_home(tmp_path / "elsewhere", "sys-a")
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "empty"))  # empty root, simulates wrong root ~/.aimail
    monkeypatch.setenv("AIMAIL_AGENT_ID", AGENT)
    (tmp_path / "empty").mkdir()

    with pytest.raises(RuntimeError, match="not registered"):
        mcp._agent_ctx()
    # The real binding is elsewhere but unreadable —— same shape as the 2026-10-02 live test (for log readers to match)
    assert (real_home / "systems" / "sys-a").is_dir()


def test_pinned_system_wins_over_directory_order(tmp_path, monkeypatch):
    """Pinned system takes precedence over "first match by directory order": sys-b is pinned ⇒ take the address of sys-b."""
    home = _mk_home(tmp_path / "aimail-home", "sys-a", "sys-b")
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    monkeypatch.setenv("AIMAIL_AGENT_ID", AGENT)
    monkeypatch.setenv("AIMAIL_SYSTEM_ID", "sys-b")

    assert mcp._agent_ctx() == AGENT
    assert base._ACTIVE_AGENT_CONFIG["email"] == "agent@sys-b.test"


def test_pinned_system_miss_falls_back_loudly(tmp_path, monkeypatch, capsys):
    """Pinned system has no binding ⇒ fall back to full scan (old behavior) and stderr must have a warning (no silent failure)."""
    home = _mk_home(tmp_path / "aimail-home", "sys-a")
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    monkeypatch.setenv("AIMAIL_AGENT_ID", AGENT)
    monkeypatch.setenv("AIMAIL_SYSTEM_ID", "sys-gone")

    assert mcp._agent_ctx() == AGENT
    assert base._ACTIVE_AGENT_CONFIG["email"] == "agent@sys-a.test"
    err = capsys.readouterr().err
    assert "WARNING" in err and "sys-gone" in err, err


def test_without_pinned_system_keeps_legacy_scan(tmp_path, monkeypatch, capsys):
    """AIMAIL_SYSTEM_ID not set ⇒ exact old behavior (scan all systems, no warning)."""
    home = _mk_home(tmp_path / "aimail-home", "sys-a", "sys-b")
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    monkeypatch.setenv("AIMAIL_AGENT_ID", AGENT)

    assert mcp._agent_ctx() == AGENT
    assert base._ACTIVE_AGENT_CONFIG["email"] == "agent@sys-a.test"
    assert "WARNING" not in capsys.readouterr().err

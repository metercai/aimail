"""Unit tests for the binding write entry point on the pysdk side (owner decision A, 2026-09-28).

Layered adjudication: System-level environment files are written by CLI, per-agent binding files are written by SDK; when CLI needs to modify binding
content, it is not allowed to hold write calls ⇒ it only calls the three semantic thin functions of pysdk
(``update_binding`` / ``backfill_binding`` / ``rename_binding``), all of which internally go through the existing
``save_agent_config`` (atomic tmp+rename + 0600 semantics unchanged).

This test focuses on the **semantics** of these three thin functions, not the implementation:
  1. ``update_binding`` field-level merge + persistence (other fields preserved verbatim; file 0600)
  2. ``backfill_binding`` full persistence (repair backfill/alignment path)
  3. ``rename_binding`` = "move directory + write content" is the same thing (attachments move with the directory;
     target directory already exists ⇒ merged=True and **do not touch** source directory)
  4. Persistence format of all three is the same as ``save_agent_config`` (indent=2 + trailing newline)

File names use contract constants (``aimail_contract.BINDING_FILE``), no literals written —— contract literal ratchet
allows only reduction, not addition, by "file × key".
"""
import json
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "pysdk"))

import aimail_base as ab  # noqa: E402
from aimail_contract import BINDING_FILE  # noqa: E402

SID = "system-test0001"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolated aimail home (aimail_home() reads env each time ⇒ can be redirected)."""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    return tmp_path


def _binding_path(home: Path, email: str) -> Path:
    return home / "systems" / SID / ab._clean_agent_dir_name(email) / BINDING_FILE


def _seed(home: Path, email: str, **extra) -> dict:
    cfg = {"email": email, "system_id": SID, "gateway_url": "http://gw:38080",
           "api_key": "k-secret", "agent_id": "agent"}
    cfg.update(extra)
    ab.save_agent_config(cfg["agent_id"], cfg, SID)
    return cfg


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_update_binding_merges_fields_and_persists(home):
    email = "alice@example.test"
    _seed(home, email, manager_address="")
    p = _binding_path(home, email)
    before = json.loads(p.read_text())

    out = ab.update_binding(SID, before, {"manager_address": "mgr@example.test"})

    assert out == p, "Persistence path must still be the binding file for that address"
    after = json.loads(p.read_text())
    assert after["manager_address"] == "mgr@example.test"
    # Other fields preserved verbatim + agent_id not lost
    for k, v in before.items():
        if k != "manager_address":
            assert after[k] == v, f"{k} was modified by update_binding"
    assert after["agent_id"] == "agent"
    assert _mode(p) == 0o600
    assert p.read_text().endswith("\n") and '"manager_address"' in p.read_text()


def test_update_binding_does_not_mutate_caller_dict(home):
    email = "bob@example.test"
    cfg = _seed(home, email)
    snapshot = json.loads(json.dumps(cfg))

    ab.update_binding(SID, cfg, {"manager_address": "m2@example.test"})

    assert cfg == snapshot, "Thin function must not modify the dict passed by the caller in place"


def test_backfill_binding_writes_whole_content(home):
    email = "carol@example.test"
    p = _binding_path(home, email)

    ab.backfill_binding({"email": email, "system_id": SID, "agent_id": "a2",
                         "gateway_url": "http://gw2:38080", "domain": "example.test"}, SID)

    d = json.loads(p.read_text())
    assert d == {"email": email, "system_id": SID, "agent_id": "a2",
                 "gateway_url": "http://gw2:38080", "domain": "example.test"}
    assert _mode(p) == 0o600


def test_rename_binding_moves_dir_and_rewrites_content(home):
    old, new = "dave@example.test", "dave2@example.test"
    cfg = _seed(home, old, webhook_url="http://127.0.0.1:8799/hook")
    old_dir = _binding_path(home, old).parent
    # **Sibling files** in the directory must also move along (proving it is moving the directory, not just changing json)
    (old_dir / "role_prompt").mkdir()
    (old_dir / "role_prompt" / "10_x.md").write_text("# x\n", encoding="utf-8")

    res = ab.rename_binding(old_dir, new, {**cfg, "email": new}, SID)

    new_dir = _binding_path(home, new).parent
    assert res["moved"] is True and res["merged"] is False
    assert res["dir"] == new_dir and res["path"] == new_dir / BINDING_FILE
    assert not old_dir.exists(), "Old directory must have been moved"
    assert (new_dir / "role_prompt" / "10_x.md").is_file(), "Sibling file did not move with directory"
    d = json.loads((new_dir / BINDING_FILE).read_text())
    assert d["email"] == new and d["webhook_url"] == "http://127.0.0.1:8799/hook"
    assert d["agent_id"] == "agent"
    assert _mode(new_dir / BINDING_FILE) == 0o600


def test_rename_binding_merges_when_target_exists(home):
    old, new = "erin@example.test", "erin2@example.test"
    cfg = _seed(home, old)
    old_dir = _binding_path(home, old).parent
    new_dir = _binding_path(home, new).parent
    new_dir.mkdir(parents=True)          # Target directory exists first (old code behavior: keep two directories)
    (new_dir / "keep.md").write_text("keep\n", encoding="utf-8")

    res = ab.rename_binding(old_dir, new, {**cfg, "email": new}, SID)

    assert res["moved"] is False and res["merged"] is True
    assert old_dir.is_dir(), "Must not touch source directory when target already exists"
    assert (new_dir / "keep.md").is_file(), "Merge must not clear existing files in target directory"
    assert json.loads((new_dir / BINDING_FILE).read_text())["email"] == new

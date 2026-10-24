"""Behavioral contract for audit fixes (2026-09-21, owner ruling D2/D5/D6).

D2 Private persistence: file 0600 / directory 0700 / atomic replacement -- credentials, pointers, email body, logs unified via
   `aimail_base.atomic_write_private` / `append_private` (no longer default umask producing 0644).
D5 MCP stdio: Blank lines are no longer treated as EOF (previously one blank line silently terminated the server).
D6 Resource release: determined by content hash —— do not overwrite user-modified files, SDK upgrades (even if mtime is older) can take effect.
"""
import io
import json
import os
import stat
import sys
from pathlib import Path

import aimail_base as base
import aimail_mcp_server as mcp
import _resources_release as rr


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


# ── D2: Private persistence ───────────────────────────────────────────────────
def test_atomic_write_private_creates_0600_file_in_0700_dir(tmp_path):
    p = tmp_path / "sub" / "secret.json"
    base.atomic_write_private(p, '{"k": 1}')
    assert json.loads(p.read_text()) == {"k": 1}
    assert _mode(p) == 0o600
    assert _mode(p.parent) == 0o700
    assert not (p.parent / (p.name + ".tmp")).exists()   # No tmp residue after atomic replacement


def test_atomic_write_private_tightens_existing_wide_dir(tmp_path):
    d = tmp_path / "wide"
    d.mkdir(mode=0o755)
    base.atomic_write_private(d / "f.json", "{}")
    assert _mode(d) == 0o700


def test_atomic_write_private_keeps_host_dir_mode_when_none(tmp_path):
    """Host-owned directories (e.g., hermes profile) are not chmod'd, only ensure file is 0600."""
    d = tmp_path / "host"
    d.mkdir(mode=0o755)
    p = d / "config.yaml"
    base.atomic_write_private(p, "x: 1\n", ensure_dir_mode=None)
    assert _mode(p) == 0o600
    assert _mode(d) == 0o755


def test_append_private_appends_and_is_private(tmp_path):
    p = tmp_path / "logs" / "a.log"
    base.append_private(p, "one\n")
    base.append_private(p, "two\n")
    assert p.read_text() == "one\ntwo\n"
    assert _mode(p) == 0o600
    assert _mode(p.parent) == 0o700


def test_write_pointer_is_private_and_atomic(tmp_path):
    ptr = tmp_path / ".agentmail"
    base._write_pointer(ptr, "system-x", "a@b.tm")
    assert json.loads(ptr.read_text()) == {"system_id": "system-x", "email": "a@b.tm"}
    assert _mode(ptr) == 0o600
    assert _mode(tmp_path) == 0o700
    assert not (tmp_path / ".agentmail.tmp").exists()


# ── D5: MCP stdio blank lines ───────────────────────────────────────────
class _FakeStdin:
    def __init__(self, data: bytes):
        self.buffer = io.BytesIO(data)


def test_read_msg_skips_blank_lines_then_eof(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(b"\n   \n{\"jsonrpc\":\"2.0\",\"id\":7}\n"))
    assert mcp.read_msg() == {"jsonrpc": "2.0", "id": 7}
    assert mcp.read_msg() is None          # Only real EOF ends it


def test_read_msg_bad_frame_still_raises(monkeypatch):
    """Bad frame semantics unchanged: raises JSONDecodeError, main returns -32700 and continues serving."""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(b"not json{{{\n"))
    try:
        mcp.read_msg()
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("malformed frame should raise JSONDecodeError")


# ── D6: Resource release hash determination ─────────────────────────────────────────
def _mk_src(tmp_path: Path, text: str) -> str:
    src = tmp_path / "src"
    (src / "role_prompt").mkdir(parents=True, exist_ok=True)
    (src / "role_prompt" / "common.md").write_text(text)
    return str(src)


def _dst(tmp_path: Path) -> Path:
    return tmp_path / "home" / "systems" / "sys-1" / "board" / "role_prompt" / "common.md"


def test_release_copies_then_skips_same_content(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "home"))
    root = _mk_src(tmp_path, "v1\n")
    r1 = rr.release_resources("sys-1", root)
    assert (r1["copied"], r1["updated"], r1["skipped"]) == (1, 0, 0)
    r2 = rr.release_resources("sys-1", root)
    assert (r2["copied"], r2["updated"], r2["skipped"]) == (0, 0, 1)


def test_release_updates_when_sdk_changed_and_user_did_not(tmp_path, monkeypatch):
    """Core regression: the original mtime check would miss sends (SDK unpack timestamp earlier than target file) ⇒ now determined by hash manifest."""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "home"))
    root = _mk_src(tmp_path, "v1\n")
    rr.release_resources("sys-1", root)
    dst = _dst(tmp_path)
    os.utime(dst, (1, 1))                      # Age the target mtime (earlier than files in the package)
    (Path(root) / "role_prompt" / "common.md").write_text("v2\n")
    r = rr.release_resources("sys-1", root)
    assert r["updated"] == 1
    assert dst.read_text() == "v2\n"


def test_release_never_overwrites_personalized(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "home"))
    root = _mk_src(tmp_path, "v1\n")
    rr.release_resources("sys-1", root)
    dst = _dst(tmp_path)
    dst.write_text("my own prompt\n")          # User personalization (content != manifest record)
    (Path(root) / "role_prompt" / "common.md").write_text("v2\n")
    r = rr.release_resources("sys-1", root)
    assert r["skipped"] == 1
    assert dst.read_text() == "my own prompt\n"

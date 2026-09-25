"""审计修复的行为契约(2026-09-21, owner 裁决 D2/D5/D6)。

D2 私有落盘: 文件 0600 / 目录 0700 / 原子替换 —— 凭证、指针、邮件正文、日志统一走
   `aimail_base.atomic_write_private` / `append_private`(不再默认 umask 产出 0644)。
D5 MCP stdio: 空白行不再被当成 EOF(原来一行空白就静默终止 server)。
D6 资源释放: 按内容 hash 判定 —— 用户改过的不覆盖, SDK 升级(即便 mtime 更旧)能生效。
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


# ── D2: 私有落盘 ───────────────────────────────────────────────────
def test_atomic_write_private_creates_0600_file_in_0700_dir(tmp_path):
    p = tmp_path / "sub" / "secret.json"
    base.atomic_write_private(p, '{"k": 1}')
    assert json.loads(p.read_text()) == {"k": 1}
    assert _mode(p) == 0o600
    assert _mode(p.parent) == 0o700
    assert not (p.parent / (p.name + ".tmp")).exists()   # 原子替换后无 tmp 残留


def test_atomic_write_private_tightens_existing_wide_dir(tmp_path):
    d = tmp_path / "wide"
    d.mkdir(mode=0o755)
    base.atomic_write_private(d / "f.json", "{}")
    assert _mode(d) == 0o700


def test_atomic_write_private_keeps_host_dir_mode_when_none(tmp_path):
    """宿主自有目录(如 hermes profile)不 chmod, 只保证文件 0600。"""
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


# ── D5: MCP stdio 空白行 ───────────────────────────────────────────
class _FakeStdin:
    def __init__(self, data: bytes):
        self.buffer = io.BytesIO(data)


def test_read_msg_skips_blank_lines_then_eof(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(b"\n   \n{\"jsonrpc\":\"2.0\",\"id\":7}\n"))
    assert mcp.read_msg() == {"jsonrpc": "2.0", "id": 7}
    assert mcp.read_msg() is None          # 只有真 EOF 才结束


def test_read_msg_bad_frame_still_raises(monkeypatch):
    """坏帧语义不变: 抛 JSONDecodeError, 由 main 回 -32700 且继续服务。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(b"not json{{{\n"))
    try:
        mcp.read_msg()
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("malformed frame should raise JSONDecodeError")


# ── D6: 资源释放 hash 判定 ─────────────────────────────────────────
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
    """核心回归: 原 mtime 判定会漏发(SDK 解包时间戳早于目标文件) ⇒ 现按 hash 清单判定。"""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "home"))
    root = _mk_src(tmp_path, "v1\n")
    rr.release_resources("sys-1", root)
    dst = _dst(tmp_path)
    os.utime(dst, (1, 1))                      # 目标 mtime 做旧(更早于包内文件)
    (Path(root) / "role_prompt" / "common.md").write_text("v2\n")
    r = rr.release_resources("sys-1", root)
    assert r["updated"] == 1
    assert dst.read_text() == "v2\n"


def test_release_never_overwrites_personalized(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "home"))
    root = _mk_src(tmp_path, "v1\n")
    rr.release_resources("sys-1", root)
    dst = _dst(tmp_path)
    dst.write_text("my own prompt\n")          # 用户个性化(内容 != 清单记录)
    (Path(root) / "role_prompt" / "common.md").write_text("v2\n")
    r = rr.release_resources("sys-1", root)
    assert r["skipped"] == 1
    assert dst.read_text() == "my own prompt\n"

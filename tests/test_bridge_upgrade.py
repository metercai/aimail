"""`aimail bridge --upgrade` 回归（2026-09-21 新增，用户纠正: 桥升级必须经 CLI）。

覆盖两条分支:
  · sha 相同 → no-op(不重启、不动二进制)
  · sha 不同 → 原子替换 + 重启(stub start_bridge, 不起真进程)
"""
import importlib.util
import io
import os
import sys
import zipfile
from importlib.machinery import SourceFileLoader
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "cli" / "aimail"
if str(_REPO / "cli") not in sys.path:
    sys.path.insert(0, str(_REPO / "cli"))


def _load_cli():
    loader = SourceFileLoader("aimail_cli_upgrade_under_test", str(_CLI))
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("aimail_cli_upgrade_under_test", loader))
    loader.exec_module(mod)
    return mod


CLI = _load_cli()


def _mk_zip(dst: Path, inner_name: str, payload: bytes) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(inner_name, payload)
    dst.write_bytes(buf.getvalue())


def _layout(tmp_path, installed: bytes, zipped: bytes):
    """搭一个最小现场: 仓内 zip + 已装二进制 + 配置占位。返回 (CLI 常量补丁)。"""
    repo = tmp_path / "repo"
    _mk_zip(repo / "bridge" / "aimail-bridge-v0.7.4-linux-amd64.zip",
            "aimail-bridge-v0.7.4-linux-amd64", zipped)
    bdir = tmp_path / "bridge_home"
    (bdir / "bin").mkdir(parents=True)
    binp = bdir / "bin" / "aimail-bridge"
    binp.write_bytes(installed)
    os.chmod(binp, 0o755)
    (bdir / "aimail_bridge.toml").write_text("schema_version = 1\n")
    return repo, bdir, binp


def test_upgrade_is_noop_when_sha_matches(tmp_path, monkeypatch, capsys):
    repo, bdir, binp = _layout(tmp_path, b"same-bytes", b"same-bytes")
    monkeypatch.setattr(CLI, "SCRIPTS_DIR", repo / "cli")
    monkeypatch.setattr(CLI, "BRIDGE_DIR", bdir)
    monkeypatch.setattr(CLI, "BRIDGE_CFG", bdir / "aimail_bridge.toml")
    monkeypatch.setattr(CLI, "BRIDGE_PID", bdir / "bridge.pid")
    monkeypatch.setattr(CLI, "_bridge_pids", lambda: [])
    assert CLI._bridge_upgrade() == 0
    out = capsys.readouterr().out
    assert "已是最新" in out
    assert binp.read_bytes() == b"same-bytes"  # 未被改写


def test_upgrade_replaces_binary_and_restarts(tmp_path, monkeypatch, capsys):
    repo, bdir, binp = _layout(tmp_path, b"old-bytes", b"new-bytes")
    monkeypatch.setattr(CLI, "SCRIPTS_DIR", repo / "cli")
    monkeypatch.setattr(CLI, "BRIDGE_DIR", bdir)
    monkeypatch.setattr(CLI, "BRIDGE_CFG", bdir / "aimail_bridge.toml")
    monkeypatch.setattr(CLI, "BRIDGE_PID", bdir / "bridge.pid")
    monkeypatch.setattr(CLI, "_bridge_pids", lambda: [])  # 未运行 → 无需停止
    calls = []

    import deploy_bridge
    monkeypatch.setattr(deploy_bridge, "start_bridge",
                        lambda b, c, p: (calls.append((b, c, p)), True)[1])
    assert CLI._bridge_upgrade() == 0
    out = capsys.readouterr().out
    assert "二进制已更新" in out and "已以新二进制启动" in out
    assert binp.read_bytes() == b"new-bytes"      # 已替换
    assert mode_ok(binp)                          # 可执行位保留
    assert calls and calls[0][0] == str(binp)     # 用新二进制启动


def mode_ok(p: Path) -> bool:
    return bool(p.stat().st_mode & 0o111)

"""_ensure_binary 无 unzip 回退: 纯 Python zipfile 解压。

2026-10-02 L2 门禁容器实测: 镜像里没有 unzip 可执行文件 ⇒ 装桥失败
("Failed to extract bridge: [Errno 2] No such file or directory: 'unzip'")。
真机同理: 装桥不能因为缺一个解压工具而失败。用仓内真 zip, 只打掉 unzip 子进程。
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "cli"))

import deploy_bridge  # noqa: E402


def test_ensure_binary_falls_back_to_zipfile(monkeypatch, tmp_path):
    def _no_unzip(*args, **kwargs):
        raise FileNotFoundError("No such file or directory: 'unzip'")

    monkeypatch.setattr(deploy_bridge.subprocess, "run", _no_unzip)
    binp = tmp_path / "bin" / "aimail-bridge"
    ok = deploy_bridge._ensure_binary(str(binp), str(binp.parent))
    assert ok is True, "zipfile fallback must deploy the binary when unzip is missing"
    assert binp.is_file() and binp.stat().st_mode & 0o111, "binary must be executable"

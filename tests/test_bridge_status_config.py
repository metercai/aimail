"""bridge status 配置维真值: 配置键是 `bind`(deploy_bridge._config_lines 唯一写法)。

2026-10-02 族1-A(真桥进容器)转真时暴露: 旧实现读 `addr` ⇒ 恒 `?`, 配置维真值
永远打不出来。本测试钉住 bind 键读取(status 只读, 无进程也能跑)。
"""
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def test_bridge_status_reads_bind_key(tmp_path):
    home = tmp_path / "aimail-home"
    bdir = home / "bridge"
    bdir.mkdir(parents=True)
    (bdir / "aimail_bridge.toml").write_text(
        'schema_version = 1\nbind = "127.0.0.1:39999"\nmode = "push"\n',
        encoding="utf-8")
    env = dict(os.environ)
    env["AIMAIL_HOME"] = str(home)
    r = subprocess.run([sys.executable, str(_REPO / "cli" / "aimail"), "bridge"],
                       env=env, capture_output=True, text=True, cwd=str(_REPO))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "addr=127.0.0.1:39999" in r.stdout, r.stdout
    assert "mode=push" in r.stdout, r.stdout

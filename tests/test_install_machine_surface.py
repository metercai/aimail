"""④B 契约测试: 隐藏子命令已删, 两个机器面 ABI 并入 `install`(裁决 B, 2026-09-23)。

锁定四件事:
1. `--payload` 通道输出与**删除前黄金**逐字相同(黄金 = 2026-09-23 删除前实测固化;
   同日 CI 化: 原黄金钉死开发机绝对路径, runner 上 HOME/仓位不同必红——
   v0.1.15 tag 的 publish-pypi L0 首跑实证 2 failed; 现改为 AIMPAIL_HOME 哨兵
   字面 + REPO 自身定位推导, byte-exact 契约不变);
2. `--system-only` = 单行 JSON 契约(成功/失败皆恰一行, exit 0/1);
3. 旧子命令名死透(argparse invalid choice, exit 2) + 互斥/错侧参数 exit 2;
4. 人面 help(`aimail --help` / `aimail install --help`)不含机器面字样。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = [sys.executable, str(REPO / "cli" / "aimail")]

# 黄金 = 删除前(2026-09-23)对 `aimail payload <action>` 的实测输出。
# CI 化(2026-09-23): dir 黄金改为 AIMPAIL_HOME 哨兵字面(run() 注入,
# 与真机 HOME 解耦); source 黄金 = REPO 自身定位推导(跟随 CLI 所在仓,
# 锁 `repo\t<abs pysdk>` 格式, 不再钉死开发机绝对路径)。
GOLDEN_PAYLOAD_DIR = "/aimail-test-home/bin/mcp"   # payload dir mcp (哨兵 AIMPAIL_HOME)
GOLDEN_PAYLOAD_SOURCE = f"repo\t{REPO / 'pysdk'}"  # payload source(随仓)


def run(*args: str, aimail_home: str | None = None) -> tuple[int, str, str]:
    env = None
    if aimail_home is not None:
        env = dict(os.environ)
        env["AIMAIL_HOME"] = aimail_home
    p = subprocess.run(CLI + list(args), capture_output=True, text=True, timeout=60, env=env)
    return p.returncode, p.stdout, p.stderr


def test_payload_dir_byte_identical_to_golden():
    rc, out, _ = run("install", "--payload", "dir", "mcp", aimail_home="/aimail-test-home")
    assert rc == 0, (rc, out)
    assert out.rstrip("\n") == GOLDEN_PAYLOAD_DIR, out


def test_payload_source_byte_identical_to_golden():
    rc, out, _ = run("install", "--payload", "source")
    assert rc == 0, (rc, out)
    assert out.rstrip("\n") == GOLDEN_PAYLOAD_SOURCE, out


def test_payload_dir_without_bundle_errors_rc2():
    # 旧 `payload dir`(无 name) 走 dir+默认 mcp;带 install 动作缺 bundle 必须 rc2
    rc, _ = run("install", "--payload", "install")[:2]
    assert rc == 2


def test_system_only_single_json_line_and_rc1_on_missing_home():
    rc, out, _ = run("install", "--system-only", "-H", "/nonexistent-aimail-xyz")
    assert rc == 1, (rc, out)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, lines  # 恰一行(契约: 日志走 stderr)
    payload = json.loads(lines[0])
    assert payload["success"] is False
    assert payload["error"]  # 带 reason


def test_old_subcommands_are_gone_rc2():
    for old in (["ensure-system", "-H", "/tmp"], ["payload", "dir"]):
        rc, out, err = run(*old)
        assert rc == 2, (old, rc, out, err)
        # argparse 报错走 stderr
        assert "invalid choice" in err or "unrecognized" in err, (out, err)


def test_machine_flags_are_mutually_exclusive_rc2():
    rc, *_ = run("install", "--system-only", "--payload", "dir")
    assert rc == 2


def test_payload_rejects_activation_flags_rc2():
    for extra in (["-c", "CODE"], ["-k", "KEY"], ["--all-agents"], ["-H", "/tmp"]):
        rc, *_ = run("install", "--payload", "dir", *extra)
        assert rc == 2, extra


def test_system_only_rejects_payload_and_wiring_flags_rc2():
    for extra in (["--all-agents"], ["--dest", "/tmp"], ["--force"], ["orphan"]):
        rc, *_ = run("install", "--system-only", *extra)
        assert rc == 2, extra


def test_orphan_payload_operand_without_flag_rc2():
    rc, *_ = run("install", "orphan-operand")
    assert rc == 2


def test_human_help_hides_machine_surface():
    for args in (["--help"], ["install", "--help"]):
        rc, out, _ = run(*args)
        assert rc == 0, (args, rc)
        for lit in ("ensure-system", "payload", "system-only"):
            assert lit not in out, (args, lit, out)

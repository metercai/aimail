"""④B 契约测试: 隐藏子命令已删, 两个机器面 ABI 并入 `install`(裁决 B, 2026-09-23)。

锁定四件事:
1. `--payload` 通道输出与**删除前黄金**逐字相同(黄金 = 2026-09-23 删除前实测固化);
2. `--system-only` = 单行 JSON 契约(成功/失败皆恰一行, exit 0/1);
3. 旧子命令名死透(argparse invalid choice, exit 2) + 互斥/错侧参数 exit 2;
4. 人面 help(`aimail --help` / `aimail install --help`)不含机器面字样。
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = [sys.executable, str(REPO / "cli" / "aimail")]

# 黄金 = 删除前(2026-09-23)对 `aimail payload <action>` 的实测输出
GOLDEN_PAYLOAD_DIR = "/home/ubuntu/.aimail/bin/mcp"  # payload dir mcp
GOLDEN_PAYLOAD_SOURCE = "repo\t/home/ubuntu/aimail/pysdk"


def run(*args: str) -> tuple[int, str, str]:
    p = subprocess.run(CLI + list(args), capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout, p.stderr


def test_payload_dir_byte_identical_to_golden():
    rc, out, _ = run("install", "--payload", "dir", "mcp")
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

"""运行时资源重部署的注册表驱动契约(2026-09-15 修订)。

修复前的两个缺陷(均在 repair.py 第 6 步内部):
1. spawn 用的是"探测到的平台名",不是注册表 sdk_install 动作的 target →
   openclaw(无 SDK 安装入口)会去 spawn `--type openclaw`,argparse 必拒
   (SystemExit 2);
2. 资源探针只认 path/alt 定位键,漏读 openclaw 检查项用的 glob 键 →
    glob 为空 = 检查项恒判"无命中",插件在位也报缺失。

契约:
- SDK 安装目标只来自注册表(install_steps kind=sdk_install → target);
  没有该步骤的平台返回空串,由调用方改为打印注册表 fix 提示。
- 探针认 path/alt/glob 三类定位键。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))
import repair  # noqa: E402


def test_sdk_install_target_from_registry():
    assert repair._sdk_install_target("hermes") == "hermes"
    assert repair._sdk_install_target("deerflow") == "deerflow"


def test_platforms_without_sdk_entry_have_no_target():
    for plat in ("openclaw", "pi", "dsh"):
        assert repair._sdk_install_target(plat) == ""


def test_unknown_platform_is_silent():
    assert repair._sdk_install_target("nope") == ""
    assert repair._failing_file_checks("nope", "/tmp") == []


def test_glob_located_check_is_evaluated(tmp_path, monkeypatch):
    """openclaw:插件目录走 glob 键——在位=不缺,缺失才缺(旧实现恒判缺)。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".openclaw"
    (home / "npm" / "projects" / "openclaw-aimail-x").mkdir(parents=True)
    skill = home / "skills" / "agentmail" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("x")

    assert repair._failing_file_checks("openclaw", str(home)) == []

    skill.unlink()
    assert [c["id"] for c in repair._failing_file_checks("openclaw", str(home))] == ["skills"]


def test_hermes_marker_checks(tmp_path):
    """标记在位 → 该项不缺;文件缺失 → 该项缺。"""
    home = tmp_path / ".hermes"
    webhook = home / "hermes-agent" / "gateway" / "platforms" / "webhook.py"
    webhook.parent.mkdir(parents=True)
    webhook.write_text("PREPROCESS_REGISTRY\n")

    ids = [c["id"] for c in repair._failing_file_checks("hermes", str(home))]
    assert "patch-webhook" not in ids      # 标记在位
    assert "patch-profiles" in ids         # profiles.py 不存在

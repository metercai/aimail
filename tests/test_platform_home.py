"""平台根归一(2026-09-11 C 修)——`--home` 传平台目录本身,或父目录也能识别。

背景:传含 `.pi/agent` 的父目录时平台判定落空 → 回退 hermes → 报
`webhook.py/profiles.py 缺失`、`.agentmail` 写成 `no_config`。
契约(与 cli/platforms.json 判据同源)：
1. 目录本身命中平台(dir_name + markers)→ 原样返回；
2. 父目录:`<root>/<dir_name>` 命中 → 返回该平台子目录；
3. 都无法识别 → 原样返回(不静默改语义)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))
from runtime_core import normalize_platform_home  # noqa: E402


def test_dir_is_platform(tmp_path):
    pi = tmp_path / ".pi"
    (pi / "agent").mkdir(parents=True)
    assert normalize_platform_home(pi) == pi


def test_parent_dir_sinks_to_platform(tmp_path):
    root = tmp_path / "host-root"
    (root / ".pi" / "agent").mkdir(parents=True)
    assert normalize_platform_home(root) == root / ".pi"


def test_unrecognized_returns_as_is(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert normalize_platform_home(plain) == plain


def test_missing_path_returns_as_is(tmp_path):
    gone = tmp_path / "nope"
    assert normalize_platform_home(gone) == gone

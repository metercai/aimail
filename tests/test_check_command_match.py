"""health_check 判据 `command_match`(命令面优先 + 路径兜底, 2026-09-25)。

为什么: 原 openclaw 判据只看 glob `~/.openclaw/npm/projects/openclaw-aimail*`
—— 本地路径安装落 `extensions/`, glob 漏判(实测过: 0.1.0 本地副本被当成"未安装"
或反之)。改为宿主自报清单优先, 命令不可用才回退 glob。
"""
import pathlib
import sys

import pytest

CLI_DIR = pathlib.Path(__file__).resolve().parents[1] / "cli"
sys.path.insert(0, str(CLI_DIR))
if "check_status" not in sys.modules:
    sys.path.insert(0, str(CLI_DIR))


def _load():
    import importlib
    return importlib.import_module("check_status")


class _Collector:
    """最小 collector 替身(只记 c.add(...))。"""

    def __init__(self):
        self.items = []

    def add(self, dim, cid, ok, msg, fix):  # noqa: D401
        self.items.append((dim, cid, ok, msg, fix))

    # 该函数只用 c.add; 其余属性若被访问则显式报错(避免静默错桩)
    def __getattr__(self, name):
        raise AttributeError(f"stub 未实现: {name}")


def _run(checks, ctx=None):
    c = _Collector()
    _load()._run_l2_checks(c, "fake", checks, ctx or {"home": "/tmp", "user_home": "/tmp"})
    assert len(c.items) == 1, c.items
    return c.items[0]  # dim, id, ok, msg, fix


def test_command_hit_is_ok():
    """命令面命中 ⇒ ok(即使 glob 不存在)。"""
    _, _, ok, msg, _ = _run([{
        "id": "plugin-installed", "kind": "command_match",
        "argv": ["/bin/sh", "-c", "echo openclaw-aimail installed"],
        "match": "openclaw-aimail",
        "fallback_glob": "/nonexistent-{user_home}/nowhere*",
        "ok_text": "在位", "fail_text": "未安装",
    }])
    assert ok and msg == "在位", (ok, msg)


def test_command_match_is_case_insensitive():
    _, _, ok, _, _ = _run([{
        "id": "p", "kind": "command_match",
        "argv": ["/bin/sh", "-c", "echo OpenClaw-aiMail"],
        "match": "openclaw-aimail", "ok_text": "o", "fail_text": "f",
    }])
    assert ok


def test_command_no_hit_falls_back_to_glob(tmp_path):
    """命令跑通但没命中 ⇒ **不**回退(命令面是权威), 判 fail 并带证据。"""
    (tmp_path / "openclaw-aimail-abc").mkdir()
    _, _, ok, msg, _ = _run([{
        "id": "p", "kind": "command_match",
        "argv": ["/bin/sh", "-c", "echo nothing-here"],
        "match": "openclaw-aimail",
        "fallback_glob": str(tmp_path / "openclaw-aimail-abc"),
        "ok_text": "o", "fail_text": "f",
    }])
    assert not ok and "match=" in msg, (ok, msg)


def test_command_unavailable_uses_glob_fallback(tmp_path):
    """命令不可用(不存在) ⇒ 回退 glob; glob 命中即 ok, 并在 fail 文案里留痕。"""
    (tmp_path / "openclaw-aimail-xyz").mkdir()
    _, _, ok, msg, _ = _run([{
        "id": "p", "kind": "command_match",
        "argv": ["/definitely-not-a-real-openclaw-binary", "plugins", "list"],
        "match": "openclaw-aimail",
        "fallback_glob": str(tmp_path / "openclaw-aimail-xyz"),
        "ok_text": "在位", "fail_text": "未安装",
    }])
    assert ok and msg == "在位", (ok, msg)


def test_command_unavailable_and_glob_miss_is_fail_with_evidence():
    _, _, ok, msg, fix = _run([{
        "id": "p", "kind": "command_match",
        "argv": ["/definitely-not-a-real-binary"],
        "match": "x", "fallback_glob": "/nowhere/at-all*",
        "ok_text": "o", "fail_text": "未安装(插件缺失)",
        "fix": "装上: aimail install --home <home>",
    }])
    assert not ok and "命令不可用" in msg, (ok, msg)
    assert fix.startswith("装上"), fix

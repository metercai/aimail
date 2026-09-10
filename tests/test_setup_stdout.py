"""setup stdout 容错解析 + 本地兜底基线(2026-09-11 假失败修复)。

背景:激活成功、配置已落盘,却因 stdout 混入一行 warning 而报
`setup finished without a system_id`(cli/aimail:805 整体 json.loads)。
契约:
1. 纯 JSON(单行/缩进)可解析;
2. JSON 前后混入噪声行(warning)仍可解析;
3. 真错误(__ERROR__ / 非 JSON)返回 {} —— 绝不猜出 system_id;
4. 兜底只认窗口内新写入的 systems/*/aimail_gateway.json,旧配置不算。
"""
import os
import time

from runtime_core import newest_system_sid, parse_setup_stdout


def test_clean_json_single_and_indented():
    assert parse_setup_stdout('{"system_id": "s1"}')["system_id"] == "s1"
    assert parse_setup_stdout('{\n  "system_id": "s2",\n  "path": "x"\n}')["system_id"] == "s2"


def test_json_with_noise_lines():
    out = 'warning: something on stdout\n{\n "system_id": "s3"\n}\n'
    assert parse_setup_stdout(out)["system_id"] == "s3"
    # 噪声在中间也要能取到最外层对象
    assert parse_setup_stdout('{"a": 1}\ntrailing noise\n')["a"] == 1


def test_unparseable_returns_empty():
    assert parse_setup_stdout("__ERROR__:Activation code already claimed") == {}
    assert parse_setup_stdout("") == {}
    assert parse_setup_stdout("no json here") == {}


def test_newest_system_sid_window(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    fresh = tmp_path / "systems" / "shared-token-fresh"
    old = tmp_path / "systems" / "shared-token-old"
    for d in (fresh, old):
        d.mkdir(parents=True)
        (d / "aimail_gateway.json").write_text("{}")
    stale = time.time() - 3600
    os.utime(old / "aimail_gateway.json", (stale, stale))

    assert newest_system_sid() == "shared-token-fresh"

    # 全部超出窗口 → 不猜
    os.utime(fresh / "aimail_gateway.json", (stale, stale))
    assert newest_system_sid() == ""


def test_newest_system_sid_missing_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "nope"))
    assert newest_system_sid() == ""

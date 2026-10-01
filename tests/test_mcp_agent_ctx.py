"""MCP server 的 agent 上下文解析: 钉 system + 响亮回退(2026-10-02, J4e 根因的第二半)。

`_agent_ctx()` 原实现只按 agent_id 全 systems 扫描(`aimail_base._scan_systems_for_agent`
按目录序**首匹配**)——同一 home 下多个 system 常写同一个 agent_id(本仓夹具实测:
旅程 system 与共享域 system 都是 `agent_id=default`), 首匹配可能挑到**别的地址**。
安装侧现在会把 `AIMAIL_SYSTEM_ID` 随 mcpServers.env 写进来(见
`tests/test_deerflow_mcp_env.py`), 所以 server 侧要:

  * 有该 env ⇒ 钉住安装时的 system 再匹配;
  * 钉住的 system 里没有(绑定搬走 / 陈旧 env)⇒ **不静默**: 回退到原"全 systems 扫描"
    行为, 并往 stderr 打一行 WARNING(stdout 是 MCP 协议帧, 不能混);
  * 没有该 env ⇒ 逐字保持旧行为(零回归)。

同文件顺带钉住 J4e 的正面判据: 绑定在 `AIMAIL_HOME` 指向的根里才找得到 —— 这正是
2026-10-02 实测的根因(子进程无 AIMAIL_HOME ⇒ 回落 `~/.aimail` ⇒ 抛
`agent 'default' not registered`)。
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "pysdk")  # ensure repo pysdk wins over any cli/ shadow

import aimail_base as base  # noqa: E402
import aimail_mcp_server as mcp  # noqa: E402

AGENT = "default"


@pytest.fixture(autouse=True)
def _isolate_agent_context(monkeypatch):
    """set_agent_context 会改模块全局 + 往 os.environ 里 setdefault —— 全部隔离。"""
    saved_cfg = base._ACTIVE_AGENT_CONFIG
    saved_loader = base._CONFIG_LOADER
    saved_env = {k: v for k, v in os.environ.items() if k.startswith("AIMAIL_")}
    for k in saved_env:
        monkeypatch.delenv(k, raising=False)
    yield
    base._ACTIVE_AGENT_CONFIG = saved_cfg
    base._CONFIG_LOADER = saved_loader
    for k in [k for k in os.environ if k.startswith("AIMAIL_")]:
        os.environ.pop(k, None)
    os.environ.update(saved_env)


def _mk_home(root: Path, *systems: str) -> Path:
    """按共享布局造 systems/{sid}/{addr}/agentmail.json(agent_id 同名, 地址各异)。"""
    for sid in systems:
        addr = root / "systems" / sid / f"agent_{sid}".replace("-", "_")
        addr.mkdir(parents=True)
        (addr / "agentmail.json").write_text(json.dumps({
            "email": f"agent@{sid}.test",
            "gateway_url": "http://127.0.0.1:34401",
            "domain": f"{sid}.test",
            "system_id": sid,
            "agent_id": AGENT,
        }))
    return root


def test_context_found_when_home_is_correct(tmp_path, monkeypatch):
    """正面判据(= J4e 的反事实 B 行): AIMAIL_HOME 指向绑定所在根 ⇒ 解析成功。"""
    home = _mk_home(tmp_path / "aimail-home", "sys-a")
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    monkeypatch.setenv("AIMAIL_AGENT_ID", AGENT)

    assert mcp._agent_ctx() == AGENT
    assert base._ACTIVE_AGENT_CONFIG["email"] == "agent@sys-a.test"


def test_context_raises_without_aimail_home(tmp_path, monkeypatch):
    """根因复现(= 反事实 A 行): home 根里没有绑定 ⇒ RuntimeError 原文。"""
    real_home = _mk_home(tmp_path / "elsewhere", "sys-a")
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "empty"))  # 空根, 模拟 ~/.aimail 错根
    monkeypatch.setenv("AIMAIL_AGENT_ID", AGENT)
    (tmp_path / "empty").mkdir()

    with pytest.raises(RuntimeError, match="not registered"):
        mcp._agent_ctx()
    # 真绑定在别处却读不到 —— 与 2026-10-02 实测同形(供读日志的人对号)
    assert (real_home / "systems" / "sys-a").is_dir()


def test_pinned_system_wins_over_directory_order(tmp_path, monkeypatch):
    """钉住的 system 优先于"按目录序首匹配": sys-b 被钉住 ⇒ 取 sys-b 的地址。"""
    home = _mk_home(tmp_path / "aimail-home", "sys-a", "sys-b")
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    monkeypatch.setenv("AIMAIL_AGENT_ID", AGENT)
    monkeypatch.setenv("AIMAIL_SYSTEM_ID", "sys-b")

    assert mcp._agent_ctx() == AGENT
    assert base._ACTIVE_AGENT_CONFIG["email"] == "agent@sys-b.test"


def test_pinned_system_miss_falls_back_loudly(tmp_path, monkeypatch, capsys):
    """钉住的 system 没有绑定 ⇒ 回退全扫描(旧行为)且 stderr 必须有告警(不许静默)。"""
    home = _mk_home(tmp_path / "aimail-home", "sys-a")
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    monkeypatch.setenv("AIMAIL_AGENT_ID", AGENT)
    monkeypatch.setenv("AIMAIL_SYSTEM_ID", "sys-gone")

    assert mcp._agent_ctx() == AGENT
    assert base._ACTIVE_AGENT_CONFIG["email"] == "agent@sys-a.test"
    err = capsys.readouterr().err
    assert "WARNING" in err and "sys-gone" in err, err


def test_without_pinned_system_keeps_legacy_scan(tmp_path, monkeypatch, capsys):
    """没写 AIMAIL_SYSTEM_ID ⇒ 逐字旧行为(全 systems 扫描, 无告警)。"""
    home = _mk_home(tmp_path / "aimail-home", "sys-a", "sys-b")
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    monkeypatch.setenv("AIMAIL_AGENT_ID", AGENT)

    assert mcp._agent_ctx() == AGENT
    assert base._ACTIVE_AGENT_CONFIG["email"] == "agent@sys-a.test"
    assert "WARNING" not in capsys.readouterr().err

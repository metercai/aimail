"""三层收口布局契约(2026-09-23 裁决; 与两阶段方案 阶段一 同车)。

锁四件事:
  1. SDK 路径构造全部落 systems/ 三层之下:
     aimail_log_path → systems/{sid|_unassigned}/{addr}/agentmail.log
     _aimail_dir     → systems/{sid|_unassigned}/{addr}/mail
     顶层**绝不**出现 logs/  mail/  .system_raw_key/
  2. resolve_system_id_for_email: agentmail.json 落点扫描为权威归属;
     解析失败收口 _unassigned(绝不回落旧顶层路径)
  3. raw key 单点: cli/setup_system 写 systems/{sid}/.system_raw_key.key(源级)
  4. bridge 日志单点: bridge/aimail-bridge.log(源级, CLI 两处常量)
"""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pysdk"))

import aimail_base  # noqa: E402


def _sandbox(tmp_path, monkeypatch, cfg=None):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    monkeypatch.setattr(aimail_base, "_CONFIG_LOADER",
                        (lambda: cfg) if cfg is not None else (lambda: None))
    monkeypatch.setattr(aimail_base, "_PROFILE_DIR_RESOLVER", lambda: None)
    return tmp_path


def test_paths_live_under_systems_layer(tmp_path, monkeypatch):
    home = _sandbox(tmp_path, monkeypatch)
    import aimail_tools
    monkeypatch.setattr(aimail_tools, "_resolve_agent_email", lambda: "bob@x.com")
    # 有归属: 建 marker 后 sid 解析命中(目录键 = 真源清洗 @ → _)
    addr = "bob_x.com"
    mdir = home / "systems" / "sys-real" / addr
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "agentmail.json").write_text(
        json.dumps({"email": "bob@x.com", "system_id": "sys-real"}), encoding="utf-8")
    lp = aimail_base.aimail_log_path("bob@x.com")
    assert lp == home / "systems" / "sys-real" / addr / "agentmail.log", lp
    leaf = aimail_tools._aimail_dir()
    assert leaf == home / "systems" / "sys-real" / addr / "mail", leaf
    # 无归属: 收口 _unassigned, 且绝不落旧顶层
    lp2 = aimail_base.aimail_log_path("ghost@nowhere")
    assert lp2.parts[-3:] == ("_unassigned", "ghost_nowhere", "agentmail.log"), lp2
    assert "logs" not in lp2.parts and "mail" not in lp2.parts
    assert (home / "logs").exists() is False
    assert (home / "mail").exists() is False


def test_sid_resolver_is_marker_based(tmp_path, monkeypatch):
    home = _sandbox(tmp_path, monkeypatch)
    assert aimail_base.resolve_system_id_for_email("a@b.com") == ""
    d = home / "systems" / "sid-42" / "a_b.com"
    d.mkdir(parents=True)
    (d / "agentmail.json").write_text("{}", encoding="utf-8")
    assert aimail_base.resolve_system_id_for_email("a@b.com") == "sid-42"
    # marker 扫描优先于 profile(带 system_id 但 email 不匹配的 profile 不劫持)
    cfg = {"email": "other@b.com", "system_id": "sid-profile"}
    monkeypatch.setattr(aimail_base, "_CONFIG_LOADER", lambda: cfg)
    assert aimail_base.resolve_system_id_for_email("a@b.com") == "sid-42"
    # email 匹配时 profile 命中(零扫描路径)
    assert aimail_base.resolve_system_id_for_email("other@b.com") == "sid-profile"


def test_cli_source_single_points(tmp_path):
    """CLI 侧 raw key / bridge 日志的单点(源级断言, 白名单纪律的机器面)。"""
    setup = (REPO / "cli" / "setup_system.py").read_text(encoding="utf-8")
    assert '"systems" / system_id' in setup and '".system_raw_key.key"' in setup
    assert '".system_raw_key"' not in setup.replace('".system_raw_key.key"', "")
    deploy = (REPO / "cli" / "deploy_bridge.py").read_text(encoding="utf-8")
    assert '"bridge", "aimail-bridge.log"' in deploy
    assert '"systems", sid' in deploy
    assert "logs/aimail-bridge.log" not in deploy
    main = (REPO / "cli" / "aimail").read_text(encoding="utf-8")
    assert 'AIMAIL_HOME / "bridge" / "aimail-bridge.log"' in main
    assert 'AIMAIL_HOME / "logs"' not in main
    assert 'AIMAIL_HOME / "mail"' not in main
    assert '".system_raw_key"' not in main.replace('".system_raw_key.key"', "")
    repair = (REPO / "cli" / "repair.py").read_text(encoding="utf-8")
    assert "~/.aimail/bridge/aimail-bridge.log" in repair
    assert "logs/aimail-bridge.log" not in repair

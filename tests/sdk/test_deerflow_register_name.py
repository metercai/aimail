"""C2 (P6, owner ruling 2026-10-02): deer-flow register 接受 --name 直达目标名.

- argparse: register 子命令带 --name(空 = 按 --agent 派生, 旧行为);
- _cmd_register: --name 只对单 agent 生效(--all 枚举各 agent 自派生, 不接受统一名);
- register_agents: 邮箱派生 name 优先(name or agent_id) —— 退役"先默认名注册再
  rename"的中间态(F13 根), 终态与旧 rename 路径同形(agent_id 承载平台语义)。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parent.parent
_PYSDK = _REPO / "pysdk"
for p in (str(_PYSDK), str(_PYSDK / "deer-flow")):
    if p not in sys.path:
        sys.path.insert(0, p)

import manage  # noqa: E402 — prints an install warning on import; harmless


def test_register_subcommand_exposes_name_flag():
    import argparse
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            manage.main(["register", "--help"])
        except SystemExit:
            pass
    assert "--name" in buf.getvalue(), "register 子命令必须暴露 --name(C2)"


def test_cmd_register_threads_name_for_single_agent(monkeypatch):
    seen = {}
    monkeypatch.setattr(manage, "register_agents",
                        lambda **kw: seen.update(kw) or 0)
    rc = manage._cmd_register(SimpleNamespace(
        agent="default", all=False, name="alice",
        manager="m@x.tm", system_id="s1"))
    assert rc == 0
    assert seen["agent"] == "default"
    assert seen["name"] == "alice"


def test_cmd_register_ignores_name_for_all(monkeypatch):
    seen = {}
    monkeypatch.setattr(manage, "register_agents",
                        lambda **kw: seen.update(kw) or 0)
    manage._cmd_register(SimpleNamespace(
        agent="", all=True, name="alice",
        manager="m@x.tm", system_id="s1"))
    assert seen["agent"] == "all"
    assert seen["name"] == "", "--all 枚举各 agent 自派生, 不接受统一名"


def test_email_derivation_rules_used_by_register_agents():
    """register_agents 内联表达式 email_for_agent(name or agent_id, …) 的两条规则:
    目标名原样派生; 缺省(空 name 回落 agent_id)保持默认名归一(default → agent)。
    """
    real = manage.email_for_agent
    assert real("alice", "d.tm", "") == "alice@d.tm"      # name 直达
    assert real("default", "d.tm", "") == "agent@d.tm"    # 旧行为(默认名归一)不变
    assert real("bob", "d.tm", "s1") == "bob.s1@d.tm"     # 共享域中缀

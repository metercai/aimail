"""C2 (P6, owner ruling 2026-10-02): deer-flow register accepts --name to directly target name.

- argparse: register subcommand has --name (empty = derived from --agent, old behavior);
- _cmd_register: --name only effective for single agent (--all enumerates each agent self-derived, does not accept unified name);
- register_agents: email derived name takes priority (name or agent_id) —— retires the intermediate state of "register with default name then
  rename" (F13 root), final state isomorphic to old rename path (agent_id carries platform semantics).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parent.parent.parent
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
    assert "--name" in buf.getvalue(), "register subcommand must expose --name (C2)"


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
    assert seen["name"] == "", "--all enumerates each agent self-derived, does not accept unified name"


def test_email_derivation_rules_used_by_register_agents():
    """register_agents inline expression email_for_agent(name or agent_id, ...) two rules:
    target name derived as-is; default (empty name falls back to agent_id) maintains default name normalization (default → agent).
    """
    real = manage.email_for_agent
    assert real("alice", "d.tm", "") == "alice@d.tm"      # name direct
    assert real("default", "d.tm", "") == "agent@d.tm"    # old behavior (default name normalization) unchanged
    assert real("bob", "d.tm", "s1") == "bob.s1@d.tm"     # shared domain infix

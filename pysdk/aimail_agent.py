"""agent-level runtime helpers — scenario 1 (address-code mailbox, no CLI).

The prompt-driven path ("activate my AIMail mailbox …") needs three things the
host must not hand-roll: the agent-level toolset, the skill file, and the pull
loop that feeds the host's inbound processing. They live here so every Python
host calls the same, tested code.

Board tools are included: an address-code mailbox can be a board MEMBER — the
invite mail carries its token and the inbound chain stores it (board_creds.json).
Creating a board is not an agent-level action (the address-code product grants
no boards).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

import _resources_release
import aimail_base as base
import aimail_contract as contract
from aimail_mcp_server import HANDLERS, TOOLS

#: Tools that are meaningful for an agent-level mailbox (board = member scope).
AGENT_TOOL_NAMES = (
    "activate_address_code",
    "role_calibrator",
    "send_mail",
    "manage_contacts",
    "contact_profile",
    "set_contact_profile",
    "email_summary",
    "set_email_summary",
    "search_mail",
    # board tools: usable as a board MEMBER — the invite mail carries the token
    # and the inbound chain stores it (board_creds.json). Creating a board is
    # not an agent-level action: the address-code product grants 0 boards.
    "board_status",
    "board_task_list",
    "board_task_show",
    "board_members",
    "board_roles",
    "board_heartbeat",
)


def agent_toolset() -> list:
    """The agent-level toolset: [{name, description, inputSchema, handler}].

    Ready to register with the host as-is: same shape as the MCP ``tools/list``
    entries plus the callable under ``handler`` (mirrors the TS MailToolDef,
    where the handler is a field of the tool).
    """
    names = set(AGENT_TOOL_NAMES)
    out = []
    for tool in TOOLS:
        name = str(tool.get("name") or "")
        if name not in names:
            continue
        handler = HANDLERS.get(name)
        if handler is None:
            continue
        out.append({**tool, "handler": handler})
    return out


def _skill_source() -> Path:
    """Shipped skill file (wheel: <pkg>/resources; repo checkout: root resources)."""
    return Path(_resources_release.resources_root()) / "skills" / "SKILL.md"


def install_agent_skill(skills_dir: str, subdir: str = "") -> dict:
    """Copy the shipped skill into ``<skills_dir>/<skill name>/SKILL.md``.

    Idempotent: rewrites only when the content differs (user edits are kept
    out of it — the file is a package artifact, not a user config).
    Returns ``{path, installed}``.
    """
    sub = subdir or contract.AGENT_SKILL_NAME
    src = _skill_source()
    text = src.read_text(encoding="utf-8")
    dest = Path(skills_dir).expanduser() / sub / "SKILL.md"
    same = False
    try:
        same = dest.read_text(encoding="utf-8") == text
    except OSError:
        same = False
    if not same:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return {"path": str(dest), "installed": not same}


def start_agent_level(skills_dir: str = "", on_email: Optional[Callable] = None,
                      on_agent: Optional[Callable] = None,
                      env: Optional[dict] = None, log: Optional[Callable] = None,
                      system_id: str = "", **pull_opts: Any) -> dict:
    """Start the agent-level runtime: skill (optional) + toolset + pull loop.

    - ``skills_dir``: when given, install the shipped skill there first.
    - ``on_agent(enriched, cfg, mail)``: your agent turn. Used by the default
      inbound chain: the SDK runs the shared pipeline (``process_inbound_mail``)
      and hands the enriched payload to you; ping/pong frames never reach it.
    - ``on_email(cfg, mail)``: raw-delivery escape hatch (no preprocessing).
    - **Hot-add**: the runtime starts fine with zero bindings (that is the normal
      state before activation) and picks up the binding the moment
      ``activate_address_code`` persists it — no host restart. The returned
      ``handles`` list grows in place; ``stop()`` unregisters the hook and stops
      every loop.
    - One mailbox per process: the default chain adopts the first binding's
      context (agent-level = one address per agent).

    Returns ``{handles, tools, skill, stop}``.
    """
    skill = install_agent_skill(skills_dir) if skills_dir else None
    bindings = [c for c in base.iter_agentmail_configs(system_id)
                if base.is_agent_scope_binding(c)]
    if on_email is None and len(bindings) > 1:
        raise ValueError(
            "start_agent_level: %d agent-scope bindings in this process — "
            "the default inbound chain is single-mailbox; pass on_email "
            "to route per binding" % len(bindings))

    def _adopt(cfg: dict) -> None:
        email = str(cfg.get("email") or "")
        base.set_agent_context(email.split("@")[0],
                              str(cfg.get("system_id") or ""))

    deliver = on_email
    if deliver is None:
        def _default_inbound(cfg, mail):
            enriched = base.process_inbound_mail(mail.get("body") or {},
                                                 mail.get("headers") or {})
            # None = the pipeline consumed the frame (ping/pong): no agent turn.
            if enriched is not None and on_agent is not None:
                return on_agent(enriched, cfg, mail)
            return enriched
        deliver = _default_inbound

    handles: list = []

    def _start_for(cfg: dict):
        email = str(cfg.get("email") or "")
        for h in handles:
            if getattr(h, "email", "") == email:
                return h  # already polling this binding (idempotent)
        if not handles:
            _adopt(cfg)
        h = base.start_agent_pull_entry(cfg, on_email=deliver, env=env, log=log,
                                        **pull_opts)
        if h is not None:
            handles.append(h)
        return h

    for cfg in bindings:
        _start_for(cfg)
    if len(bindings) == 1:
        _adopt(bindings[0])  # context even when polling is disabled for it

    def _on_binding_created(cfg: dict) -> None:
        if not base.is_agent_scope_binding(cfg):
            return
        _start_for(cfg)
        if log is not None:
            log("[aimail-pull] %s: bound after activation — polling started "
                "without a reload" % cfg.get("email"))

    base.register_binding_created_hook(_on_binding_created)

    def stop() -> None:
        base.unregister_binding_created_hook(_on_binding_created)
        base.stop_agent_pull_entries(handles)
        handles.clear()

    return {"handles": handles, "tools": agent_toolset(), "skill": skill,
            "stop": stop}


# ═══════════════════════════════════════════════════════════════
# Bootstrap command line —— shell only does "install SDK / install skill / report registration surface", activation still happens inside the agent
#
#   pip install agent-aimail
#   aimail-agent setup --skills-dir <skills dir>
#
# This command does not change any semantics other than state: it installs skill, prints toolset (tool names) and registration method
# (MCP or in-process agent_toolset). After host registers and reloads, agent should see
# activate_address_code, then the agent itself calls the tool to complete activation —— do not run activation on behalf in the shell.
# TypeScript environment has no CLI: tool surface is `agentTools()`, see docs/agent-self-setup.md.
# ═══════════════════════════════════════════════════════════════


def _cmd_setup(args) -> int:
    skill = install_agent_skill(args.skills_dir) if args.skills_dir else None
    names = [t["name"] for t in agent_toolset()]
    out = {
        "skill": skill,
        "toolset": names,
        "register": {
            "mcp": "python -m aimail_mcp_server",
            "in_process": "from aimail_agent import agent_toolset",
        },
        "activate_tool_visible": "activate_address_code" in names,
        "next": "register this toolset in the host, reload the agent, then call "
                "activate_address_code from inside the agent",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["activate_tool_visible"] else 1


def main(argv=None) -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(
        prog="aimail-agent",
        description="Install the agent-level AIMail toolset+skill into a host "
                    "(the activation itself runs inside the agent).")
    ap.add_argument("--home", default="", help="AIMAIL_HOME override (default: ~/.aimail)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="install the skill and print the toolset to register")
    s.add_argument("--skills-dir", default="",
                   help="also install the skill here (<dir>/<skill name>/SKILL.md)")
    s.set_defaults(fn=_cmd_setup)

    args = ap.parse_args(argv)
    if args.home:
        os.environ["AIMAIL_HOME"] = args.home
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

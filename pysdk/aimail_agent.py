"""agent-level runtime helpers — scenario 1 (address-code mailbox, no CLI).

The prompt-driven path ("activate my AIMail mailbox …") needs three things the
host must not hand-roll: the agent-level toolset, the skill file, and the pull
loop that feeds the host's inbound processing. They live here so every Python
host calls the same, tested code.

What is agent-level only: board tools are **not** part of this toolset. Board
access needs a board membership and its token; an address-code mailbox has
neither, and calling them with the agent key is rejected (measured: HTTP 401
"invalid token or role"). Everything else (mail, contacts, summaries, local
search, identity card, activation) works on the agent-scope key.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import _resources_release
import aimail_base as base
import aimail_contract as contract
from aimail_mcp_server import HANDLERS, TOOLS

#: Tools that are meaningful for an agent-level mailbox (board_* deliberately
#: absent — no board membership/token exists on this path).
AGENT_TOOL_NAMES = (
    "activate_address_code",
    "send_mail",
    "manage_contacts",
    "contact_profile",
    "set_contact_profile",
    "email_summary",
    "set_email_summary",
    "search_mail",
    "set_public_whoami",
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
      inbound chain: the SDK runs the shared pipeline
      (``process_inbound_mail`` — the same chain push feeds) and hands the
      enriched payload to you; ping/pong frames are consumed by the pipeline
      and never reach this callback.
    - ``on_email(cfg, mail)``: raw-delivery escape hatch — replaces the whole
      inbound chain with your own (no preprocessing).
    - The default chain needs exactly one agent-scope binding in the process
      (agent-level = one mailbox per agent).
    - Injects the agent context for a single binding, so the module-level mail
      tools work without a manual ``set_agent_context`` call.
    Returns ``{handles, tools, skill}``.
    """
    skill = install_agent_skill(skills_dir) if skills_dir else None
    bindings = [c for c in base.iter_agentmail_configs(system_id)
                if base.is_agent_scope_binding(c)]

    def _adopt(cfg: dict) -> None:
        email = str(cfg.get("email") or "")
        base.set_agent_context(email.split("@")[0],
                              str(cfg.get("system_id") or ""))

    deliver = on_email
    if deliver is None:
        if len(bindings) != 1:
            raise ValueError(
                "start_agent_level: %d agent-scope bindings in this process — "
                "the default inbound chain is single-mailbox; pass on_email "
                "to route per binding" % len(bindings))
        _adopt(bindings[0])

        def _default_inbound(cfg, mail):
            enriched = base.process_inbound_mail(mail.get("body") or {},
                                                 mail.get("headers") or {})
            # None = the pipeline consumed the frame (ping/pong): no agent turn.
            if enriched is not None and on_agent is not None:
                return on_agent(enriched, cfg, mail)
            return enriched
        deliver = _default_inbound
    elif len(bindings) == 1:
        _adopt(bindings[0])

    handles = base.start_agent_pull_entries(
        on_email=deliver, env=env, log=log, system_id=system_id, **pull_opts)
    return {"handles": handles, "tools": agent_toolset(), "skill": skill}

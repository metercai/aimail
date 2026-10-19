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
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

import _resources_release
import aimail_base as base
import aimail_contract as contract
import aimail_tools as tools
from aimail_mcp_server import HANDLERS, TOOLS

#: Tools that are meaningful for an agent-level mailbox (board = member scope).
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


# ═══════════════════════════════════════════════════════════════
# 引导命令行 —— 宿主没有 toolset、但有 shell 时的最小路径
#
# 激活任务不能靠 agent 自己现写代码: 装完 SDK 后, 这套子命令就是全部动作。
#   python -m aimail_agent activate "<粘进来的激活行>" --skills-dir DIR
#   python -m aimail_agent pull --once            # 或 --loop 交给宿主守护
# 有 toolset 的宿主(D4/D5 场景)不需要这几条命令, toolset 才是主路径。
# ═══════════════════════════════════════════════════════════════

_CODE_RE = re.compile(r"\b([A-Za-z0-9_]+-[0-9a-f]{4}(?:-[0-9a-f]{4}){2,})\b")
_ADDR_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")
_URL_RE = re.compile(r"https?://[^\s,;\"'<>)]+")


def parse_activation_line(text: str) -> dict:
    """Pull address / code / gateway out of the activation line a user pastes.

    The line is prose ("Activate my AIMail mailbox a@b using the activation code
    shared_a-… . AIMail gateway is at http://…"), so this only has to be exact
    about the three shapes — not about wording.
    """
    body = " ".join(str(text or "").split())
    addr = _ADDR_RE.search(body)
    code = _CODE_RE.search(body)
    url = _URL_RE.search(body)
    if not (addr and code and url):
        raise ValueError(
            "cannot read address/code/gateway from the line "
            "(expected an address, an activation code like shared_a-xxxx-…, and a gateway URL)"
        )
    # the line is prose: a sentence-ending dot is not part of the URL
    gateway_url = url.group(0).rstrip(".,;:")
    return {"address": addr.group(0), "code": code.group(1), "gateway_url": gateway_url}


def _binding_for(address: str, system_id: str = "") -> dict:
    wanted = address.strip().lower()
    for cfg in base.iter_agentmail_configs(system_id):
        if str(cfg.get("email") or "").strip().lower() == wanted:
            return cfg
    raise SystemExit("no local binding for %s — run `activate` first" % address)


def _cmd_activate(args) -> int:
    parsed = parse_activation_line(args.line)
    res = tools.activate_address_code(
        code=parsed["code"], address=parsed["address"],
        gateway_url=parsed["gateway_url"], profile_home=args.home or None)
    out = dict(res)
    if out.get("success") and args.skills_dir:
        out["skill"] = install_agent_skill(args.skills_dir)
    if out.get("success"):
        out["tools"] = [t["name"] for t in agent_toolset()]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("success") else 1


def _mail_line(cfg, mail) -> None:
    body = mail.get("body") if isinstance(mail, dict) else None
    headers = (mail or {}).get("headers") if isinstance(mail, dict) else None
    enriched = base.process_inbound_mail(body or {}, headers or {})
    if enriched is not None:
        print(json.dumps(enriched, ensure_ascii=False), flush=True)


def _cmd_pull(args) -> int:
    cfg = _binding_for(args.address, args.system_id)
    email = str(cfg.get("email") or "")
    base.set_agent_context(email.split("@")[0], str(cfg.get("system_id") or ""))
    if args.loop:
        rt = start_agent_level(on_email=_mail_line)
        handles = rt["handles"]
        if not handles:
            print(json.dumps({"error": "no agent-scope binding to poll"}))
            return 2
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            base.stop_agent_pull_entries(handles)
        return 0
    client = tools._GatewayClient(
        str(cfg.get("gateway_url") or ""), str(cfg.get("api_key") or ""),
        identity=email)
    stats = client.start_polling(lambda mail: _mail_line(cfg, mail),
                                interval=0, limit=args.limit, max_rounds=1)
    print(json.dumps({"stats": stats}, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(
        prog="aimail-agent",
        description="Agent-level AIMail bootstrap: activate one address, then drain its mail.")
    ap.add_argument("--home", default="", help="AIMAIL_HOME override (default: ~/.aimail)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("activate", help="claim the activation code in the pasted line")
    a.add_argument("line", help="the activation line the user pasted")
    a.add_argument("--skills-dir", default="", help="also install the skill here")
    a.set_defaults(fn=_cmd_activate)

    p = sub.add_parser("pull", help="drain pending mail (one round, or a loop)")
    p.add_argument("--address", required=True)
    p.add_argument("--system-id", default="")
    p.add_argument("--once", action="store_true", help="drain one round (default)")
    p.add_argument("--loop", action="store_true", help="poll until interrupted")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=_cmd_pull)

    args = ap.parse_args(argv)
    if args.home:
        os.environ["AIMAIL_HOME"] = args.home
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

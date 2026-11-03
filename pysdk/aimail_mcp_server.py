#!/usr/bin/env python3
"""aimail_mcp_server.py — Shared AIMail MCP server (stdio, fallback service).

Exposes Hermes equivalent toolset (structured calls, replacing CLI method):
  send_mail / manage_contacts / contact_profile / set_contact_profile
  email_summary / set_email_summary / board_*（A2A）

 Platform-agnostic (promoted from tools/openclaw/ on 2026-08-18): any agent system only needs to follow the shared
layout to land ~/.aimail/systems/{sid}/{cleaned_addr}/agentmail.json (register chain
auto-writes), to reuse this service——does not import any platform adaptation layer, directly depends on shared core
(aimail_base/aimail_tools/aimail_board)。

agent context: env AIMAIL_AGENT_ID (configured in mcp.servers.<name>.env, default main).
Each tool can also explicitly pass agentId parameter to override (when multiple agents share server).

Zero third-party dependencies: pure standard library JSON-RPC over stdio.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aimail_base as _base            # noqa: E402
import aimail_tools as _tools          # noqa: E402
import aimail_board as _board          # noqa: E402
import tool_spec as _tool_spec         # noqa: E402  (SSOT: pysdk/tool-spec.json)

# Platform identity injection: shared MCP server does not import platform adaptation layer (platform-agnostic),
# identity is injected by platform install scripts (install-mcp.sh etc.) via env with real detection results
# (e.g., AIMAIL_AGENT_IDENTITY=deerflow/2.1.0)——same nature as OpenClaw adaptation layer
# _AGENT_IDENTITY_OVERRIDE injection.
# When env is absent, explicitly set unknown/unknown instead of directory detection: multiple agents coexist locally
# (~/.hermes and ~/.openclaw both present), directory detection will inevitably misreport (hermes takes priority),
# identity can only be declared by the calling interface layer, server makes zero guesses.
_env_identity = os.environ.get("AIMAIL_AGENT_IDENTITY", "").strip()
if _env_identity:
    _tools._AGENT_IDENTITY_OVERRIDE = _env_identity
else:
    # Defer alerting until the server actually starts (main): the generic host only imports to fetch TOOLS/HANDLERS
    # this module just to get TOOLS/HANDLERS should not be disturbed by stderr noise (agent-level integration has no MCP config to write).
    _tools._AGENT_IDENTITY_OVERRIDE = "unknown/unknown"


def _warn_identity_if_unknown() -> None:
    if not _env_identity:
        print(
            "[aimail_mcp] WARNING: AIMAIL_AGENT_IDENTITY not set — "
            "X-AIMail-Agent will report unknown/unknown. Set it in the "
            "MCP client config (see scripts/<platform>/install-mcp.sh).",
            file=sys.stderr,
        )


# ── MCP stdio frames (OpenClaw packaged SDK uses newline-delimited JSON,
#    does not support Content-Length frames)─────────────────────────────────

def read_msg():
    """Read a line of newline-delimited JSON.

    Audit D5: original implementation treated "empty line" and EOF as the same branch (readline gets empty line → strip results in empty →
    return None) ⇒ sporadic whitespace lines sent by peer due to keep-alive/buffer jitter would be treated as stream end,
    server silently exits (subsequent tools/call all unresponsive). Now: empty lines are skipped, only true EOF
    ends the stream.
    """
    while True:
        line = sys.stdin.buffer.readline()
        if not line:            # EOF
            return None
        line = line.strip()
        if not line:            # Blank line: skip and continue reading (no longer terminates service)
            continue
        return json.loads(line)


def write_msg(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ── agent context ────────────────────────────────────────────────

def _agent_ctx(agent_id: str = "") -> str:
    """Determine current agentId (explicit parameter > env) and switch context (shared set_agent_context).

    system_id taken from env ``AIMAIL_SYSTEM_ID`` (written by install-mcp.sh via mcpServers.env,
    supplied by the assembly step pysdk/install.py::_assembly_env): multiple systems under the same home often have same-named
    agent_id, pinning the system at install time prevents ``_scan_systems_for_agent``'s "first match by directory order"
    first match" picks a different address. If not found in the pinned system (binding moved/stale env) ⇒ **loud fallback** to
    full systems scan (i.e., original behavior of this function), one line warning on stderr (stdout is MCP protocol, no mixing allowed).
    No such env ⇒ keep old behavior: scan all systems.
    """
    aid = agent_id or os.environ.get("AIMAIL_AGENT_ID", "main")
    sid = (os.environ.get("AIMAIL_SYSTEM_ID", "") or "").strip()
    if sid:
        try:
            _base.set_agent_context(aid, sid)
            return aid
        except RuntimeError as exc:
            sys.stderr.write(
                "[aimail_mcp] WARNING: %s (AIMAIL_SYSTEM_ID=%s) — "
                "rescanning all systems for agent '%s'\n" % (exc, sid, aid))
            sys.stderr.flush()
    _base.set_agent_context(aid)
    return aid


def _safe(fn):
    """Tool call wrapper: exception → MCP error."""
    try:
        return {"ok": True, **fn()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Tool implementation ────────────────────────────────────────────────────

def tool_send_mail(args: dict) -> dict:
    def fn():
        return _tools.send_mail(
            to=args.get("to", ""),
            subject=args.get("subject", ""),
            body=args.get("body", ""),
            cc=args.get("cc"),
            attachments=args.get("attachments"),
            message_id=args.get("message_id"),
        )
    return _safe(fn)


def tool_role_calibrator(args: dict) -> dict:
    def fn():
        return _tools.role_calibrator()
    return _safe(fn)


def tool_aimail_address(args: dict) -> dict:
    def fn():
        return _tools.aimail_address(
            action=args.get("action", ""),
            code=args.get("code", ""),
            email=args.get("email", ""),
            gateway_url=args.get("gateway_url"),
        )
    return _safe(fn)


def tool_activate_address_code(args: dict) -> dict:
    def fn():
        return _tools.activate_address_code(
            code=args.get("code", ""),
            address=args.get("address", ""),
            gateway_url=args.get("gateway_url"),
            profile_home=args.get("profile_home"),
        )
    return _safe(fn)


def tool_manage_contacts(args: dict) -> dict:
    def fn():
        return _tools.manage_contacts(
            action=args.get("action", "check"),
            address=args.get("address"),
            direction=args.get("direction", "all"),
        )
    return _safe(fn)


def tool_contact_profile(args: dict) -> dict:
    def fn():
        return _tools.contact_profile(address=args.get("address", ""), name=args.get("name", ""))
    return _safe(fn)


def tool_set_contact_profile(args: dict) -> dict:
    def fn():
        return _tools.set_contact_profile(address=args.get("address", ""),
                                          profile=args.get("profile", ""))
    return _safe(fn)


def tool_email_summary(args: dict) -> dict:
    def fn():
        return _tools.email_summary(message_id=args.get("message_id", ""))
    return _safe(fn)


def tool_set_email_summary(args: dict) -> dict:
    def fn():
        return _tools.set_email_summary(message_id=args.get("message_id", ""),
                                        summary=args.get("summary", ""))
    return _safe(fn)


def tool_search_mail(args: dict) -> dict:
    def fn():
        return _tools.search_mail(
            query=args.get("query", ""),
            scope=args.get("scope", "all"),
            since=args.get("since", ""),
            until=args.get("until", ""),
            from_=args.get("from", ""),
            limit=args.get("limit", 20),
        )
    return _safe(fn)


def tool_board_status(args: dict) -> dict:
    def fn():
        return {"status": _board.board_status(args.get("board", ""))}
    return _safe(fn)


def tool_board_task_list(args: dict) -> dict:
    def fn():
        return {"tasks": _board.board_task_list(args.get("board", ""),
                                                args.get("status", ""),
                                                args.get("assignee", ""))}
    return _safe(fn)


def tool_board_task_show(args: dict) -> dict:
    def fn():
        return {"task": _board.board_task_show(args.get("task_id", ""),
                                               args.get("board", ""))}
    return _safe(fn)


def tool_board_heartbeat(args: dict) -> dict:
    def fn():
        return {"result": _board.board_heartbeat(args.get("task_id", ""),
                                                 args.get("board", ""),
                                                 args.get("note", ""))}
    return _safe(fn)


def tool_board_members(args: dict) -> dict:
    def fn():
        return {"members": _board.board_members(args.get("board", ""), args.get("email", ""))}
    return _safe(fn)


def tool_board_roles(args: dict) -> dict:
    def fn():
        return {"roles": _board.board_roles(args.get("board", ""), args.get("role", ""))}
    return _safe(fn)


# ── Tool registry ──────────────────────────────────────────────────
# SSOT (2026-11-03): semantic text no longer lives here. TOOLS is a
# projection of the shared spec (pysdk/tool-spec.json via tool_spec.py) —
# the same file the hermes adapter and the TS SDK project from, so the
# three registration surfaces can never drift.

TOOLS = _tool_spec.mcp_tools()

HANDLERS = {
    "send_mail": tool_send_mail,
    "manage_contacts": tool_manage_contacts,
    "contact_profile": tool_contact_profile,
    "set_contact_profile": tool_set_contact_profile,
    "email_summary": tool_email_summary,
    "set_email_summary": tool_set_email_summary,
    "search_mail": tool_search_mail,
    "board_status": tool_board_status,
    "board_task_list": tool_board_task_list,
    "board_task_show": tool_board_task_show,
    "board_heartbeat": tool_board_heartbeat,
    "board_members": tool_board_members,
    "board_roles": tool_board_roles,
    "role_calibrator": tool_role_calibrator,
    "aimail_address": tool_aimail_address,
    "activate_address_code": tool_activate_address_code,
}

# ── MCP main loop ──────────────────────────────────────────────────

def main() -> int:
    _warn_identity_if_unknown()
    while True:
        try:
            msg = read_msg()
        except json.JSONDecodeError:
            # Bad frame (invalid JSON): return -32700 per JSON-RPC and continue——
            # single frame dirty input is not allowed to kill the entire server
            write_msg({"jsonrpc": "2.0", "id": None,
                       "error": {"code": -32700, "message": "Parse error"}})
            continue
        if msg is None:
            break
        mid = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if method == "initialize":
            write_msg({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aimail-mcp", "version": "1.0.0"},
            }})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            write_msg({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            handler = HANDLERS.get(name)
            if not handler:
                write_msg({"jsonrpc": "2.0", "id": mid,
                           "error": {"code": -32601, "message": f"unknown tool {name}"}})
                continue
            # agent context: explicit agentId parameter > env
            agent_id = args.pop("agentId", "") if isinstance(args, dict) else ""
            try:
                _agent_ctx(agent_id)
            except RuntimeError as e:
                write_msg({"jsonrpc": "2.0", "id": mid,
                           "error": {"code": -32000, "message": str(e)}})
                continue
            result = handler(args)
            text = json.dumps(result, ensure_ascii=False)
            write_msg({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}]}})
        elif method == "ping":
            write_msg({"jsonrpc": "2.0", "id": mid, "result": {}})
        else:
            write_msg({"jsonrpc": "2.0", "id": mid,
                       "error": {"code": -32601, "message": f"unknown method {method}"}})
    return 0


if __name__ == "__main__":
    sys.exit(main())

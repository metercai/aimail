#!/usr/bin/env python3
"""amail_mcp_server.py — 共享 agentmail MCP server（stdio,兜底服务）。

暴露 Hermes 等价工具集（结构化调用,替代 CLI 方式）:
  send_mail / manage_contacts / contact_profile / set_contact_profile
  email_summary / set_email_summary / board_*（A2A）

平台无关（2026-08-18 从 tools/openclaw/ 提升）:任何 agent 系统只需按共享
布局落 ~/.agentmail/systems/{sid}/{cleaned_addr}/agentmail.json(register 链
自动写),即可复用本服务——不 import 任何平台适配层,直接依赖共享核心
(aimail_base/aimail_tools/aimail_board)。

agent 上下文：env AIMAIL_AGENT_ID（mcp.servers.<name>.env 配置,默认 main）。
每工具也可显式传 agentId 参数覆盖（多 agent 共享 server 时）。

零第三方依赖：纯标准库 JSON-RPC over stdio。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aimail_base as _base            # noqa: E402
import aimail_tools as _tools          # noqa: E402
import aimail_board as _board          # noqa: E402

# 平台身份注入:共享 MCP server 不 import 平台适配层(平台无关),
# 身份由平台安装脚本(install-mcp.sh 等)经 env 注入真实检测结果
# (如 AIMAIL_AGENT_IDENTITY=deerflow/2.1.0)——与 OpenClaw 适配层
# _AGENT_IDENTITY_OVERRIDE 注入同性质。
# 无 env 时显式置 unknown/unknown 而非目录检测:本机多 agent 共存
# (~/.hermes 与 ~/.openclaw 同在),目录检测必然误报(hermes 优先),
# 身份只能由调用接口层声明,server 零猜测。
_env_identity = os.environ.get("AIMAIL_AGENT_IDENTITY", "").strip()
if _env_identity:
    _tools._AGENT_IDENTITY_OVERRIDE = _env_identity
else:
    _tools._AGENT_IDENTITY_OVERRIDE = "unknown/unknown"
    print(
        "[amail_mcp] WARNING: AIMAIL_AGENT_IDENTITY not set — "
        "X-AIMail-Agent will report unknown/unknown. Set it in the "
        "MCP client config (see scripts/<platform>/install-mcp.sh).",
        file=sys.stderr,
    )


# ── MCP stdio 帧（OpenClaw 打包的 SDK 用 newline-delimited JSON，
#    不支持 Content-Length 帧）─────────────────────────────────

def read_msg():
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    return json.loads(line)


def write_msg(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ── agent 上下文 ────────────────────────────────────────────────

def _agent_ctx(agent_id: str = "") -> str:
    """确定当前 agentId（显式参数 > env）并切换上下文（共享 set_agent_context）。"""
    aid = agent_id or os.environ.get("AIMAIL_AGENT_ID", "main")
    _base.set_agent_context(aid)
    return aid


def _safe(fn):
    """工具调用包装：异常 → MCP error。"""
    try:
        return {"ok": True, **fn()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 工具实现 ────────────────────────────────────────────────────

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
        return {"task": _board.board_task_show(args.get("task_id", ""))}
    return _safe(fn)


def tool_board_heartbeat(args: dict) -> dict:
    def fn():
        return {"result": _board.board_heartbeat(args.get("task_id", ""), args.get("note", ""))}
    return _safe(fn)


def tool_board_members(args: dict) -> dict:
    def fn():
        return {"members": _board.board_members(args.get("board", ""), args.get("email", ""))}
    return _safe(fn)


def tool_set_public_whoami(args: dict) -> dict:
    def fn():
        return {"result": _board.set_public_whoami(args.get("text", ""))}
    return _safe(fn)


# ── 工具注册表 ──────────────────────────────────────────────────

SCHEMA_STR = {"type": "string"}
TOOLS = [
    {"name": "send_mail", "description": "Send an email from your agentmail address. Returns delivery status.",
     "inputSchema": {"type": "object", "properties": {
         "to": {"type": "string", "description": "Comma-separated recipients"},
         "subject": SCHEMA_STR, "body": {"type": "string", "description": "Markdown body"},
         "cc": {"type": "string", "description": "Comma-separated CC recipients"},
         "attachments": {"type": "array", "items": {"type": "string"},
                         "description": "Local file paths to attach"},
         "message_id": {"type": "string", "description": "Inbound message_id to reply to (threads the reply)"},
     }, "required": ["to", "subject", "body"]}},
    {"name": "manage_contacts", "description": (
        "Manage your contact whitelist: check if an address is allowed, "
        "add, remove, or update entries."),
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["check", "add", "remove", "update"],
                    "description": "Action to perform"},
         "address": {"type": "string", "description": "Email address"},
         "direction": {"type": "string", "enum": ["from", "to", "all"], "default": "all",
                       "description": "Whitelist direction"},
     }, "required": ["action"]}},
    {"name": "contact_profile", "description": "Look up a contact's profile.",
     "inputSchema": {"type": "object", "properties": {
         "address": {"type": "string", "description": "Email address"},
         "name": {"type": "string", "description": "Contact name"}}, "required": []}},
    {"name": "set_contact_profile", "description": "Store or update a contact's profile.",
     "inputSchema": {"type": "object", "properties": {
         "address": {"type": "string", "description": "Email address"},
         "profile": {"type": "string", "description": "Profile fields as JSON string"}},
         "required": ["address", "profile"]}},
    {"name": "email_summary", "description": "Read the stored summary of an email thread.",
     "inputSchema": {"type": "object", "properties": {"message_id": {"type": "string", "description": "Thread message_id"}},
                     "required": ["message_id"]}},
    {"name": "set_email_summary", "description": "Save the summary of an email thread.",
     "inputSchema": {"type": "object", "properties": {
         "message_id": {"type": "string", "description": "Thread message_id"},
         "summary": {"type": "string", "description": "Thread summary text (max 2000 chars)"}},
         "required": ["message_id", "summary"]}},
    {"name": "board_status", "description": (
        "Get a board's working status: goal, progress per status "
        "with assignees, and blockers."),
     "inputSchema": {"type": "object", "properties": {
         "board": {"type": "string", "description": "Board ID (b_ prefix)"}}, "required": ["board"]}},
    {"name": "board_task_list", "description": "List a board's tasks, optionally filtered by status or assignee.",
     "inputSchema": {"type": "object", "properties": {
         "board": {"type": "string", "description": "Board ID (b_ prefix)"},
         "status": {"type": "string", "description": "Filter by task status"},
         "assignee": {"type": "string", "description": "Filter by assignee email"}}, "required": ["board"]}},
    {"name": "board_task_show", "description": "Show one task's full details, including parent-task context.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string", "description": "Task ID (t_<board>_<id>)"}}, "required": ["task_id"]}},
    {"name": "board_heartbeat", "description": (
        "Signal your task is still in progress (a ready task advances to running)."),
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string", "description": "Task ID (t_<board>_<id>)"},
         "note": {"type": "string", "description": "Progress note (optional)"}},
                     "required": ["task_id"]}},
    {"name": "board_members", "description": "List a board's members and their roles.",
     "inputSchema": {"type": "object", "properties": {
         "board": {"type": "string", "description": "Board ID (b_ prefix)"},
         "email": {"type": "string", "description": "Filter by member email"}}, "required": ["board"]}},
    {"name": "set_public_whoami", "description": "Set the public identity card returned for stranger WHOAMI queries.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "Public identity text"}}, "required": ["text"]}},
]

HANDLERS = {
    "send_mail": tool_send_mail,
    "manage_contacts": tool_manage_contacts,
    "contact_profile": tool_contact_profile,
    "set_contact_profile": tool_set_contact_profile,
    "email_summary": tool_email_summary,
    "set_email_summary": tool_set_email_summary,
    "board_status": tool_board_status,
    "board_task_list": tool_board_task_list,
    "board_task_show": tool_board_task_show,
    "board_heartbeat": tool_board_heartbeat,
    "board_members": tool_board_members,
    "set_public_whoami": tool_set_public_whoami,
}

# board 函数体（共享 aimail_board 直接 import——顶层无 registry 注册块,
# 2026-08-18 已从 amail_base.load_board_module 的 ast 裁剪方式简化为直接 import）
_board = _board  # noqa: E741  (显式绑定:共享 aimail_board 模块,见上注释)


# ── MCP 主循环 ──────────────────────────────────────────────────

def main() -> int:
    while True:
        msg = read_msg()
        if msg is None:
            break
        mid = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if method == "initialize":
            write_msg({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "amail-mcp", "version": "1.0.0"},
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
            # agent 上下文：显式 agentId 参数 > env
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

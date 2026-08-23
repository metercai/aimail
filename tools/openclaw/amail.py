#!/usr/bin/env python3
"""amail.py — agentmail 运维/调试 CLI（OpenClaw 侧）。

运行时工具（tools/openclaw/ = 源代码路径，安装时随 MCP server 一并部署）：
  send      发送/回复邮件（--message-id 自动 threading）
  contacts  白名单 check/add/remove/update（add 走 manager 审批）
  contact   get/set 联系人画像
  summary   get/set 线程摘要
  board     status/task/members/roles/heartbeat（A2A 主动查询）

主路径是 MCP server（agent 结构化调用 send_mail 等）；本 CLI 用于
ping-pong 回发、E2E 验证、人工运维。

用法:
  amail.py --agent <agentId> send --to a@x.com --subject S --body B
  amail.py --agent <agentId> contacts --action check --address a@x.com
"""
from __future__ import annotations

import argparse
import json
import os
import sys

def _amail_bootstrap():
    """定位 aimail 运行时核心(bundle / site-packages / 仓库),装配 sys.path。"""
    import importlib.util as _ilu
    _here = os.path.dirname(os.path.abspath(__file__))
    for _d in (_here, os.path.dirname(_here)):
        _p = os.path.join(_d, "_aimail_bootstrap.py")
        if os.path.isfile(_p):
            _spec = _ilu.spec_from_file_location("_aimail_bootstrap", _p)
            if _spec is None or _spec.loader is None:
                continue
            _m = _ilu.module_from_spec(_spec)
            sys.modules["_aimail_bootstrap"] = _m
            _spec.loader.exec_module(_m)
            _core = _m.ensure_core(_here)
            if _core is None:
                raise ImportError("aimail runtime core not found — set AIMAIL_RUNTIME_DIR")
            return _core
    raise ImportError("aimail runtime core not found — set AIMAIL_RUNTIME_DIR")


_amail_bootstrap()

import amail_base as _base            # noqa: E402
import aimail_tools as _tools      # noqa: E402

_board = _base.load_board_module()    # noqa: E402  (board_* 函数体)


def _patch_config(agent_id: str, system_id: str = "") -> dict:
    """切换 agent 上下文（统一走 amail_base.set_agent_context）。"""
    try:
        _base.set_agent_context(agent_id, system_id)
    except RuntimeError as e:
        raise SystemExit(f"ERROR: {e}")
    return _base._ACTIVE_AGENT_CONFIG or {}


def _emit(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_send(args) -> int:
    _patch_config(args.agent)
    result = _tools.send_mail(
        to=args.to,
        subject=args.subject,
        body=args.body,
        cc=args.cc,
        attachments=[a.strip() for a in args.attachment] if args.attachment else None,
        message_id=args.message_id,
    )
    _emit(result)
    return 0 if result.get("success") else 1


def cmd_contacts(args) -> int:
    _patch_config(args.agent)
    result = _tools.manage_contacts(
        action=args.action,
        address=args.address,
        direction=args.direction,
    )
    _emit(result)
    return 0 if result.get("success") or args.action == "check" else 1


def cmd_contact_get(args) -> int:
    _patch_config(args.agent)
    result = _tools.contact_profile(address=args.address, name=args.name or "")
    _emit(result)
    return 0


def cmd_contact_set(args) -> int:
    _patch_config(args.agent)
    result = _tools.set_contact_profile(address=args.address, profile=args.profile)
    _emit(result)
    return 0 if result.get("success") is not False else 1


def cmd_summary_get(args) -> int:
    _patch_config(args.agent)
    result = _tools.email_summary(message_id=args.message_id)
    _emit(result)
    return 0


def cmd_summary_set(args) -> int:
    _patch_config(args.agent)
    result = _tools.set_email_summary(message_id=args.message_id, summary=args.text)
    _emit(result)
    return 0 if result.get("success") is not False else 1


def cmd_board(args) -> int:
    _patch_config(args.agent)
    fn = {
        "status": _board.board_status,
        "task-list": _board.board_task_list,
        "task-show": _board.board_task_show,
        "members": _board.board_members,
        "roles": _board.board_roles,
        "heartbeat": _board.board_heartbeat,
    }[args.action]
    if args.action == "task-list":
        result = fn(args.board, args.status or "", args.assignee or "")
    elif args.action == "task-show":
        result = fn(args.task_id)
    elif args.action == "members":
        result = fn(args.board, args.email or "")
    elif args.action == "roles":
        result = fn(args.board, args.role or "")
    elif args.action == "heartbeat":
        result = fn(args.task_id, args.note or "")
    else:
        result = fn(args.board)
    _emit(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="amail.py", description="agentmail CLI (OpenClaw)")
    p.add_argument("--agent", default=os.environ.get("AIMAIL_AGENT_ID", "main"),
                   help="OpenClaw agentId (default: $AIMAIL_AGENT_ID or main)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("send", help="发送/回复邮件")
    sp.add_argument("--to", required=True)
    sp.add_argument("--subject", required=True)
    sp.add_argument("--body", required=True)
    sp.add_argument("--cc")
    sp.add_argument("--attachment", action="append")
    sp.add_argument("--message-id")
    sp.set_defaults(fn=cmd_send)

    sp = sub.add_parser("contacts", help="白名单管理")
    sp.add_argument("--action", choices=["check", "add", "remove", "update"], required=True)
    sp.add_argument("--address")
    sp.add_argument("--direction", default="all")
    sp.set_defaults(fn=cmd_contacts)

    sp = sub.add_parser("contact", help="联系人画像")
    sp.add_argument("--get", dest="address_get")
    sp.add_argument("--name")
    sp.add_argument("--set", dest="address_set")
    sp.add_argument("--profile")
    sp.set_defaults(fn=None)  # 由 main 分发

    sp = sub.add_parser("summary", help="线程摘要")
    sp.add_argument("--get", dest="msg_get")
    sp.add_argument("--set", dest="msg_set")
    sp.add_argument("--text")
    sp.set_defaults(fn=None)

    sp = sub.add_parser("board", help="A2A board")
    sp.add_argument("action", choices=["status", "task-list", "task-show", "members", "roles", "heartbeat"])
    sp.add_argument("--board")
    sp.add_argument("--task-id")
    sp.add_argument("--status")
    sp.add_argument("--assignee")
    sp.add_argument("--email")
    sp.add_argument("--role")
    sp.add_argument("--note")
    sp.set_defaults(fn=cmd_board)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "contact":
        if args.address_get:
            return cmd_contact_get(args)
        if args.address_set:
            args.address = args.address_set
            args.profile = args.profile or ""
            return cmd_contact_set(args)
        print("contact requires --get <addr> or --set <addr> --profile <json>", file=sys.stderr)
        return 2
    if args.cmd == "summary":
        if args.msg_get:
            args.message_id = args.msg_get
            return cmd_summary_get(args)
        if args.msg_set:
            args.message_id = args.msg_set
            args.text = args.text or ""
            return cmd_summary_set(args)
        print("summary requires --get <msg_id> or --set <msg_id> --text", file=sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

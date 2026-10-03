#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aimail.sdk_ops — SDK 侧**可执行门**（进程命令契约）。

架构（与 `install.py` 同一原则，2026-10-04 新增）：

    每个 SDK 自带**可被 spawn 的入口**，调用方（宿主运维程序或人）只执行它，不
    自己持有 SDK 的算法。TS 侧的平台包早已是这个形态（`dist/register-cli.js` /
    `lib/register-cli.js`，注册表 `register.kind=node_entry`）；本模块是 Python 侧
    的**对等门**：把 CLI 侧需要的几个"薄入口"暴露成同形进程契约，从而让任何语言
    的调用方（Rust CLI / 未来的 Python CLI / 宿主脚本）都只依赖**一条协议**，
    而不是依赖某语言的运行时内部结构。

    · 门长在 SDK 侧，因为**算法归 SDK**（webhook secret 生成、注册链、`webhook_host`
      三态派生）：语言中立的是**数据**（绑定文件格式、网关 HTTP），不是算法。
      ⇒ 调用方只能"调"，不许抄一份实现。
    · 本模块**不做业务判断**：判定（哪里缺、要不要补、注册值该取哪个）留在调用方；
      这里只做"数据进 → 调 SDK 薄入口 → 结果出"。

进程命令契约（ABI）：

    python -m aimail.sdk_ops <op> [--args '<json-object>']

    stdout  恰一行 JSON（**结果只在 stdout，人类可读文本一律走 stderr**）：
              成功: {"ok": true, "result": <any>}
              失败: {"ok": false, "kind": "usage|import|call", "exc": "<异常类名>", "error": "<文本>"}
    exit    0 = 成功 · 1 = 运行期失败(import/call) · 2 = 用法错误(usage)
    stderr  用法提示 / 异常回溯 / SDK 自身的输出（被重定向到这里，保证 stdout 单行）
    --args  缺省 `{}`；必须是 JSON **对象**，键 = 各 op 的参数名

op 表（op → SDK 薄入口；均为幂等或只读）：

    iter_bindings                  system_id                → aimail_base.iter_agentmail_configs
    ensure_webhook_secret          binding                  → aimail_base.ensure_binding_webhook_secret
    resolve_register_webhook_url   gw, local_webhook_url    → aimail_base.resolve_register_webhook_url
    register_agent_email           gw, system_id, email,    → aimail_base.register_agent_email
                                   webhook_url?, webhook_secret?, manager_address?
    backfill_binding               binding, system_id       → aimail_base.backfill_binding

    · `binding` = `iter_bindings` 返回的元素**原样回传**（含 SDK 注入的 `_config_path`，
      它是"这条绑定落在哪"的唯一依据 —— 自己拼路径会绕过 SDK 的落盘语义）。
    · `gw` = 系统级网关配置对象（`gateway_url` / `admin_key`）：本模块据此建网关客户端，
      不自己读盘 ⇒ 调用方仍是"环境主控"。
    · `register_agent_email` 的 manager 硬门语义不变：缺 manager 时由 SDK 抛
      `ManagerRequiredError`，此处**原样上报**（kind=call, exc=ManagerRequiredError），
      绝不降级成空值注册。

签名/正确性单真源仍在 `aimail_base`；本模块只做参数校验与结果封装。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ── 双形态自举：本文件位于 core 目录（pysdk/ 或 site-packages/aimail/）──
# flat core（aimail_base.py 等）就在此目录下；包形态下 `python -m aimail.sdk_ops`
# 会先执行包 __init__ 完成同样的自举，这里是仓库形态与直接执行时的兜底。
_CORE = os.path.dirname(os.path.abspath(__file__))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


class UsageError(Exception):
    """用法/参数错误（exit 2）。"""


def _require(args: dict, key: str) -> object:
    if key not in args:
        raise UsageError(f"missing required argument: {key}")
    return args[key]


def _require_str(args: dict, key: str, *, allow_empty: bool = True) -> str:
    val = _require(args, key)
    if not isinstance(val, str):
        raise UsageError(f"argument {key} must be a string")
    if not allow_empty and not val.strip():
        raise UsageError(f"argument {key} must not be empty")
    return val


def _require_obj(args: dict, key: str) -> dict:
    val = _require(args, key)
    if not isinstance(val, dict):
        raise UsageError(f"argument {key} must be a JSON object")
    return val


# ── SDK 薄入口的引入（延迟到调用时，便于把 import 失败报成 kind=import）──────


def _sdk():
    import aimail_base  # noqa: PLC0415 — 延迟导入：import 失败要报成 import 类而非 traceback

    return aimail_base


def _gateway_client(gw: dict):
    """按 `gateway_url` / `admin_key` 建网关客户端（`aimail_tools._GatewayClient` 全集）。"""
    from aimail_tools import _GatewayClient  # noqa: PLC0415

    return _GatewayClient(gw.get("gateway_url", ""), gw.get("admin_key", ""))


# ── ops ─────────────────────────────────────────────────────────────────────


def _op_iter_bindings(args: dict):
    return _sdk().iter_agentmail_configs(_require_str(args, "system_id") if "system_id" in args else "")


def _op_ensure_webhook_secret(args: dict):
    return _sdk().ensure_binding_webhook_secret(_require_obj(args, "binding"))


def _op_resolve_register_webhook_url(args: dict):
    return _sdk().resolve_register_webhook_url(
        _require_obj(args, "gw"), _require_str(args, "local_webhook_url")
    )


def _op_register_agent_email(args: dict):
    client = _gateway_client(_require_obj(args, "gw"))
    return _sdk().register_agent_email(
        client,
        _require_str(args, "system_id", allow_empty=False),
        _require_str(args, "email", allow_empty=False),
        webhook_url=_require_str(args, "webhook_url") if "webhook_url" in args else "",
        webhook_secret=_require_str(args, "webhook_secret") if "webhook_secret" in args else "",
        manager_address=_require_str(args, "manager_address") if "manager_address" in args else "",
    )


def _op_backfill_binding(args: dict):
    path = _sdk().backfill_binding(_require_obj(args, "binding"), _require_str(args, "system_id"))
    return {"path": str(path or "")}


OPS = {
    "iter_bindings": _op_iter_bindings,
    "ensure_webhook_secret": _op_ensure_webhook_secret,
    "resolve_register_webhook_url": _op_resolve_register_webhook_url,
    "register_agent_email": _op_register_agent_email,
    "backfill_binding": _op_backfill_binding,
}


# ── ABI 外壳 ────────────────────────────────────────────────────────────────


def _emit(obj: dict) -> None:
    """把信封写到**真正的** stdout（恰一行）。"""
    out = sys.__stdout__ or sys.stdout
    out.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    out.flush()


def _parse_args_argv(argv: list) -> dict:
    parser = argparse.ArgumentParser(
        prog="python -m aimail.sdk_ops",
        description="SDK-side executable ops entry (one-line JSON ABI).",
    )
    parser.add_argument("op", choices=sorted(OPS), help="operation name")
    parser.add_argument("--args", default="{}", help="JSON object with the op's arguments")
    ns = parser.parse_args(argv)  # 用法错 → argparse 自身 stderr + exit 2
    try:
        parsed = json.loads(ns.args)
    except Exception as e:  # noqa: BLE001
        raise UsageError(f"--args is not valid JSON: {e}") from None
    if not isinstance(parsed, dict):
        raise UsageError("--args must be a JSON object")
    return {"op": ns.op, "args": parsed}


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        parsed = _parse_args_argv(argv)
    except SystemExit as e:  # argparse 已打印用法，沿用其退出码（2）
        return int(e.code or EXIT_USAGE)
    except UsageError as e:
        print(f"sdk_ops: {e}", file=sys.stderr)
        _emit({"ok": False, "kind": "usage", "exc": "UsageError", "error": str(e)})
        return EXIT_USAGE

    op, args = parsed["op"], parsed["args"]
    # op 执行期间把 stdout 重定向到 stderr：SDK/依赖自身的任何打印都不许污染单行 JSON。
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        try:
            result = OPS[op](args)
        except UsageError as e:
            _emit({"ok": False, "kind": "usage", "exc": "UsageError", "error": str(e)})
            return EXIT_USAGE
        except ImportError as e:
            msg = f"{type(e).__name__}: {e}"
            _emit({"ok": False, "kind": "import", "exc": type(e).__name__, "error": msg})
            return EXIT_ERROR
        except Exception as e:  # noqa: BLE001 — 一切调用期异常按 ABI 上报，不外泄 traceback 到 stdout
            print(f"sdk_ops: {op} failed: {type(e).__name__}: {e}", file=sys.stderr)
            _emit({"ok": False, "kind": "call", "exc": type(e).__name__, "error": f"{type(e).__name__}: {e}"})
            return EXIT_ERROR
    finally:
        sys.stdout = real_stdout
    _emit({"ok": True, "result": result})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

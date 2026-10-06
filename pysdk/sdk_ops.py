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
    · 本模块**自带判定与编排**（2026-10-06 owner 契约 v1.0 §4.1）：与 TS 侧
      `register-cli.js` 同形 —— 命名/派生/注册/改名/落绑定/受控变更/注销的判定与
      编排都在 SDK 侧完成；调用方（CLI）只做 **transport 分派 + 参数传递 + 收单行 JSON**，
      不再自己拼注册器 argv、不再持有 SDK 算法（此前"判定留在调用方"的立场已废止）。

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
import subprocess

# 注册器执行超时上限（秒）
REGISTRAR_TIMEOUT = 120
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


def _op_version(_args: dict) -> dict:
    """SDK 能力自述：包版本 + 本 SDK 暴露的 op 名单。

    调用方（CLI/宿主）可用它在**使用前**校验能力，避免旧快照/旧包给出"看似成功
    实则过时"的结论（例如旧的 prompt 规则逻辑误报）。无参数、只读、幂等。
    """
    ver = ""
    try:
        from importlib.metadata import version as _v

        ver = _v("aimailsdk")
    except Exception:
        try:
            import aimail  # type: ignore

            ver = str(getattr(aimail, "__version__", ""))
        except Exception:
            ver = ""
    return {"version": ver, "ops": sorted(OPS)}


# ── 自持动作 op（契约 v1.0 §4.1）─────────────────────────────────────────────
# 与 TS 侧 register-cli.js 同形：判定与编排在 SDK 内；调用方只传参、只收单行 JSON。
# 复用既有 aimail_base 薄入口（不新写逻辑）；按目标函数签名过滤 kwargs，缺参响亮失败。


def _call(fn, **kw):
    """按目标函数真实签名映射参数后调用（未知 kwargs / 缺必填 ⇒ 响亮失败）。"""
    import inspect as _inspect

    sig = _inspect.signature(fn)
    accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    pos_args, call_kw, missing = [], {}, []
    if accepts_kwargs:
        call_kw.update({k: v for k, v in kw.items() if v is not None})
    for name, prm in sig.parameters.items():
        if prm.kind in (prm.VAR_POSITIONAL, prm.VAR_KEYWORD):
            continue
        if name in kw and kw[name] is not None:
            if prm.kind == prm.POSITIONAL_ONLY:
                pos_args.append(kw[name])
            else:
                call_kw[name] = kw[name]
        elif prm.default is prm.empty:
            missing.append(name)
    if not accepts_kwargs:
        allowed = {n for n, prm in sig.parameters.items()
                   if prm.kind in (prm.POSITIONAL_OR_KEYWORD, prm.KEYWORD_ONLY,
                                   prm.POSITIONAL_ONLY)}
        unknown = sorted(set(kw) - allowed)
        if unknown:
            raise RuntimeError(f"{getattr(fn, '__name__', fn)}: unsupported args {unknown}")
    if missing:
        raise RuntimeError(f"{getattr(fn, '__name__', fn)}: 缺少必填参数 {missing}")
    return fn(*pos_args, **call_kw)

def _import_base():
    """取 aimail_base（双形态：flat core 目录直接执行 / 包形态 aimail.aimail_base）。"""
    try:
        import aimail_base  # flat core: pysdk/（或 site-packages/aimail/）内直接可 import
        return aimail_base
    except Exception:  # noqa: BLE001
        from aimail import aimail_base
        return aimail_base


def _sdk_version():
    try:
        return getattr(_import_base(), "__version__", "") or ""
    except Exception:
        return ""


def _load_system(system_id):
    """系统级配置（CLI 写、SDK 只读）+ 网关客户端 —— 三 op 自足，CLI 不传 cfg/client。"""
    base = _import_base()
    cfg = None
    loader = getattr(base, "_load_gateway_config", None)
    if loader:
        try:
            cfg = loader(system_id) or None
        except Exception:
            cfg = None
    if not cfg:
        raise UsageError(f"找不到系统级配置 system_id={system_id!r}（须先 aimail install）")
    try:
        from aimail_tools import _GatewayClient
        client = _GatewayClient(cfg.get("gateway_url"), cfg.get("admin_key"))
    except Exception as e:
        raise RuntimeError(f"无法构造网关客户端: {e}")
    return base, cfg, client


def _agent_binding(base, agent_id, system_id):
    f = getattr(base, "load_agent_config", None)
    if f and agent_id:
        try:
            return f(agent_id, system_id) or {}
        except Exception:
            return {}
    return {}


def _run_registrar(spec):
    """按 spec.kind 跑平台注册器（spec = CLI 传入的已展开数据）；未知 kind ⇒ 响亮失败。"""
    kind = str((spec or {}).get("kind") or "")
    argv = [str(x) for x in (spec.get("argv") or [])]
    env = dict(os.environ)
    for k, v in (spec.get("env") or {}).items():
        env[str(k)] = str(v)
    py = str(spec.get("python") or "python3")
    if kind == "node_entry":
        cmd = ["node", str(spec.get("node_path") or "")] + argv
    elif kind == "python_script":
        cmd = [py, str(spec.get("script") or "")] + argv
    elif kind == "python_module":
        cmd = [py, "-m", str(spec.get("module") or "")] + argv
    elif kind == "host_command":
        cmd = [str(spec.get("command") or "")] + argv
    else:
        raise UsageError(f"未知 register_spec.kind={kind!r}（拒绝静默跳过）")
    if not cmd[0] or (len(cmd) > 1 and not cmd[1]):
        raise UsageError("register_spec 缺少可执行体（拒绝静默）")
    pr = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=REGISTRAR_TIMEOUT)
    if pr.returncode != 0:
        raise RuntimeError(
            f"注册器失败 rc={pr.returncode}: {' '.join(cmd[:2])}: {(pr.stderr or pr.stdout or '')[-400:]}"
        )
    return {"rc": pr.returncode, "stdout_tail": (pr.stdout or "")[-400:]}


def _op_assemble(args):
    """装配：自定位配置 → 命名 → 跑平台注册器 → 必要时改名 → 注册 → 落绑定。只返回最终结果。"""
    system_id = args.get("system_id") or ""
    email = args.get("email") or ""
    if not system_id:
        raise UsageError("assemble: 需要 system_id")
    base, syscfg, client = _load_system(system_id)
    agent_id = args.get("agent_id") or email
    binding_cfg = _agent_binding(base, agent_id, system_id)
    plan = base.plan_address_name(
        args.get("requested_name") or args.get("name") or "",
        agent_id=agent_id,
        domain=args.get("domain") or syscfg.get("domain") or "",
        system_name=args.get("system_name") or syscfg.get("system_name") or "",
        aliases=tuple(args.get("aliases") or ()),
        register_argv=tuple(args.get("register_argv") or ()),
    )
    reg_run = _run_registrar(args.get("register_spec") or {})
    renamed = None
    if plan.get("needs_rename") and getattr(base, "rename_address", None):
        renamed = base.rename_address(
            system_id, plan.get("old_email") or "",
            plan.get("new_name") or plan.get("reg_as") or email, binding_cfg)
    webhook_url = args.get("local_webhook_url") or args.get("webhook_url") or ""
    rw = getattr(base, "resolve_register_webhook_url", None)
    if webhook_url and rw:
        webhook_url = rw({"gateway_url": syscfg.get("gateway_url"),
                          "admin_key": syscfg.get("admin_key")}, webhook_url) or webhook_url
    reg = base.register_agent_email(
        client, system_id, email, webhook_url=webhook_url or "",
        webhook_secret=args.get("webhook_secret") or "",
        manager_address=args.get("manager_address") or "")
    updates = {k: v for k, v in (("manager_address", args.get("manager_address")),
                                 ("prompt_rules", args.get("prompt_rules")),
                                 ("persona", args.get("persona"))) if v is not None}
    binding_path = None
    ub = getattr(base, "update_binding", None)
    if updates and ub:
        binding_path = str(ub(system_id, binding_cfg, updates))
    return {"ok": True, "email": email, "plan": plan, "registrar": reg_run,
            "renamed": renamed, "registered": reg, "binding_path": binding_path,
            "sdk_version": _sdk_version()}


def _op_update(args):
    """受控变更：action = rename | set-manager | prompt | persona | webhook-secret。"""
    system_id = args.get("system_id") or ""
    if not system_id:
        raise UsageError("update: 需要 system_id")
    base, syscfg, client = _load_system(system_id)
    action = (args.get("action") or "").strip()
    email = args.get("email") or args.get("old_email") or ""
    cfg = _agent_binding(base, args.get("agent_id") or email, system_id)
    if action == "rename":
        r = base.rename_address(system_id, args.get("old_email") or email,
                                args.get("new_name") or args.get("name") or "", cfg)
    elif action == "set-manager":
        manager = args.get("manager_address") or base.resolve_manager_address("")
        r = base.set_agent_manager(system_id, email, manager, cfg, cfg) or {"ok": True}
    elif action in ("prompt", "persona", "webhook-secret"):
        updates = {"prompt_rules": args.get("prompt_rules")} if action == "prompt" else (
            {"persona": args.get("persona")} if action == "persona" else
            {"webhook_secret": args.get("webhook_secret")})
        updates = {k: v for k, v in updates.items() if v is not None}
        if not updates:
            raise UsageError(f"update action={action}: 缺少要更新的字段")
        r = {"binding_path": str(base.update_binding(system_id, cfg, updates))}
    else:
        raise UsageError(f"update: 未知 action={action!r}（rename|set-manager|prompt|persona|webhook-secret）")
    return {"ok": True, "action": action, "result": r, "sdk_version": _sdk_version()}


def _op_teardown(args):
    """拆除：注销 → 白名单清理 → 绑定回填（顺序固定；缺目标 ⇒ 响亮失败）。"""
    system_id = args.get("system_id") or ""
    email = args.get("email") or ""
    mode = args.get("mode") or {}
    wants = mode.get("unregister", True) or mode.get("whitelist") or mode.get("backfill")
    if not wants:
        # 显式关闭全部动作 ⇒ 幂等 no-op（不触网、不要求目标）
        return {"ok": True, "actions": [], "sdk_version": _sdk_version()}
    if not system_id or not email:
        raise UsageError("teardown: 需要 system_id 与 email（有动作时缺目标 ⇒ 响亮失败）")
    base, syscfg, client = _load_system(system_id)
    actions = []
    if mode.get("unregister", True):
        actions.append({"deregister_agent_email": base.deregister_agent_email(
            client, system_id, email, manager_address=args.get("manager_address") or "")})
    if mode.get("whitelist"):
        actions.append({"cleanup_system_whitelists": base.cleanup_system_whitelists(
            client, system_id, mode.get("addresses") or [email], mode.get("domains") or [],
            mode.get("deregistered") or [email])})
    if mode.get("backfill"):
        cfg = _agent_binding(base, args.get("agent_id") or email, system_id)
        actions.append({"backfill_binding": str(base.backfill_binding(cfg, system_id))})
    return {"ok": True, "actions": actions, "sdk_version": _sdk_version()}

OPS = {
    "version": _op_version,
    "iter_bindings": _op_iter_bindings,
    "ensure_webhook_secret": _op_ensure_webhook_secret,
    "resolve_register_webhook_url": _op_resolve_register_webhook_url,
    "register_agent_email": _op_register_agent_email,
    "backfill_binding": _op_backfill_binding,
    # 自持动作 op（契约 v1.0 §4.1；调用方只做 transport 分派与传参）
    "assemble": _op_assemble,
    "update": _op_update,
    "teardown": _op_teardown,
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
    except SystemExit as e:  # argparse 已打印用法/帮助，沿用其退出码（--help=0，用法错=2）
        # 注意 `e.code or DEFAULT` 会把 0 吞成 DEFAULT（--help 本该 exit 0）—— 必须判 None。
        return int(e.code) if e.code is not None else EXIT_USAGE
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

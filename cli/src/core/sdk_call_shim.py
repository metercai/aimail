"""CLI 侧通用 SDK 调用 shim（**CLI 域**，随 rust 二进制内嵌发布）。

为什么长这样（owner 2026-10-04 口径）：CLI 要用的 SDK 算法函数**早已发布**，按名可调；
不该为此在 SDK 侧"每加一个能力就加一段代码"（那会把 CLI 的每次开发都变成 SDK 发版）。
所以：**SDK 冻结**，调用 glue 归 CLI —— 本 shim 只做"取参数 → 调已发布函数 → 回信封"，
**不允许**在此复刻任何 SDK 算法。

用法（由 Rust 侧 `core::sdkcall::call` 调起）：
    python3 -c "<本文件内容>" <core_dir> <module> <function> <kwargs-json> [<positional-json>]

位置参数可选（有些 SDK 函数按位置传，如 hermes 适配层的 `(name, profile_dir, config)`）：
仍只是"取参数 → 调函数"，不含任何算法。

ABI（与边界稿 §1.5 同形）：
    stdout 恰一行 JSON：{"ok": true, "result": …} / {"ok": false, "kind": "usage|import|call",
    "exc": …, "error": …}；exit 0/1/2；人话与 SDK 自身打印一律 stderr。
"""

import json
import os
import sys


def _emit(obj):
    out = sys.__stdout__ or sys.stdout
    out.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    out.flush()


def main(argv):
    if len(argv) not in (4, 5):
        _emit(
            {
                "ok": False,
                "kind": "usage",
                "exc": "",
                "error": "shim expects: <core_dir> <module> <function> <kwargs-json> [<positional-json>]",
            }
        )
        return 2
    core_dir, mod_name, fn_name, kwargs_json = argv[:4]
    positional_json = argv[4] if len(argv) > 4 else "[]"
    try:
        kwargs = json.loads(kwargs_json)
    except Exception as e:  # noqa: BLE001
        _emit({"ok": False, "kind": "usage", "exc": type(e).__name__, "error": str(e)})
        return 2
    try:
        positional = json.loads(positional_json)
    except Exception as e:  # noqa: BLE001
        _emit({"ok": False, "kind": "usage", "exc": type(e).__name__, "error": str(e)})
        return 2
    if not isinstance(positional, list):
        _emit(
            {
                "ok": False,
                "kind": "usage",
                "exc": "TypeError",
                "error": "positional json must be an array",
            }
        )
        return 2
    if not isinstance(kwargs, dict):
        _emit(
            {
                "ok": False,
                "kind": "usage",
                "exc": "TypeError",
                "error": "kwargs json must be an object",
            }
        )
        return 2
    # 让已发布的 SDK 可导入：仓库/快照形态的 pysdk 目录优先（pip 形态无需此步）
    if core_dir and core_dir not in sys.path:
        sys.path.insert(0, core_dir)
    try:
        mod = __import__(mod_name, fromlist=["_"])
    except Exception as e:  # noqa: BLE001
        _emit({"ok": False, "kind": "import", "exc": type(e).__name__, "error": str(e)})
        return 1
    fn = getattr(mod, fn_name, None)
    if fn is None:
        _emit(
            {
                "ok": False,
                "kind": "import",
                "exc": "AttributeError",
                "error": f"module {mod_name} has no attribute {fn_name}",
            }
        )
        return 1
    # 通用对象 glue：任何位置的 `{"__client__": {"gateway_url":…, "admin_key":…}}`
    # 替换为 SDK 的网关客户端对象（CLI 只是"怎么传对象"，不含算法）。
    def _materialize(v):
        if isinstance(v, dict) and set(v.keys()) == {"__client__"}:
            spec = v["__client__"] or {}
            from aimail_tools import _GatewayClient  # noqa: PLC0415

            return _GatewayClient(spec.get("gateway_url", ""), spec.get("admin_key", ""))
        return v

    positional = [_materialize(v) for v in positional]
    kwargs = {k: _materialize(v) for k, v in kwargs.items()}

    # 通用能力过滤：`__if_accepted__` 里的键**仅当目标签名接受时才传**（CLI 零平台知识，
    # 例如 `_sdk_install` 的 manager 形参只看被调函数签名）。
    conditional = kwargs.pop("__if_accepted__", None) or {}
    if conditional:
        try:
            import inspect

            accepted = set(inspect.signature(fn).parameters)
            for k, v in conditional.items():
                if k in accepted:
                    kwargs[k] = v
        except Exception:  # noqa: BLE001
            pass
    # 被调 SDK 函数的输出改道 stderr：stdout 是"单行 JSON"协议面，不能被污染。
    # 必须是 **fd 级**（os.dup2）——只重绑 Python 的 sys.stdout 抓不到 SDK 自己 spawn 的子进程
    # （实测：pnpm/uv 那类子进程的输出直接落 fd 1 ⇒ 信封被踩 ⇒ 调用方判 TransportError）。
    sys.stdout.flush()
    _saved_fd = os.dup(1)
    try:
        try:
            os.dup2(2, 1)
            result = fn(*positional, **kwargs)
        finally:
            sys.stdout.flush()
            os.dup2(_saved_fd, 1)
    except SystemExit as e:  # SDK 里的显式退出（如 manager 硬门）原样上报，不降级
        _emit(
            {
                "ok": False,
                "kind": "call",
                "exc": "SystemExit",
                "error": str(getattr(e, "code", "") or ""),
            }
        )
        return 1
    except BaseException as e:  # noqa: BLE001
        _emit({"ok": False, "kind": "call", "exc": type(e).__name__, "error": str(e)})
        return 1
    _emit({"ok": True, "result": result})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

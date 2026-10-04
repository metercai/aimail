"""CLI 侧通用 SDK 调用 shim（**CLI 域**，随 rust 二进制内嵌发布）。

为什么长这样（owner 2026-10-04 口径）：CLI 要用的 SDK 算法函数**早已发布**，按名可调；
不该为此在 SDK 侧"每加一个能力就加一段代码"（那会把 CLI 的每次开发都变成 SDK 发版）。
所以：**SDK 冻结**，调用 glue 归 CLI —— 本 shim 只做"取参数 → 调已发布函数 → 回信封"，
**不允许**在此复刻任何 SDK 算法。

用法（由 Rust 侧 `core::sdkcall::call` 调起）：
    python3 -c "<本文件内容>" <core_dir> <module> <function> <kwargs-json>

ABI（与边界稿 §1.5 同形）：
    stdout 恰一行 JSON：{"ok": true, "result": …} / {"ok": false, "kind": "usage|import|call",
    "exc": …, "error": …}；exit 0/1/2；人话与 SDK 自身打印一律 stderr。
"""

import json
import sys


def _emit(obj):
    out = sys.__stdout__ or sys.stdout
    out.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    out.flush()


def main(argv):
    if len(argv) != 4:
        _emit(
            {
                "ok": False,
                "kind": "usage",
                "exc": "",
                "error": "shim expects: <core_dir> <module> <function> <kwargs-json>",
            }
        )
        return 2
    core_dir, mod_name, fn_name, kwargs_json = argv
    try:
        kwargs = json.loads(kwargs_json)
    except Exception as e:  # noqa: BLE001
        _emit({"ok": False, "kind": "usage", "exc": type(e).__name__, "error": str(e)})
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
    try:
        result = fn(**kwargs)
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

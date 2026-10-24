"""CLI-side generic SDK call shim (**CLI domain**, embedded and shipped with the rust binary).

Why it looks like this (owner 2026-10-04 ruling): the SDK algorithm functions the CLI needs are **already published**, callable by name;
should not add code on the SDK side "for every new capability" (that would turn every CLI development into an SDK release).
So: **SDK frozen**, invocation glue belongs to CLI — this shim only does "get params -> call published function -> return envelope",
**Not allowed** to replicate any SDK algorithm here.

Usage (invoked by Rust-side `core::sdkcall::call`):
    python3 -c "<content of this file>" <core_dir> <module> <function> <kwargs-json> [<positional-json>]

Positional arguments are optional (some SDK functions pass by position, e.g. hermes adapter's `(name, profile_dir, config)`):
Still just "fetch params → call function", no algorithm included.

ABI (same shape as boundary draft §1.5):
    stdout exactly one line JSON: {"ok": true, "result": …} / {"ok": false, "kind": "usage|import|call",
    "exc": …, "error": …}; exit 0/1/2; human-readable text and SDK's own prints all go to stderr.
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
    # Make published SDK importable: repo/snapshot form pysdk directory takes priority (pip form does not need this step)
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
    # Generic object glue: any position's `{"__client__": {"gateway_url":…, "admin_key":…}}`
    # is replaced by SDK's gateway client object (CLI is just "how to pass object", no algorithm).
    def _materialize(v):
        if isinstance(v, dict) and set(v.keys()) == {"__client__"}:
            spec = v["__client__"] or {}
            from aimail_tools import _GatewayClient  # noqa: PLC0415

            return _GatewayClient(spec.get("gateway_url", ""), spec.get("admin_key", ""))
        return v

    positional = [_materialize(v) for v in positional]
    kwargs = {k: _materialize(v) for k, v in kwargs.items()}

    # Generic capability filtering: keys in `__if_accepted__` are **only passed if target signature accepts them** (CLI has zero platform knowledge,
    # e.g. `_sdk_install`'s manager formal parameter only looks at called function signature).
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
    # Redirect output of called SDK function to stderr: stdout is "single-line JSON" protocol surface, cannot be polluted.
    # Must be **fd-level** (os.dup2) — only rebinding Python's sys.stdout cannot capture child processes spawned by the SDK itself
    # (tested: pnpm/uv type sub-process output lands directly on fd 1 ⇒ envelope corrupted ⇒ caller judges TransportError).
    sys.stdout.flush()
    _saved_fd = os.dup(1)
    try:
        try:
            os.dup2(2, 1)
            result = fn(*positional, **kwargs)
        finally:
            sys.stdout.flush()
            os.dup2(_saved_fd, 1)
    except SystemExit as e:  # Explicit exit in SDK (e.g. manager hard gate) reported as-is, no downgrade
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

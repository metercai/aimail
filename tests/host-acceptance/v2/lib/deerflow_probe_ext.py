"""deerflow_probe_ext.py — a throwaway deer-flow extension used by the L2 environment gate.

It is NOT part of the AIMail SDK. Its only job is to prove, on a real host, that the
extension surface the V2 design depends on actually works:

  * `config.yaml: plugins: [{use: "deerflow_probe_ext:install"}]` resolves and loads
    (entry point path = plain dotted module, resolved by deerflow's `resolve_variable`);
  * `install(registry, config)` is called with a script-controlled `config`;
  * a `ModelTool` registered through `PluginContribution` reaches the model and is
    invoked with a host-supplied `ToolContext` that carries `thread_id` (that thread
    identity is what the real plugin uses to answer "which conversation is this mail in?");
  * the tool's side effect is observable from outside the process (a marker file), so a
    fixture can assert "the model really called it" instead of trusting log wording.

Delete-nothing policy: this file stays a fixture. If the real plugin ever needs the same
shape, copy the pattern — do not import this module from production code.
"""
from __future__ import annotations

import json
import os
import time

__all__ = ["install"]

#: One JSON line per invocation; the fixture reads it to prove the call really happened.
MARKER_ENV = "AIMAIL_DF_PROBE_LOG"
DEFAULT_MARKER = "/tmp/deerflow-probe-hits.log"


def _marker_path() -> str:
    return os.environ.get(MARKER_ENV, "") or DEFAULT_MARKER


def _append(event: dict) -> None:
    path = _marker_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        # A fixture must never break the host it is probing.
        pass


def install(registry, config) -> None:
    """Register the probe tool. `config` is the private `config:` block of the record."""
    token = str((config or {}).get("token", "") or "unset")
    _append({"event": "install_request", "token": token, "at": time.time(),
             "marker": _marker_path()})
    try:
        from deerflow_extension_api.plugins import ModelTool, PluginContribution

        async def _probe(args, ctx) -> dict:
            """Echo the argument back, and record the host-supplied thread identity."""
            value = str((args or {}).get("value", "") or "")
            thread_id = getattr(ctx, "thread_id", None)
            principal = getattr(ctx, "principal", None)
            user_id = getattr(principal, "user_id", None) if principal is not None else None
            _append({"event": "call", "value": value, "thread_id": thread_id,
                     "user_id": user_id, "token": token, "at": time.time()})
            return {"ok": True, "echo": value, "thread_id": thread_id, "token": token}

        contribution = PluginContribution(
            # namespace/title are required by the host dataclass, and `enabled=True` is what
            # makes plugin_settings()["enabled"] true — without it build_plugin_tools()
            # silently skips every declared tool (measured 2026-11-23: the model reported
            # "tool not registered" while install() had already run).
            namespace="aimail_env_probe",
            title="AIMail environment probe",
            description="Test-only extension used by the L2 environment gate.",
            enabled=True,
            tools=(
                ModelTool(
                    name="aimail_env_probe",
                    description=(
                        "Environment self-test. Call this tool with any short string; it echoes "
                        "the value back and records the calling thread. Use it whenever the user "
                        "asks to probe the tool channel."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {"value": {"type": "string", "description": "Any short string."}},
                        "required": ["value"],
                    },
                    handler=_probe,
                    group="aimail-probe",
                ),
            ),
        )
        accepted = registry.plugin(contribution)
        _append({"event": "install_ok" if accepted else "install_rejected",
                 "namespace": contribution.namespace, "token": token, "at": time.time()})
    except Exception as e:  # a fixture must record WHY it failed, never fail silently
        _append({"event": "install_error", "token": token,
                 "error": f"{type(e).__name__}: {e}", "at": time.time()})
        raise

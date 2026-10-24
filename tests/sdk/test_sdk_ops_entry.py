"""Acceptance of the SDK-side executable gate `aimail.sdk_ops` (SDK domain, 2026-10-04 A' landing).

J4e root cause (binary choice fixed = **(i) skip**, not wrong directory):
1. **Process command contract** (ABI): stdout exactly one line of JSON, exit 0/1/2, three tiers for usage error/import error/call exception;
2. **Delegation correctness**: each op actually sends parameters to the corresponding SDK thin entry (monkeypatch evidence).
   Note (Contract v1.0 §4.1, 2026-10-06): `assemble`/`update`/`teardown` are **self-contained action ops**——
   judgment and orchestration are within the SDK (aligning with TS `register-cli.js`), but orchestration must ultimately **delegate to existing implementations**,
   must not copy logic; old ops remain available (whitelist period).
3. **stdout is not polluted**: All prints from the SDK/dependencies themselves go to stderr — otherwise, the caller's parsing of single-line JSON will break.

Usage: In repository form, run `python3 pysdk/sdk_ops.py <op> --args ...` (the module includes dual-form self-bootstrapping),
and pip form `python -m aimail.sdk_ops` (covered by tests/sdk/release/l2-verify-wheel.sh).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aimail_contract import BINDING_FILE, INBOUND_PATH  # noqa: E402 — contract single source: no literals (ratchet collision)

REPO = Path(__file__).resolve().parent.parent.parent
SDK_OPS = REPO / "pysdk" / "sdk_ops.py"

# manager related env: clear to verify P1 hard gate (no environment fallback)
_MGR_ENV_KEYS = (
    "AIMAIL_MANAGER_ADDRESS",
    "MANAGER_ADDRESS",
    "AIMAIL_MANAGER_EMAIL",
)


def _env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["AIMAIL_HOME"] = str(tmp_path / "aimail")
    for k in _MGR_ENV_KEYS:
        env.pop(k, None)
    return env


def _run(tmp_path: Path, *argv: str, cwd: Path | None = None, env: dict | None = None):
    return subprocess.run(
        [sys.executable, str(SDK_OPS), *argv],
        cwd=str(cwd or REPO),
        env=env or _env(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _envelope(proc) -> dict:
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, f"stdout must be exactly one line of JSON, got {len(lines)} lines: {proc.stdout!r}"
    return json.loads(lines[0])


def test_iter_bindings_ok_is_one_line_json(tmp_path):
    proc = _run(tmp_path, "iter_bindings", "--args", '{"system_id":""}')
    assert proc.returncode == 0, proc.stderr
    env = _envelope(proc)
    assert env["ok"] is True and env["result"] == []


def test_help_exits_0(tmp_path):
    """`--help` must exit 0 (argparse's 0 being swallowed by `or` into default code is a pitfall actually stepped in)."""
    proc = _run(tmp_path, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "usage:" in (proc.stdout + proc.stderr)


def test_unknown_op_exits_2_without_stdout_json(tmp_path):
    proc = _run(tmp_path, "nope", "--args", "{}")
    assert proc.returncode == 2
    assert proc.stdout.strip() == ""  # argparse usage goes to stderr
    assert "invalid choice" in proc.stderr


def test_bad_args_json_is_usage_error(tmp_path):
    proc = _run(tmp_path, "iter_bindings", "--args", "{not json")
    assert proc.returncode == 2
    env = _envelope(proc)
    assert env["ok"] is False and env["kind"] == "usage" and env["exc"] == "UsageError"


def test_missing_required_arg_is_usage_error(tmp_path):
    proc = _run(tmp_path, "ensure_webhook_secret", "--args", "{}")
    assert proc.returncode == 2
    env = _envelope(proc)
    assert env["ok"] is False and env["kind"] == "usage"
    assert "binding" in env["error"]


def test_import_failure_reports_kind_import(tmp_path):
    """Copy gate to isolated dir ⇒ `import aimail_base` not found ⇒ kind=import, exit 1 (not traceback)."""
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    (isolated / "sdk_ops.py").write_text(SDK_OPS.read_text(encoding="utf-8"), encoding="utf-8")
    env = _env(tmp_path)
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, str(isolated / "sdk_ops.py"), "iter_bindings", "--args", "{}"],
        cwd=str(isolated),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 1, proc.stderr
    env_obj = _envelope(proc)
    assert env_obj["ok"] is False and env_obj["kind"] == "import"
    assert env_obj["exc"] == "ModuleNotFoundError"


def test_manager_hard_gate_survives_the_entry(tmp_path):
    """P1 hard gate not obscured by gate: missing manager ⇒ SDK raises ManagerRequiredError ⇒ kind=call and exc preserved."""
    proc = _run(
        tmp_path,
        "register_agent_email",
        "--args",
        json.dumps(
            {
                "gw": {"gateway_url": "http://127.0.0.1:1", "admin_key": "k"},
                "system_id": "s1",
                "email": "a@example.test",
            }
        ),
    )
    assert proc.returncode == 1, proc.stderr
    env = _envelope(proc)
    assert env["ok"] is False and env["kind"] == "call"
    assert env["exc"] == "ManagerRequiredError", env


def test_sdk_prints_do_not_pollute_stdout(tmp_path):
    """SDK's own prints must go to stderr — otherwise the 'exactly one line of JSON' contract is violated."""
    wrapper = tmp_path / "wrap.py"
    wrapper.write_text(
        "import sys, sdk_ops, aimail_base\n"
        "aimail_base.iter_agentmail_configs = lambda sid='': (print('NOISE-FROM-SDK'), [])[1]\n"
        "sys.exit(sdk_ops.main(['iter_bindings', '--args', '{}']))\n",
        encoding="utf-8",
    )
    env = _env(tmp_path)
    env["PYTHONPATH"] = str(REPO / "pysdk")
    proc = subprocess.run(
        [sys.executable, str(wrapper)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    env_obj = _envelope(proc)  # single-line assertion in _envelope
    assert env_obj["ok"] is True
    assert "NOISE-FROM-SDK" in proc.stderr


# ── Delegation correctness (in-process, monkeypatch forensics: parameters really sent to SDK thin entry)──────────


@pytest.fixture()
def sdk(monkeypatch):
    import sdk_ops

    import aimail_base

    calls = {}

    def fake(name, ret):
        def fn(*a, **kw):
            calls[name] = (a, kw)
            return ret

        monkeypatch.setattr(aimail_base, name, fn)

    return sdk_ops, calls, fake


def test_iter_bindings_forwards_system_id(sdk):
    mod, calls, fake = sdk
    fake("iter_agentmail_configs", [{"email": "a@x"}])
    assert mod.main(["iter_bindings", "--args", '{"system_id":"s7"}']) == 0
    assert calls["iter_agentmail_configs"][0] == ("s7",)


def test_ensure_webhook_secret_passes_binding_through(sdk):
    mod, calls, fake = sdk
    fake("ensure_binding_webhook_secret", {"changed": True, "secret": "s", "path": "/p", "reason": "provisioned", "detail": ""})
    binding = {"email": "a@x", "_config_path": "/p"}
    assert mod.main(["ensure_webhook_secret", "--args", json.dumps({"binding": binding})]) == 0
    assert calls["ensure_binding_webhook_secret"][0][0] == binding


def test_resolve_register_webhook_url_arg_order(sdk):
    mod, calls, fake = sdk
    fake("resolve_register_webhook_url", f"http://127.0.0.1:9{INBOUND_PATH}")
    assert mod.main(
        [
            "resolve_register_webhook_url",
            "--args",
            json.dumps({"gw": {"webhook_host": ""}, "local_webhook_url": "http://127.0.0.1:9/x"}),
        ]
    ) == 0
    assert calls["resolve_register_webhook_url"][0] == ({"webhook_host": ""}, "http://127.0.0.1:9/x")


def test_register_agent_email_builds_client_from_gw(sdk, monkeypatch):
    mod, calls, fake = sdk
    import aimail_tools

    seen = {}

    class FakeClient:
        def __init__(self, url, key):
            seen["url"], seen["key"] = url, key

    monkeypatch.setattr(aimail_tools, "_GatewayClient", FakeClient)
    fake("register_agent_email", {"api_key": "ak", "activation_code": "code"})
    rc = mod.main(
        [
            "register_agent_email",
            "--args",
            json.dumps(
                {
                    "gw": {"gateway_url": "http://gw.test", "admin_key": "AK"},
                    "system_id": "s1",
                    "email": "a@x",
                    "webhook_url": f"http://127.0.0.1:9{INBOUND_PATH}",
                    "webhook_secret": "sec",
                    "manager_address": "m@x",
                }
            ),
        ]
    )
    assert rc == 0
    assert seen == {"url": "http://gw.test", "key": "AK"}
    a, kw = calls["register_agent_email"]
    assert a[1:] == ("s1", "a@x")
    assert kw == {
        "webhook_url": f"http://127.0.0.1:9{INBOUND_PATH}",
        "webhook_secret": "sec",
        "manager_address": "m@x",
    }


def test_backfill_binding_returns_path_string(sdk):
    mod, calls, fake = sdk
    fake("backfill_binding", Path("/tmp/x") / BINDING_FILE)
    binding = {"email": "a@x", "_config_path": str(Path("/tmp/x") / BINDING_FILE)}
    rc = mod.main(["backfill_binding", "--args", json.dumps({"binding": binding, "system_id": "s1"})])
    assert rc == 0
    assert calls["backfill_binding"][0] == (binding, "s1")


# ── Contract v1.0 §4.1: minimal cases for self-contained action ops (SDK domain)─────────────────────────
CONVERGED_OPS = ("assemble", "update", "teardown")


def _door_env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["AIMAIL_HOME"] = str(tmp_path / "aimail")
    return env


def test_converged_ops_registered(tmp_path):
    """Three self-contained ops must be registered within the gate, and old ops remain (whitelist period does not break existing calls)."""
    r = subprocess.run([sys.executable, str(SDK_OPS), "version"],
                       capture_output=True, text=True, env=_door_env(tmp_path))
    assert r.returncode == 0, r.stderr
    ops = set(json.loads(r.stdout)["result"]["ops"])
    for name in CONVERGED_OPS:
        assert name in ops, f"missing converged op: {name}"
    for legacy in ("register_agent_email", "iter_bindings", "backfill_binding"):
        assert legacy in ops, f"legacy op lost: {legacy}"


def test_converged_ops_fail_loudly_on_missing_args(tmp_path):
    """Missing params must fail loudly (exit 2 / ok:false / kind:usage), no silent default values."""
    for op in ("assemble", "update"):
        r = subprocess.run([sys.executable, str(SDK_OPS), op, "--args", "{}"],
                           capture_output=True, text=True, env=_door_env(tmp_path))
        assert r.returncode == 2, f"{op}: rc={r.returncode} stderr={r.stderr[:200]}"
        env = json.loads(r.stdout)
        assert env["ok"] is False and env["kind"] == "usage", env


def test_teardown_requires_target_when_acting(tmp_path):
    """teardown has action but missing target ⇒ fail loudly (rc=2/usage), no silent success."""
    r = subprocess.run([sys.executable, str(SDK_OPS), "teardown", "--args", "{}"],
                       capture_output=True, text=True, env=_door_env(tmp_path))
    assert r.returncode == 2, f"rc={r.returncode} stderr={r.stderr[:200]}"
    env = json.loads(r.stdout)
    assert env["ok"] is False and env["kind"] == "usage", env


def test_teardown_noop_when_all_modes_off(tmp_path):
    """Explicitly close all actions ⇒ idempotent return ok:true + actions:[]."""
    args = json.dumps({"mode": {"unregister": False, "whitelist": False}})
    r = subprocess.run([sys.executable, str(SDK_OPS), "teardown", "--args", args],
                       capture_output=True, text=True, env=_door_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["result"]["actions"] == []

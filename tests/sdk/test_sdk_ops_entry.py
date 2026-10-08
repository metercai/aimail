"""SDK 侧可执行门 `aimail.sdk_ops` 的验收（SDK 域，2026-10-04 A′ 落地）。

钉住三件事：
1. **进程命令契约**（ABI）：stdout 恰一行 JSON、exit 0/1/2、用法错/import 错/调用抛异常三分档；
2. **委派正确性**：每个 op 真的把参数送到了对应的 SDK 薄入口（monkeypatch 取证）。
   注（契约 v1.0 §4.1, 2026-10-06）：`assemble`/`update`/`teardown` 为**自持动作 op**——
   判定与编排在 SDK 内（向 TS `register-cli.js` 靠拢），但编排最终必须**委派到既有实现**，
   不得复制逻辑；旧 op 保留可用（白名单期）。
3. **stdout 不被污染**：SDK/依赖自身的打印一律进 stderr —— 否则调用方解析单行 JSON 会炸。

跑法：仓库形态直接 `python3 pysdk/sdk_ops.py <op> --args …`（模块自带双形态自举），
以及 pip 形态 `python -m aimail.sdk_ops`（由 tests/sdk/release/l2-verify-wheel.sh 覆盖）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aimail_contract import BINDING_FILE, INBOUND_PATH  # noqa: E402 — 契约单源: 禁字面量(撞棘轮)

REPO = Path(__file__).resolve().parent.parent.parent
SDK_OPS = REPO / "pysdk" / "sdk_ops.py"

# manager 相关 env：清掉才能验 P1 硬门（不吃环境兜底）
_MGR_ENV_KEYS = (
    "AIMAIL_MANAGER_ADDRESS",
    "AIMAIL_MANAGER",
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
    assert len(lines) == 1, f"stdout 必须恰一行 JSON，实得 {len(lines)} 行: {proc.stdout!r}"
    return json.loads(lines[0])


def test_iter_bindings_ok_is_one_line_json(tmp_path):
    proc = _run(tmp_path, "iter_bindings", "--args", '{"system_id":""}')
    assert proc.returncode == 0, proc.stderr
    env = _envelope(proc)
    assert env["ok"] is True and env["result"] == []


def test_help_exits_0(tmp_path):
    """`--help` 必须 exit 0（argparse 的 0 被 `or` 吞成默认码是实测踩过的坑）。"""
    proc = _run(tmp_path, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "usage:" in (proc.stdout + proc.stderr)


def test_unknown_op_exits_2_without_stdout_json(tmp_path):
    proc = _run(tmp_path, "nope", "--args", "{}")
    assert proc.returncode == 2
    assert proc.stdout.strip() == ""  # argparse 的用法走 stderr
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
    """把门拷到隔离目录 ⇒ `import aimail_base` 找不到 ⇒ kind=import、exit 1（不是 traceback）。"""
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
    """P1 硬门不被门糊掉：缺 manager ⇒ SDK 抛 ManagerRequiredError ⇒ kind=call 且 exc 保留。"""
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
    """SDK 自身打印必须进 stderr —— 否则「恰一行 JSON」的契约不成立。"""
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
    env_obj = _envelope(proc)  # 单行断言在 _envelope 里
    assert env_obj["ok"] is True
    assert "NOISE-FROM-SDK" in proc.stderr


# ── 委派正确性（in-process，monkeypatch 取证：参数真的送到 SDK 薄入口）──────────


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


# ── 契约 v1.0 §4.1：自持动作 op 的最小用例（SDK 域）─────────────────────────
CONVERGED_OPS = ("assemble", "update", "teardown")


def _door_env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["AIMAIL_HOME"] = str(tmp_path / "aimail")
    return env


def test_converged_ops_registered(tmp_path):
    """三个自持 op 必须在门内注册，且旧 op 仍保留（白名单期不破坏既有调用）。"""
    r = subprocess.run([sys.executable, str(SDK_OPS), "version"],
                       capture_output=True, text=True, env=_door_env(tmp_path))
    assert r.returncode == 0, r.stderr
    ops = set(json.loads(r.stdout)["result"]["ops"])
    for name in CONVERGED_OPS:
        assert name in ops, f"missing converged op: {name}"
    for legacy in ("register_agent_email", "iter_bindings", "backfill_binding"):
        assert legacy in ops, f"legacy op lost: {legacy}"


def test_converged_ops_fail_loudly_on_missing_args(tmp_path):
    """缺参必须响亮失败（exit 2 / ok:false / kind:usage），不得静默成分默认值。"""
    for op in ("assemble", "update"):
        r = subprocess.run([sys.executable, str(SDK_OPS), op, "--args", "{}"],
                           capture_output=True, text=True, env=_door_env(tmp_path))
        assert r.returncode == 2, f"{op}: rc={r.returncode} stderr={r.stderr[:200]}"
        env = json.loads(r.stdout)
        assert env["ok"] is False and env["kind"] == "usage", env


def test_teardown_requires_target_when_acting(tmp_path):
    """teardown 有动作但缺目标 ⇒ 响亮失败（rc=2/usage），不得静默当成功。"""
    r = subprocess.run([sys.executable, str(SDK_OPS), "teardown", "--args", "{}"],
                       capture_output=True, text=True, env=_door_env(tmp_path))
    assert r.returncode == 2, f"rc={r.returncode} stderr={r.stderr[:200]}"
    env = json.loads(r.stdout)
    assert env["ok"] is False and env["kind"] == "usage", env


def test_teardown_noop_when_all_modes_off(tmp_path):
    """显式关闭全部动作 ⇒ 幂等返回 ok:true + actions:[]。"""
    args = json.dumps({"mode": {"unregister": False, "whitelist": False}})
    r = subprocess.run([sys.executable, str(SDK_OPS), "teardown", "--args", args],
                       capture_output=True, text=True, env=_door_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["result"]["actions"] == []

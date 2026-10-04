"""CLI → SDK 的**消费形态**闸门（2026-10-04 A′：走 SDK 侧可执行门）。

钉三件事：
1. `cli/repair.py` 不再对那 5 个 op 做**进程内裸 import**（改用门的进程契约）——
   边界改动后两个 CLI（python + rust）只有一条消费形态；
2. 门调用真的能用：真跑 `iter_bindings`（隔离子夹具）拿到列表；
3. **异常语义不被门糊掉**：缺 manager ⇒ 抛出的异常类名仍是 `ManagerRequiredError`，
   且消息不带双前缀（调用方的 `({type(e).__name__}: {e})` 文案逐字不变）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "cli"
sys.path.insert(0, str(CLI))

from aimail_contract import BINDING_FILE  # noqa: E402 — 契约单源: 禁字面量(撞棘轮)
from _common import sdk_ops  # noqa: E402

OPS = (
    "iter_bindings",
    "ensure_webhook_secret",
    "resolve_register_webhook_url",
    "register_agent_email",
    "backfill_binding",
)


def test_repair_no_longer_bare_imports_aimail_base():
    src = (CLI / "repair.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import aimail_base\b", src, re.M), "repair.py 仍进程内裸 import SDK"
    assert not re.search(r"^\s*from aimail_base import\b", src, re.M), "repair.py 仍 from ... import SDK"
    for op in OPS:
        assert f'"{op}"' in src, f"repair.py 未使用门 op: {op}"
    assert "_sdk_ops(" in src


def test_sdk_ops_call_is_registered_in_module_and_used(monkeypatch, tmp_path):
    """门助手只做"怎么调"：不改判定、不做业务分支（结构性断言，防它长成第二个实现）。"""
    src = (CLI / "_common.py").read_text(encoding="utf-8")
    loc = re.search(r"def _sdk_ops_command\(.*?\n(?=\ndef )", src, re.S)
    body = re.search(r"def sdk_ops\(.*?\n(?=\ndef |\n# ──|\Z)", src, re.S)
    assert loc and body, "_common.py 缺少门的定位/调用实现"
    assert "sdk_ops.py" in loc.group(0) and "aimail.sdk_ops" in loc.group(0), \
        "缺少同源优先 / pip 回退两条定位分支"
    assert "subprocess.run" in body.group(0) and "json.loads" in body.group(0)


def test_iter_bindings_via_door_reads_real_binding(tmp_path, monkeypatch):
    """真跑门：隔离子夹具里放一条绑定 ⇒ 门返回 1 条且带 _config_path。"""
    home = tmp_path / "home"
    ah = tmp_path / "aimail"
    sub = ah / "systems" / "s1" / "a_example.test"
    sub.mkdir(parents=True)
    (sub / BINDING_FILE).write_text(
        json.dumps({"agent_id": "a_example", "email": "a@example.test", "system_id": "s1"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AIMAIL_HOME", str(ah))
    res = sdk_ops("iter_bindings", {"system_id": "s1"})
    assert isinstance(res, list) and len(res) == 1
    assert str(res[0].get("_config_path", "")).endswith(BINDING_FILE)


def test_call_error_keeps_exception_name_without_double_prefix(tmp_path, monkeypatch):
    """缺 manager ⇒ 门报 kind=call/exc=ManagerRequiredError ⇒ 助手复原同名异常、消息无前缀。"""
    ah = tmp_path / "aimail"
    (ah / "systems" / "s1").mkdir(parents=True)
    monkeypatch.setenv("AIMAIL_HOME", str(ah))
    for k in ("AIMAIL_MANAGER_ADDRESS", "AIMAIL_MANAGER", "MANAGER_ADDRESS"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(Exception) as ei:
        sdk_ops("register_agent_email", {
            "gw": {"gateway_url": "http://127.0.0.1:1", "admin_key": "k"},
            "system_id": "s1", "email": "a@example.test",
        })
    assert type(ei.value).__name__ == "ManagerRequiredError", type(ei.value).__name__
    assert not str(ei.value).startswith("ManagerRequiredError: "), str(ei.value)


def test_usage_error_is_system_exit(tmp_path, monkeypatch):
    """用法错（未知 op）⇒ SystemExit（与 load_core 失败形态一致，不静默降级）。"""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "aimail"))
    with pytest.raises(SystemExit):
        sdk_ops("nope_not_an_op", {})

"""`check` 命令面的端到端逐字对比（plan v3 S4 切片5 的验收）。

与 `test_cli_parity.py` 的分工：那个骨架对**已实现命令**做严格逐字比对（只放行
时间戳/夹具根前缀）。`check` 的输出里有三类**跨语言必然不同**的东西，所以单独
放这里、并把允许的差异写死在 `_CHECK_ALLOWED` 注释里（不是"大概一样"）：

1. OS/网络错误原文 —— Python 走 urllib/socket，Rust 走 ureq/std::net，错误串天然不同；
   只保留错误原文**之前**的前缀（前缀不同 ⇒ 走了不同分支 ⇒ 仍会红）。
2. 载荷 fix 提示里的脚本路径 —— Python 给源码检出路径，Rust 给部署形态路径
   （`{program_root}/aimail-src/cli/runtime_bundle.py`）。
3. 时间戳（`--json` 内部形态才有；命令面是表视图，正常不出现）—— 仍照
   `test_cli_parity._TS` 归一，防将来有记录带时间。

夹具刻意做成 **hermes 可识别**（`hermes-agent` + `profiles` 标记齐 + root/具名 profile
两套 config.yaml/指针/绑定），这样 L0–L4 全层都会出行：不是只比"空机器上的一行提示"。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from test_cli_parity import REPO, PY_CLI, _env, _norm, _run, rust_bin

sys.path.insert(0, str(REPO / "pysdk"))
from aimail_contract import (  # noqa: E402
    AGENT_SKILL_NAME,
    AGENT_TOOLSET_NAME,
    BINDING_FILE,
    POINTER_FILE,
)

#: OS/网络错误原文前缀（见模块 docstring 第 1 条）。
_ERR_TAIL = re.compile(
    r"(Cannot reach [^\n:]*|Unreachable at [^\n:]*|Port 25 unreachable|HTTP 0|"
    r"unexpected end of file|connection refused[^\n]*): .*",
    re.IGNORECASE,
)
#: 载荷 fix 提示里的脚本路径（见 docstring 第 2 条）。
_RUNTIME_BUNDLE = re.compile(r"\S*/runtime_bundle\.py")


def _cfg_yaml() -> str:
    return (
        "platforms:\n  webhook:\n    enabled: true\n    port: 8646\n"
        "    extra:\n      secret: s3cr3t\n"
        f"platform_toolsets:\n  webhook:\n    - {AGENT_TOOLSET_NAME}\n"
    )


def _write_binding(systems: Path, sid: str, dirname: str, email: str, key: str) -> None:
    d = systems / sid / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / BINDING_FILE).write_text(
        json.dumps({"email": email, "api_key": key, "agent_id": dirname})
    )


def _write_profile(profiles: Path, name: str, email: str) -> None:
    pd = profiles / name
    (pd / "skills" / AGENT_SKILL_NAME).mkdir(parents=True, exist_ok=True)
    (pd / POINTER_FILE).write_text(json.dumps({"system_id": "s1", "email": email}))
    (pd / "config.yaml").write_text(_cfg_yaml())
    (pd / "webhook_subscriptions.json").write_text(
        json.dumps({"aimail-route": "http://127.0.0.1:1/x"})
    )


@pytest.fixture()
def hermes_fixture(tmp_path: Path) -> dict:
    root = tmp_path
    aimail_home = root / "aimail"
    user_home = root / "home"
    hermes = user_home / ".hermes"
    systems = aimail_home / "systems"
    (hermes / "hermes-agent").mkdir(parents=True)
    (hermes / "profiles").mkdir(parents=True)
    (hermes / "skills" / AGENT_SKILL_NAME).mkdir(parents=True)
    user_home.mkdir(parents=True, exist_ok=True)

    (hermes / POINTER_FILE).write_text(
        json.dumps({"system_id": "s1", "email": "a@example.test"})
    )
    (hermes / "config.yaml").write_text(_cfg_yaml())
    (hermes / "webhook_subscriptions.json").write_text(
        json.dumps({"aimail-route": "http://127.0.0.1:1/x"})
    )
    _write_profile(hermes / "profiles", "p1", "b@example.test")

    _write_binding(systems, "s1", "a_example.test", "a@example.test", "key-a")
    _write_binding(systems, "s1", "b_example.test", "b@example.test", "key-b")
    (systems / "s1" / "aimail_gateway.json").write_text(
        json.dumps(
            {
                "gateway_url": "http://127.0.0.1:1",
                "admin_key": "k",
                "system_id": "s1",
                "system_name": "s1",
                "domain": "example.test",
                "system_home": str(hermes),
            }
        )
    )
    return {"root": root, "home": user_home, "aimail_home": aimail_home, "hermes": hermes}


def _normalize(text: str, roots: list) -> str:
    text = _norm(text, roots)
    text = _ERR_TAIL.sub(lambda m: f"{m.group(1)}: <ERR>", text)
    return _RUNTIME_BUNDLE.sub("<RUNTIME_BUNDLE>", text)


@pytest.mark.parametrize("args", [("check", "-s", "s1"), ("check", "-s", "s1", "-v")])
def test_check_command_matches_python_byte_for_byte(hermes_fixture, args):
    """两侧 `aimail check` 的表视图逐字相同（除文档化的三类差异）。"""
    rb = rust_bin()
    if rb is None:
        pytest.skip("rust CLI 未构建（cd cli && cargo build）")

    fx = hermes_fixture
    env = _env(fx)
    # 两侧同源：Hermes 的平台根（Rust 侧读同名环境变量）
    env["AGENT_HOME"] = str(fx["hermes"])

    prc, pso, pse = _run(PY_CLI, args, env)
    rrc, rso, rse = _run([str(rb)], args, env)
    roots = [fx["root"], fx["aimail_home"], fx["home"]]

    pn, rn = _normalize(pso, roots), _normalize(rso, roots)
    # 断言输出**确实是一张表**（不是空/一行提示），否则这条测试会空过
    assert "system" in pn and "gateway" in pn, pn
    assert pn == rn, "\n".join(
        __import__("difflib").unified_diff(pn.splitlines(), rn.splitlines(),
                                          "python", "rust", lineterm="")
    )
    assert prc == rrc
    assert pse == rse

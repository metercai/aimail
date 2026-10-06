"""D5 修订（owner 2026-10-06 裁决·甲）：装配步的**对称规则**。

规则（按架构）：
  · node 宿主（register.kind = node_entry / host_command：dsh · pi · openclaw）
    ⇒ **不带** sdk_install 步：其 SDK/扩展由宿主自己的包管理器安装。
  · python 宿主（register.kind = python_module / python_script：hermes · deerflow）
    ⇒ **带** sdk_install 步：由该步安装 aimailsdk（pip）。

历史：dsh 曾单方面挂 sdk_install/install_dsh（与 pi 同类却例外）⇒ 2026-10-06 删除并对齐。
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REG = json.loads((REPO / "cli" / "platforms.json").read_text(encoding="utf-8"))["platforms"]

NODE_KINDS = {"node_entry", "host_command"}
PY_KINDS = {"python_module", "python_script"}


def _step_kinds(p):
    return [s.get("kind") for s in (p.get("install_steps") or [])]


def test_node_hosts_have_no_sdk_install_step():
    offenders = {k: _step_kinds(p) for k, p in REG.items()
                 if (p.get("register") or {}).get("kind") in NODE_KINDS and "sdk_install" in _step_kinds(p)}
    assert not offenders, f"node 宿主不得带 sdk_install（应由宿主包管理器安装）: {offenders}"


def test_python_hosts_keep_sdk_install_step():
    missing = [k for k, p in REG.items()
               if (p.get("register") or {}).get("kind") in PY_KINDS and "sdk_install" not in _step_kinds(p)]
    assert not missing, f"python 宿主必须有 sdk_install 步: {missing}"


def test_dsh_is_aligned_with_other_node_hosts():
    dsh, pi = REG["dsh"], REG["pi"]
    assert "sdk_install" not in _step_kinds(dsh), "dsh 应与 pi 对齐：不含 sdk_install"
    assert (dsh.get("register") or {}).get("kind") == (pi.get("register") or {}).get("kind") == "node_entry"

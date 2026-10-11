"""D5 修订（owner 2026-10-06 裁决·甲）：装配步的**对称规则**。

规则（按架构）：
  · node 宿主（register.kind = node_entry：dsh · pi · openclaw）
    ⇒ **不带** sdk_install 步：其 SDK/扩展由宿主自己的包管理器安装。
  · python 宿主（register.kind = python_module / python_script：hermes · deerflow）
    ⇒ **带** sdk_install 步：由该步安装 aimailsdk（pip）。

历史：dsh 曾单方面挂 sdk_install/install_dsh（与 pi 同类却例外）⇒ 2026-10-06 删除并对齐。
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REG = json.loads((REPO / "cli" / "platforms.json").read_text(encoding="utf-8"))["platforms"]

NODE_KINDS = {"node_entry"}  # owner 2026-10-11: host_command 形态退役 ⇒ 收紧（不允许回退该形态）
PY_KINDS = {"python_module", "python_script"}


def _step_kinds(p):
    return [s.get("kind") for s in (p.get("install_steps") or [])]


def test_pysdk_only_assembles_python_hosts():
    """§1 装配归属 + 落点(2026-10-11 补证): node 宿主的装配在它自己的包内(node entry /
    共享 mail-core 装配步)⇒ pysdk 侧不得有它们的装配入口或载荷。"""
    src = (REPO / "pysdk" / "install.py").read_text(encoding="utf-8")
    for fn in ("install_dsh", "install_pi", "install_openclaw"):
        assert f"def {fn}(" not in src, f"pysdk/install.py 不该再有 node 宿主装配入口 {fn}(§1)"
    for stray in ("dsh", "pi", "openclaw"):
        assert not (REPO / "pysdk" / stray).exists(), \
            f"pysdk/{stray}/ 不该存在(node 宿主装配归各自包,§1)"


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

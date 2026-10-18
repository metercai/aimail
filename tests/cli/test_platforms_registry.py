"""平台注册表形状测试 —— 数据源 = `aimail platform spec --json`(P2c S3b, 2026-10-16)。

`cli/platforms.json` 已退役(真源 = 宿主模块 `cli/src/hosts/*.rs`);
本文件的断言语义逐字自 tests/sdk/ 迁移,只换数据源:注册表一律经**已构建的 CLI
二进制**导出(黑盒,与被测物同源 ⇒ 断言的是真产物的形状,不是仓里某份文件)。

迁移来源(断言语义不变):
  · test_dsh_register_default_step.py          → dsh/pi register_default 同形 + 排序
  · test_sdk_install_step_contract.py          → node 宿主无 sdk_install / python 宿主有
  · test_hermes_pkgmgr_adapt.py                → hermes host/docker 两步自适配形状 + when 闸
  · test_hermes_toolset_ensure.py(第 3 节)     → 写/读同源 yaml_toolsets glob
  · test_register_name_token_registry.py       → dsh/pi register 模板 {name} token + 计划面

前置:rust 电池已构建 `cli/target/debug/aimail`(run-cli-gates.sh 顺序保证)。
缺二进制 ⇒ 响亮失败(不 SKIP:注册表形状是本域门禁,不是环境可选项)。
"""
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_BIN = _REPO / "cli" / "target" / "debug" / "aimail"
for _d in ("pysdk", os.path.join("pysdk", "hermes")):
    if str(_REPO / _d) not in sys.path:
        sys.path.insert(0, str(_REPO / _d))

import pytest  # noqa: E402


def _spec() -> dict:
    if not _BIN.exists():
        pytest.fail(f"CLI 二进制缺失: {_BIN}(先跑 rust 电池: cargo build)")
    out = subprocess.run([str(_BIN), "platform", "spec", "--json"],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


_SPEC = _spec()
PLATS = _SPEC["platforms"]


# ── dsh register_default 同形(自 test_dsh_register_default_step.py)─────────────
def _steps(name: str) -> list:
    return PLATS[name]["install_steps"]


def _register_default(name: str) -> dict:
    hits = [s for s in _steps(name) if s.get("kind") == "register_default"]
    assert len(hits) == 1, f"{name} 应恰好有一个 register_default 步, 实际 {len(hits)}"
    return hits[0]


def test_dsh_has_register_default_step():
    st = _register_default("dsh")
    assert st["on_error"] == "warn"
    assert st["warn_hint"], "失败提示字段必须在(注册失败不许静默)"


def test_dsh_register_default_shape_matches_pi():
    """形状对齐:顶层字段集合与 when 子字段集合都必须和 pi 一致。"""
    dsh, pi = _register_default("dsh"), _register_default("pi")
    assert set(dsh) == set(pi), (
        f"register_default 节点字段不同: dsh={sorted(dsh)} pi={sorted(pi)}")
    assert set(dsh["when"]) == set(pi["when"])
    assert dsh["when"] == {"cfg_complete": True}, "cfg 未就绪时应跳过(与 pi 同条件)"
    assert dsh["on_error"] == pi["on_error"] == "warn"
    # 提示字段是模板形态:两平台都用 {sid} 占位
    assert "{sid}" in dsh["warn_hint"] and "{sid}" in pi["warn_hint"]


def test_dsh_register_default_runs_after_plugin_ensure():
    """确保步(spawn)必须在注册之前:插件没装上就注册必失败。"""
    kinds = [s.get("kind") for s in _steps("dsh")]
    assert "register_default" in kinds
    assert kinds.index("spawn") < kinds.index("register_default"), kinds


# ── sdk_install 对称(自 test_sdk_install_step_contract.py)─────────────────────
NODE_KINDS = {"node_entry"}
PY_KINDS = {"python_module", "python_script"}


def _step_kinds(p):
    return [s.get("kind") for s in (p.get("install_steps") or [])]


def test_node_hosts_have_no_sdk_install_step():
    offenders = {k: _step_kinds(p) for k, p in PLATS.items()
                 if (p.get("register") or {}).get("kind") in NODE_KINDS and "sdk_install" in _step_kinds(p)}
    assert not offenders, f"node 宿主不得带 sdk_install（应由宿主包管理器安装）: {offenders}"


def test_python_hosts_keep_sdk_install_step():
    missing = [k for k, p in PLATS.items()
               if (p.get("register") or {}).get("kind") in PY_KINDS and "sdk_install" not in _step_kinds(p)]
    assert not missing, f"python 宿主必须有 sdk_install 步: {missing}"


def test_dsh_is_aligned_with_other_node_hosts():
    dsh, pi = PLATS["dsh"], PLATS["pi"]
    assert "sdk_install" not in _step_kinds(dsh), "dsh 应与 pi 对齐：不含 sdk_install"
    assert (dsh.get("register") or {}).get("kind") == (pi.get("register") or {}).get("kind") == "node_entry"


def test_pysdk_only_assembles_python_hosts():
    """§1 装配归属: node 宿主的装配在它自己的包内 ⇒ pysdk 侧不得有它们的装配入口或载荷。"""
    src = (_REPO / "pysdk" / "install.py").read_text(encoding="utf-8")
    for fn in ("install_dsh", "install_pi", "install_openclaw"):
        assert f"def {fn}(" not in src, f"pysdk/install.py 不该再有 node 宿主装配入口 {fn}(§1)"
    for stray in ("dsh", "pi", "openclaw"):
        assert not (_REPO / "pysdk" / stray).exists(), \
            f"pysdk/{stray}/ 不该存在(node 宿主装配归各自包,§1)"


# ── hermes 包管理器自适配(自 test_hermes_pkgmgr_adapt.py)─────────────────────
HERMES_STEPS = PLATS["hermes"]["install_steps"]
HOST_STEP = next(s for s in HERMES_STEPS if s["kind"] == "spawn" and s["argv"][0] == "sh")
DOCKER_STEP = next(s for s in HERMES_STEPS if s["kind"] == "spawn" and s["argv"][0] == "docker")


def _body(step) -> str:
    return step["argv"][-1]


def test_host_step_adapts_uv_then_pip_then_ensurepip():
    body = _body(HOST_STEP)
    # 检测序: uv 优先 → venv pip → ensurepip 兜底(单步内, 执行器零改动)
    assert body.index("command -v uv") < body.index("-m pip --version"), body
    assert body.index("-m pip --version") < body.index("ensurepip"), body
    assert "uv pip install --python" in body, body
    # 目标 = 域内发现的 agent 解释器({python}, C-4: 不预置 venv 字面量)
    assert "P='{python}'" in body, body
    assert "hermes-agent/venv" not in body, body


def test_docker_step_adapts_same_order_inside_container():
    body = _body(DOCKER_STEP)
    assert "docker exec" in " ".join(DOCKER_STEP["argv"][:3])
    # 三径齐全（uv → venv pip + ensurepip ✓）；只断存在性与不变量，不绑旧字面量 ✗
    assert "command -v uv" in body and "ensurepip" in body and "uv pip install" in body
    assert "-m pip install" in body
    # owner 2026-10-07：必须始终装**最新已发布版**（--upgrade ✓），并自证可导入（否则不算真装上 ✓）
    assert "--upgrade" in body, body
    assert "import aimail" in body, body


def test_when_guards_unchanged():
    # host 步: 真机(非 docker) + 平台 marker 目录在才跑(解释器由域内发现, 不入 when); docker 步: 容器态
    assert HOST_STEP["when"] == {
        "path_exists": "{home}/hermes-agent",
        "runtime_not": "docker",
    }
    assert DOCKER_STEP["when"] == {"runtime": "docker"}
    # SDK 安装失败保持 warn 语义(F9: 不阻塞 install 主流程)
    assert HOST_STEP["on_error"] == "warn"
    assert DOCKER_STEP["on_error"] == "warn"


# ── hermes 写/读同源 glob(自 test_hermes_toolset_ensure.py 第 3 节)──────────────
def test_write_path_is_the_file_check_reads(tmp_path):
    """写入目标(ensure_config)与 check 的读取 glob(yaml_toolsets.health_checks)同源。"""
    import yaml
    import ensure_config
    import aimail_contract as _contract

    def _toolsets(cfg):
        return cfg.get("platform_toolsets") or {}

    checks = PLATS["hermes"]["health_checks"]
    toolsets_check = next(c for c in checks if c.get("kind") == "yaml_toolsets")

    # 默认 profile: 写 <HERMES_HOME>/config.yaml
    ensure_config.ensure_profile_config(tmp_path)
    # 具名 profile: 写 <HERMES_HOME>/profiles/<name>/config.yaml
    named = tmp_path / "profiles" / "p1"
    named.mkdir(parents=True)
    ensure_config.ensure_profile_config(named)

    matched = set()
    for pattern in toolsets_check["configs"]:
        matched.update(glob.glob(pattern.replace("{home}", str(tmp_path))))

    for written in (tmp_path / "config.yaml", named / "config.yaml"):
        assert str(written) in matched, (
            f"写入的 {written} 不在 check 的读取 glob {toolsets_check['configs']} 里"
        )
        cfg = yaml.safe_load(written.read_text(encoding="utf-8")) or {}
        pt = _toolsets(cfg)
        assert _contract.AGENT_TOOLSET_NAME in (pt.get("webhook") or []) \
            or _contract.AGENT_TOOLSET_NAME in (pt.get("cli") or [])


# ── node 宿主 register 只声明入口节点(owner 2026-10-17:CLI 无定名责任)──
def test_node_host_register_declares_entry_only():
    """注册器参数归宿主包自带 op 入口 ⇒ 注册表不得回填 args/argv/preset 等宿主 flag。"""
    for plat in ("dsh", "pi", "openclaw"):
        r = PLATS[plat]["register"]
        assert r["kind"] == "node_entry", plat
        for dead in ("args", "argv", "preset"):
            assert dead not in r, f"{plat} register 回填了宿主 flag {dead}"

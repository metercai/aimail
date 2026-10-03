"""hermes 包管理器自适配棘轮(owner 2026-10-03 裁决, 纯配置钉死):

- `pip/uv install aimailsdk` = SDK 的安装(非环境依赖包), 由 `aimail install` 的
  install_steps 承载; 包管理器按 agent 环境自适配, 检测序 = uv → venv pip → ensurepip。
- 钉死两步(host/docker)的自适配形状, 防止回退成硬编码 pip 单径。
"""
import json
from pathlib import Path

REG = json.loads(Path(__file__).resolve().parent.parent.joinpath("cli/platforms.json").read_text())
HERMES_STEPS = REG["platforms"]["hermes"]["install_steps"]
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
    # 目标 = 平台 venv 解释器(uv 装进同一环境)
    assert "P='{home}/hermes-agent/venv/bin/python'" in body, body


def test_docker_step_adapts_same_order_inside_container():
    body = _body(DOCKER_STEP)
    assert "docker exec" in " ".join(DOCKER_STEP["argv"][:3])
    assert body.index("command -v uv") < body.index("-m pip --version"), body
    assert body.index("-m pip --version") < body.index("ensurepip"), body
    assert "P='{container_home}/.venv/bin/python'" in body, body
    # 分支完整性: uv、pip、ensurepip 三径齐全(owner: 两个分支同序)
    assert "uv pip install --python" in body and "ensurepip" in body


def test_when_guards_unchanged():
    # host 步: 真机(非 docker) + venv 在才跑; docker 步: 容器态
    assert HOST_STEP["when"] == {
        "path_exists": "{home}/hermes-agent/venv/bin/python",
        "runtime_not": "docker",
    }
    assert DOCKER_STEP["when"] == {"runtime": "docker"}
    # SDK 安装失败保持 warn 语义(F9: 不阻塞 install 主流程)
    assert HOST_STEP["on_error"] == "warn"
    assert DOCKER_STEP["on_error"] == "warn"

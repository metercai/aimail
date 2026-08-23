#!/usr/bin/env python3
"""runtime_core — 仓库侧维护脚本的运行时核心加载器(单一实现)。

用途: scripts/ 与 bin/ 下的维护脚本注册/解注册/测试 agent 时需要
导入运行时核心(aimail_base / aimail_tools / gateway_api)与平台
适配层(amail_base / aimail_hermes)。本模块统一解析核心目录并挂到
sys.path,消除各脚本散落的 `sys.path.insert(... "tools"...)` 仓路径耦合。

源解析(repo 优先 > pip 兜底):
  1. 仓库 tools/(维护脚本 = 仓库自身工具链,应用仓库当前代码;
     若 pip 优先,开发期装了旧版 aimail 包会用错代码)
  2. pip aimail(site-packages/aimail,仓库 tools/ 缺失时兜底)

用法(各维护脚本头部):
    import os, sys
    _SCRIPTS = os.path.dirname(os.path.abspath(__file__))          # 或按层级上溯
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    from runtime_core import load_core, load_adapter

    load_core()                      # 核心裸导入可用: import aimail_base ...
    load_adapter("openclaw")         # 可选: 适配层裸导入可用: import amail_base
    import aimail_base as _base
"""
from __future__ import annotations

import os
import sys

_CORE_DIR_NAME = "tools"
_ADAPTERS = ("openclaw", "hermes", "deer-flow")


def _repo_tools_dir() -> str:
    """仓库 tools/ 绝对路径(本文件在 scripts/ 下,上溯一级)。"""
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", _CORE_DIR_NAME))


def _pip_core_dir() -> str | None:
    """pip aimail 包目录(载荷根,含 aimail_base.py);未安装返回 None。"""
    try:
        import aimail  # type: ignore
        pkg = os.path.dirname(os.path.abspath(aimail.__file__))
        if os.path.isfile(os.path.join(pkg, "aimail_base.py")):
            return pkg
    except Exception:
        pass
    return None


def resolve_core_dir() -> str:
    """返回运行时核心目录(repo tools/ 优先 > pip aimail)。"""
    repo = _repo_tools_dir()
    if os.path.isfile(os.path.join(repo, "aimail_base.py")):
        return repo
    pip = _pip_core_dir()
    if pip:
        return pip
    raise SystemExit("ERROR: 运行时核心未找到(仓库 tools/ 与 pip aimail 均不可用)")


def load_core() -> str:
    """把核心目录挂到 sys.path(幂等),返回核心目录。

    挂上后核心模块的裸导入即可用: import aimail_base / aimail_tools /
    gateway_api(_aimail_bootstrap 亦在核心目录)。
    """
    d = os.path.abspath(resolve_core_dir())
    if d not in sys.path:
        sys.path.insert(0, d)
    return d


def load_adapter(name: str) -> str:
    """把平台适配层子目录挂到 sys.path(幂等),返回适配层目录。

    name ∈ openclaw|hermes|deer-flow。挂上后适配层裸导入即可用
    (import amail_base / aimail_hermes)。核心目录会一并挂上
    (适配层依赖核心)。
    """
    if name not in _ADAPTERS:
        raise SystemExit(f"ERROR: 未知适配层 {name}(可选: {', '.join(_ADAPTERS)})")
    load_core()
    d = os.path.abspath(os.path.join(resolve_core_dir(), name))
    if not os.path.isdir(d):
        raise SystemExit(f"ERROR: 适配层目录缺失: {d}")
    if d not in sys.path:
        sys.path.insert(0, d)
    return d


if __name__ == "__main__":
    print(f"core\t{load_core()}")

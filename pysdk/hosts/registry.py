# -*- coding: utf-8 -*-
"""宿主注册表 —— Python 宿主的单真源(tssdk hosts/registry.ts 的 Python 对等物)。

加一个新 python 宿主 = 这里加一条目 + 一个 `<adapter_dir>/` 适配目录
(与 tssdk "add one entry here + one src/{host}/ entry dir" 同构)。

字段纪律:
  · `home_dir` / `marker` 与 CLI 宿主表 `cli/src/hosts/{hermes,deerflow}.rs`
    的 `home_dir` / `detect.markers` 逐字段一致(两侧改必须同步)。
  · `adapter_dir` = SDK 内适配目录名(注意 deerflow 目录名带连字符,
    不能作包名 ⇒ 适配模块走 sys.path 扁平导入,不 import 本目录)。
"""
from __future__ import annotations

from typing import Dict

HostSpec = Dict[str, str]

HOSTS: Dict[str, HostSpec] = {
    "hermes": {
        # CLI 侧: cli/src/hosts/hermes.rs — markers=["hermes-agent"], home_dir=".hermes"
        "label": "Hermes",
        "home_dir": ".hermes",
        "marker": "hermes-agent",
        "adapter_dir": "hermes",
        # 注册入口(与 CLI Register::PythonModule 一致)
        "register_module": "aimail_hermes",
        "register_fn": "_auto_register_email",
        "install_fn": "install_hermes",
    },
    "deerflow": {
        # CLI 侧: cli/src/hosts/deerflow.rs — markers=["backend/app/gateway"], home_dir=".deer-flow"
        "label": "DeerFlow",
        "home_dir": ".deer-flow",
        "marker": "backend/app/gateway",
        "adapter_dir": "deer-flow",
        "register_module": "manage",
        "register_fn": "register_agents",
        "install_fn": "install_deerflow",
    },
}


def host_ids() -> list:
    """全部已知 python 宿主 id(注册表键,顺序稳定)。"""
    return sorted(HOSTS)


def get_host(host_id: str) -> HostSpec | None:
    """按 id 取宿主规格;未知 id ⇒ None(调用方响亮失败,不静默回落)。"""
    return HOSTS.get(str(host_id or "").strip())

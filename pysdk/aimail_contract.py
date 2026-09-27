# -*- coding: utf-8 -*-
"""aimail_contract — AIMail agent 侧契约常量(Python 侧唯一副本)。

⚠ 值必须等于仓根 ``contract/aimail-contract.json``(单一真源)。
本文件是**硬编码常量 + 门禁断言**形态(不是运行时读文件):轮子(hatchling
force-include)/运行时捆绑都是自包含的,发布产物里没有仓根 contract/ 目录,
运行时读清单会在 pip/捆绑形态下直接崩 —— 所以真源落盘、常量硬编码,由
``tests/contract/check-contract-single-source.py`` 在 L0 门禁里逐项断言两者相等
(漂移即红并给 file:line),``verify_against_manifest()`` 是同一断言的库形态。

边界(owner 裁决 2026-09-27):合法的 ``aimail`` 面 = 产品/仓库/框架/CLI 名 +
``~/.aimail`` + ``AIMAIL_*`` 环境变量 —— 这些**不是** agent 内部契约名, 不要
按本模块判违约。本模块只约束 agent 内部契约面: skill/toolset 注册名、绑定
文件名、指针文件名、入站路径。
"""

from __future__ import annotations

import json
import os

# ── 契约值(必须等于 contract/aimail-contract.json)─────────────────────────

#: 四个平台(openclaw / dsh / pi / deer-flow)的固定入站路径。
INBOUND_PATH = "/aimail/inbound"

#: hermes 是唯一例外:入站挂在 hermes 网关自身的 webhook 路由上。
HERMES_INBOUND_PATH = "/webhooks/aimail-inbound"

#: hermes 网关路由名(webhook_subscriptions.json 的 route 键)。
HERMES_ROUTE_NAME = "aimail-inbound"

#: agent 内部 skill 注册名 == SKILL.md frontmatter ``name:``(目录名亦同)。
AGENT_SKILL_NAME = "agentmail"

#: agent 内部 toolset 注册名 == SKILL.md frontmatter ``toolset:`` / hermes
#: ``platform_toolsets`` 键名。
AGENT_TOOLSET_NAME = "agentmail"

#: 每个地址的绑定文件名(布局 systems/{sid}/{cleaned_addr}/agentmail.json)。
BINDING_FILE = "agentmail.json"

#: 系统指针文件名(如 ~/.hermes/.agentmail、~/.deer-flow/.agentmail)。
POINTER_FILE = ".agentmail"

#: 入站监听端口默认值 —— **端口可配, 路径不可变**。
INBOUND_PORTS = {"dsh": 9099, "pi": 9101, "deerflow": 8001}

#: 桥(bridge)默认转发路径(与 hermes_inbound_path 同值; 桥在另一仓, 下批接)。
BRIDGE_DEFAULT_PATH = "/webhooks/aimail-inbound"

#: 清单相对仓根的路径(门禁/库校验用)。
MANIFEST_REL_PATH = os.path.join("contract", "aimail-contract.json")

# 清单键 → 本模块属性(常量一致性断言用; Rust 侧本批未覆盖 = GAP)。
MANIFEST_MAP = {
    "inbound_path": "INBOUND_PATH",
    "hermes_inbound_path": "HERMES_INBOUND_PATH",
    "hermes_route_name": "HERMES_ROUTE_NAME",
    "agent_skill_name": "AGENT_SKILL_NAME",
    "agent_toolset_name": "AGENT_TOOLSET_NAME",
    "binding_file": "BINDING_FILE",
    "pointer_file": "POINTER_FILE",
    "inbound_ports": "INBOUND_PORTS",
    "bridge_default_path": "BRIDGE_DEFAULT_PATH",
}


def inbound_url(port: int, host: str = "127.0.0.1") -> str:
    """拼本机入站接收端点(非 hermes 平台)。"""
    return "http://%s:%d%s" % (host, int(port), INBOUND_PATH)


def hermes_inbound_url(port: int, host: str = "127.0.0.1") -> str:
    """拼 hermes 进程内入站接收端点。"""
    return "http://%s:%d%s" % (host, int(port), HERMES_INBOUND_PATH)


def load_manifest(path: str | None = None) -> dict:
    """读清单。path 缺省 = 从本文件位置推导仓根(设 AIMAIL_CONTRACT_MANIFEST
    可覆盖 —— 门禁的三态演练/负例用)。文件缺失/不可解析时抛异常, 调用方决定
    退出码(门禁判 rc=2 = 判不了, 不假装通过)。"""
    p = path or os.environ.get("AIMAIL_CONTRACT_MANIFEST", "")
    if not p:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", MANIFEST_REL_PATH)
    with open(os.path.abspath(p), "r", encoding="utf-8") as f:
        return json.load(f)


def verify_against_manifest(path: str | None = None) -> list:
    """逐项断言本模块常量 == 清单。返回不符项描述列表(空 = 一致)。"""
    m = load_manifest(path)
    bad = []
    for key, attr in MANIFEST_MAP.items():
        if key not in m:
            bad.append("manifest 缺键: %s" % key)
            continue
        want, got = m[key], globals()[attr]
        if want != got:
            bad.append("%s: 常量 %r != 清单 %r" % (attr, got, want))
    return bad


if __name__ == "__main__":  # pragma: no cover - 手工自检
    _bad = verify_against_manifest()
    print("aimail_contract vs manifest: %s" % ("OK" if not _bad else "; ".join(_bad)))
    raise SystemExit(1 if _bad else 0)

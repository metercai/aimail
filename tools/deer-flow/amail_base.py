#!/usr/bin/env python3
"""amail_base.py — DeerFlow 适配层（第三实例,2026-08-18）。

与 OpenClaw amail_base.py 同构（三件套）:
  ① 平台实现: config 加载 / profile 目录 / set_agent_context
  ② 注入点赋值 + 能力开关: PERSONA_SUPPORTED = False
  ③ 平台注册: 身份注入(deerflow/{ver})

共享核心(tools/aimail_base)已提供平台无关 set_agent_context
(按 agentmail.json 布局扫描),本适配层在 OpenClaw 基础上仅做:
  - 转发共享函数(与 OpenClaw 同款)
  - 注入 DeerFlow 身份(X-AIMail-Agent: deerflow/...)

入站(2026-08-18 重构):预处理并入 DeerFlow 本地 gateway(8001)进程,
aimail_inbound router 直接 import 本适配层获得注入点;独立接收进程
amail_deerflow_bridge.py(8798)已退役删除——链路 gateway→bridge→
8001 /agentmail/inbound(验签+预处理+start_run 投递),仿 Hermes 进程内预处理。

布局: ~/.agentmail/systems/{sid}/{cleaned_addr}/agentmail.json(共享)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# ── aimail 运行时核心定位(bundle / site-packages / 仓库 dev;不再依赖仓库路径)──
def _amail_bootstrap():
    """定位 aimail 运行时核心,装配 sys.path。"""
    import importlib.util as _ilu
    _here = os.path.dirname(os.path.abspath(__file__))
    for _d in (_here, os.path.dirname(_here)):
        _p = os.path.join(_d, "_aimail_bootstrap.py")
        if os.path.isfile(_p):
            _spec = _ilu.spec_from_file_location("_aimail_bootstrap", _p)
            if _spec is None or _spec.loader is None:
                continue
            _m = _ilu.module_from_spec(_spec)
            sys.modules["_aimail_bootstrap"] = _m
            _spec.loader.exec_module(_m)
            _core = _m.ensure_core(_here)
            if _core is None:
                raise ImportError("aimail runtime core not found — set AIMAIL_RUNTIME_DIR")
            return _core
    raise ImportError("aimail runtime core not found — set AIMAIL_RUNTIME_DIR")


_amail_bootstrap()

import aimail_base as _ab          # noqa: E402  (共享核心)
import aimail_tools as _tools      # noqa: E402  (X-AIMail-Agent 身份注入)


# ── DeerFlow 身份检测(只报真实检测结果)───────────────────────────
def _detect_deerflow_version() -> str:
    """检测 DeerFlow 版本:优先 DEER_FLOW_HOME/backend/pyproject.toml,
    失败回退 unknown。"""
    for pp in (
        os.path.join(os.environ.get("DEER_FLOW_HOME", ""), "backend", "pyproject.toml"),
        os.path.join(os.path.expanduser("~"), "deer-flow", "backend", "pyproject.toml"),
        os.path.join(os.path.expanduser("~"), "deer-flow", "pyproject.toml"),
    ):
        if pp and os.path.isfile(pp):
            try:
                with open(pp) as f:
                    for line in f:
                        if line.strip().startswith("version"):
                            v = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if v:
                                return v
            except Exception:
                pass
    return "unknown"


# ── 注入身份(与 OpenClaw 同模式: 目录检测会误判,必须显式注入)────
_tools._AGENT_IDENTITY_OVERRIDE = f"deerflow/{_detect_deerflow_version()}"


# ── ① 平台实现 ──────────────────────────────────────────────────
def _deerflow_profile_dir() -> Optional[str]:
    """profile 目录: 当前 system 目录(system_id 解析同 OpenClaw 指针)。"""
    sid = os.environ.get("AIMAIL_SYSTEM_ID", "")
    if sid:
        return str(Path.home() / ".agentmail" / "systems" / sid)
    return None


def set_agent_context(agent_id: str, system_id: str = "") -> None:
    """把当前 agent 的 config 挂到公共核心注入点(转发共享实现)。"""
    _ab.set_agent_context(agent_id, system_id)


def make_client(api_key: str = "", system_id: str = ""):
    """Gateway 客户端(aimail_tools._GatewayClient,全方法集)。

    api_key 缺省时从当前 agent 的 agentmail.json 读取。
    """
    cfg = load_agent_config_for_key(system_id)
    gw_url = (cfg or {}).get("gateway_url", "")
    key = api_key or ((cfg or {}).get("api_key", ""))
    return _tools._GatewayClient(gw_url, key)


def load_agent_config_for_key(system_id: str = "") -> Optional[dict]:
    """读取当前 system 的 gateway 配置(agentmail_gateway.json)。"""
    try:
        sid = system_id or os.environ.get("AIMAIL_SYSTEM_ID", "")
        path = Path.home() / ".agentmail" / "systems" / sid / "agentmail_gateway.json"
        if path.is_file():
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def load_gateway_config(system_id: str = "") -> Optional[dict]:
    """gateway 连接配置(转发共享 helper)。"""
    return _ab._load_gateway_config(system_id)


def detect_system_id() -> str:
    """系统身份: 指针文件唯一来源(~/.deer-flow/.agentmail 或 env)。"""
    sid = os.environ.get("AIMAIL_SYSTEM_ID", "")
    if sid:
        return sid
    for ptr in (Path.home() / ".deer-flow" / ".agentmail",
                Path.home() / ".agentmail" / ".agentmail"):
        d = _ab._read_pointer(ptr)
        if d.get("system_id"):
            return d["system_id"]
    return ""


def load_agents_registry(system_id: str) -> dict:
    """扫描地址键 agentmail.json,重建 {email → agent_id} 路由映射(共享布局)。"""
    registry = {}
    sys_dir = Path.home() / ".agentmail" / "systems" / system_id
    if sys_dir.is_dir():
        for addr_dir in sorted(sys_dir.iterdir()):
            aj = addr_dir / "agentmail.json"
            if not aj.is_file():
                continue
            try:
                cfg = json.loads(aj.read_text())
                email = cfg.get("email", "")
                agent_id = cfg.get("agent_id", "")
                if email and agent_id:
                    registry[email] = agent_id
            except Exception:
                pass
    return registry


# ── ② 注入点 + 能力开关 ─────────────────────────────────────────
_ab.PERSONA_SUPPORTED = False        # DeerFlow 无 persona 派生地址概念
_ab._PROFILE_DIR_RESOLVER = _deerflow_profile_dir


# ── ③ 身份注入(在 ② 之上,同 OpenClaw 模式)──────────────────────
# 注:入站预处理并入 8001 进程后,dispatch_to_deerflow(原 ③)已删除;
# 投递由 deer-flow backend 的 aimail_inbound router 内部 start_run 完成。

# ── 转发共享核心(复用面,与 OpenClaw 同款)────────────────────────
preprocess_mail_payload = _ab.preprocess_mail_payload
process_inbound_mail = _ab.process_inbound_mail
parse_amail_persona = _ab.parse_amail_persona
_extract_board_gateway = _ab._extract_board_gateway
register_board_gateway = _ab._register_board_gateway
store_board_credential = _ab._store_board_credential
email_for_agent = _ab.email_for_agent
register_agent_email = _ab.register_agent_email
deregister_agent_email = _ab.deregister_agent_email
load_agent_config = _ab.load_agent_config
render_message = _ab.render_message
agent_for_email = _ab.route_agent_for_email

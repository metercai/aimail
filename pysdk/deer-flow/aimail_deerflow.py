#!/usr/bin/env python3
"""aimail_deerflow.py — DeerFlow 适配层。

与 aimail_hermes 同构（三件套）:
  ① 平台实现: config 加载 / profile 目录 / set_agent_context
  ② 注入点赋值 + 能力开关: PERSONA_SUPPORTED = False
  ③ 平台注册: 身份注入(deerflow/{ver})

共享核心(pysdk/aimail_base)已提供平台无关 set_agent_context
(按 agentmail.json 布局扫描),本适配层仅做:
  - 转发共享函数(与 Hermes 同款)
  - 注入 DeerFlow 身份(X-AIMail-Agent: deerflow/...)

入站:预处理在 DeerFlow 本地 gateway(8001)进程内完成,
aimail_inbound router 直接 import 本适配层获得注入点;
链路 gateway→bridge→8001 /aimail/inbound(验签+预处理+start_run 投递)。

布局: ~/.aimail/systems/{sid}/{cleaned_addr}/agentmail.json(共享)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# ── aimail 运行时核心定位(bundle / site-packages / 仓库 dev;不再依赖仓库路径)──
def _aimail_bootstrap():
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


_aimail_bootstrap()

import aimail_base as _ab          # noqa: E402  (共享核心)
import aimail_tools as _tools      # noqa: E402  (X-AIMail-Agent 身份注入)

# ── 宿主识别(移植 tssdk "activate on entry load"): declared=deerflow, 入口加载时
#    复验环境。never raises(装错宿主只 warn); home 语义与 CLI 对齐(DEER_FLOW_HOME
#    或 ~/.deer-flow; marker backend/app/gateway 在其下)。_aimail_bootstrap 已把
#    core 挂 sys.path ⇒ hosts 子包可 import(pip 走 aimail.hosts, dev 直接 hosts)。
try:
    from aimail import hosts as _aimail_hosts
except ImportError:
    import hosts as _aimail_hosts
_HOST_VERIFY = _aimail_hosts.activate_host(
    "deerflow", os.environ.get("DEER_FLOW_HOME") or os.path.expanduser("~/.deer-flow"))


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
        return str(Path(_ab.aimail_home()) / "systems" / sid)
    return None


def _deerflow_soul_md(config=None) -> str:
    """SOUL.md per-agent: 锚定该 agent 自己的地址目录。

    DeerFlow「目录即 agent」(SOUL.md/agents/ — manage.py 目录真相源);SDK
    自持的唯一 per-agent 目录 = save_agent_config 落盘绑定的地址键目录
    systems/{sid}/{cleaned_addr}/,SOUL 锚在那里:<dir>/SOUL.md。缺失 ⇒ ''
    (未作者 SOUL 的 agent 渲染空,文档化)。全 agents/ 树扫描归上游
    DeerFlow(manage.discover 简化 stub,「扩展扫描留待后续」),不在此复制。
    """
    if not config:
        return ""
    sid = str(config.get("system_id") or "")
    email = str(config.get("email") or "")
    if not (sid and email):
        return ""
    soul = Path(_ab._aimail_system_dir(sid)) / _ab._clean_agent_dir_name(email) / "SOUL.md"
    if soul.is_file():
        return soul.read_text(encoding="utf-8")
    return ""


def _deerflow_skills(config=None) -> list:
    """skills 列表(per-agent)。DeerFlow「目录即 agent」:skills 与 SOUL.md
    同锚在 agent 自己的地址目录。枚举 systems/{sid}/{cleaned_addr}/skills/
    下的子目录(每 skill 一子目录,与 SOUL 同锚、与 pi 同款)。缺失 ⇒ []
    (不臆造;与 SOUL 缺失⇒'' 同形)。全平台 agents/ 树扫描归上游 DeerFlow
    (manage.discover 简化 stub,「扩展扫描留待后续」),不在此复制。"""
    if not config:
        return []
    sid = str(config.get("system_id") or "")
    email = str(config.get("email") or "")
    if not (sid and email):
        return []
    skills_dir = Path(_ab._aimail_system_dir(sid)) / _ab._clean_agent_dir_name(email) / "skills"
    try:
        return sorted(
            e.name for e in skills_dir.iterdir()
            if e.is_dir() and not e.name.startswith(".")
        )
    except OSError:
        return []


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
    """读取当前 system 的 gateway 配置(aimail_gateway.json)。"""
    try:
        sid = system_id or os.environ.get("AIMAIL_SYSTEM_ID", "")
        path = Path(_ab.aimail_home()) / "systems" / sid / "aimail_gateway.json"
        if path.is_file():
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def load_gateway_config(system_id: str = "") -> Optional[dict]:
    """gateway 连接配置(转发共享 helper)。"""
    return _ab._load_gateway_config(system_id)


# 转发共享核心函数(AUDIT-1 P0-2:manage.py 曾直调 _base.aimail_home()
# AttributeError——adapters 只转发声明过的符号)
aimail_home = _ab.aimail_home


def detect_system_id() -> str:
    """系统身份: 指针文件唯一来源(~/.deer-flow/.agentmail 或 env)。"""
    sid = os.environ.get("AIMAIL_SYSTEM_ID", "")
    if sid:
        return sid
    for ptr in (Path.home() / ".deer-flow" / ".agentmail",
                Path(_ab.aimail_home()) / ".agentmail"):
        d = _ab._read_pointer(ptr)
        if d.get("system_id"):
            return d["system_id"]
    return ""

# ── ② 注入点 + 能力开关 ─────────────────────────────────────────
_ab.PERSONA_SUPPORTED = False        # DeerFlow 无 persona 派生地址概念
_ab._PROFILE_DIR_RESOLVER = _deerflow_profile_dir
# per-agent SOUL/skills(填 deer-flow 原先 0 注入的缺口: SOUL 与 skills 均按
# agent 地址目录解析;skills 枚举该目录 skills/ 子目录,缺失 ⇒ [])
_ab._SOUL_PROVIDER = _deerflow_soul_md
_ab._SKILLS_PROVIDER = _deerflow_skills
# 注入后自检: 指针解析不出来时入站预处理会降级(邮件照常投递,丢画像/附件下载),
# 在启动处留一条 warn,而不是每封邮件才报。
_ab.check_profile_pointer()


# ── ③ 身份注入(在 ② 之上,同 OpenClaw 模式)──────────────────────
# 注:入站预处理并入 8001 进程后,dispatch_to_deerflow(原 ③)已删除;
# 投递由 deer-flow backend 的 aimail_inbound router 内部 start_run 完成。

# ── 转发共享核心(复用面,与 OpenClaw 同款)────────────────────────
preprocess_mail_payload = _ab.preprocess_mail_payload
process_inbound_mail = _ab.process_inbound_mail
parse_aimail_persona = _ab.parse_aimail_persona
_extract_board_gateway = _ab._extract_board_gateway
register_board_gateway = _ab._register_board_gateway
store_board_credential = _ab._store_board_credential
email_for_agent = _ab.email_for_agent
register_agent_email = _ab.register_agent_email
deregister_agent_email = _ab.deregister_agent_email
load_agent_config = _ab.load_agent_config
render_message = _ab.render_message
agent_for_email = _ab.route_agent_for_email

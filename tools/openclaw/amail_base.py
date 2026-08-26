#!/usr/bin/env python3
"""amail_base — OpenClaw 接入复用层.

复用 Hermes 的 aimail_base.py（preprocess/parse/persona/board）与
scripts/gateway_api.py（标准 amail API 客户端），仅替换 config 加载与
目录约定为 OpenClaw 形态：

  ~/.agentmail/systems/{system_id}/        ← 独立激活产生的系统目录
      agentmail_gateway.json               ← 网关配置（激活时写入，含 mode/bridge_port）
      {cleaned_addr}/agentmail.json        ← 每 agent 配置（api_key + agent_id，地址键）

不修改 aimail-gateway，不修改 Hermes 代码 —— 只做运行时配置源替换。
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

import aimail_base as _ab          # noqa: E402  (Hermes 复用层)
import gateway_api as _gw             # noqa: E402  (标准 API 客户端)
import aimail_tools as _tools      # noqa: E402  (X-AIMail-Agent 身份注入)
# 共享核心目录(aimail_board.py 等同目录)——取导入源实际位置,与部署形态无关
_TOOLS = os.path.dirname(os.path.abspath(_ab.__file__))

# ── X-AIMail-Agent 身份注入 ────────────────────────────────────────
# 多 agent 共存机器上,aimail_tools 的"目录存在"自动检测会把
# OpenClaw 进程误判为 hermes(~/.hermes 目录同样存在且 registry 在前)。
# 适配层显式注入 OpenClaw 身份:openclaw/{CLI 版本}。
_oc_ver = "unknown"
try:
    import subprocess as _sp
    _r = _sp.run(["openclaw", "--version"], capture_output=True, text=True, timeout=5)
    _out = (_r.stdout or _r.stderr).strip()
    import re as _re
    _m = _re.search(r"([0-9][0-9.]*-?[0-9]*)", _out)
    if _m:
        _oc_ver = _m.group(1)
except Exception:
    pass
_tools._AGENT_IDENTITY_OVERRIDE = f"openclaw/{_oc_ver}"

# ── 平台注入（公共核心注入点；Hermes → tools/hermes/aimail_hermes.py 对称）──
def _openclaw_profile_dir() -> Optional[str]:
    """公共核心 _PROFILE_DIR_RESOLVER 的 OpenClaw 版：当前 system 目录。"""
    sid = detect_system_id()
    return str(system_dir(sid)) if sid else None


# 公共核心隐含依赖（store_inbound_message/_log_amail/_GatewayClient）已由
# aimail_base 函数级 import 自解析，无需适配层注入。
# 注入点设置（OpenClaw 平台实现；personas 用公共默认空）
_ab._PROFILE_DIR_RESOLVER = _openclaw_profile_dir
# ── 系统能力声明（跨系统共享开关，Hermes 默认 True 不变）────────
# OpenClaw 不支持 persona（角色 = 独立 agentId）→ 设 False。
# preprocess 内部按此开关处理派生地址：False 时归一为基础地址。
_ab.PERSONA_SUPPORTED = False

# ── 目录与配置 ─────────────────────────────────────────────────

system_dir = _ab._agentmail_system_dir   # 目录约定统一：共享核心同一 helper


def load_gateway_config(system_id: str = "") -> Optional[dict]:
    """读取 agentmail_gateway.json（{gateway_url, admin_key, domain, system_id, system_name}）。"""
    return _gw.load_gateway_config(system_id)


agent_config_path = _ab._agent_config_path  # 地址键 per-agent 配置路径（共享）


def load_agents_registry(system_id: str) -> dict:
    """扫描地址键 agentmail.json，重建 {email → agent_id} 路由映射。"""
    registry = {}
    sys_dir = system_dir(system_id)
    if sys_dir.is_dir():
        for addr_dir in sys_dir.iterdir():
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


def load_agent_config(agent_id: str, system_id: str = "") -> Optional[dict]:
    """按 agentId 找地址键 config（agentmail.json 中 agent_id 匹配）。

    2026-08-18 起转发共享核心实现（tools/aimail_base.load_agent_config，
    平台无关布局扫描）——单一权威。
    """
    return _ab.load_agent_config(agent_id, system_id)


def save_agent_config(agent_id: str, config: dict, system_id: str = "") -> None:
    if not system_id:
        system_id = config.get("system_id", detect_system_id())
    email = config.get("email", "")
    if not email:
        raise ValueError("save_agent_config requires config['email']")
    config = dict(config)
    config["agent_id"] = agent_id
    p = agent_config_path(system_id, email)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")


def write_system_pointer(system_id: str, email: str = "") -> None:
    """Write the OpenClaw agent pointer (~/.openclaw/.agentmail).

    Single source of system identity for MCP tools — must be refreshed
    whenever a new system is activated, otherwise MCP keeps resolving a
    stale (deleted) system_id.
    """
    _ab._write_pointer(Path.home() / ".openclaw" / ".agentmail", system_id, email)


def detect_system_id() -> str:
    """Resolve the OpenClaw system id from the agent pointer file.

    ~/.openclaw/agents/{agent_id}/agent/.agentmail names the system
    (agent_id from AIMAIL_AGENT_ID, default "main").  System identity is
    fixed by config — env override is intentionally NOT supported:
    switching system_id must be an explicit config change, not a process
    env tweak.  Never scan ~/.agentmail (that picked the wrong system
    before, e.g. OpenClaw replying as agent.vfy@).
    """
    pointer = Path.home() / ".openclaw" / ".agentmail"
    sid = _ab._read_pointer(pointer).get("system_id", "")
    if sid:
        return sid
    raise SystemExit(
        "no .agentmail pointer at "
        + str(pointer)
        + " - system identity must be explicit"
    )


def load_openclaw_hooks() -> Optional[dict]:
    """读取 ~/.openclaw/openclaw.json 的 hooks 块（token/path）。"""
    p = Path.home() / ".openclaw" / "openclaw.json"
    if p.is_file():
        try:
            cfg = json.loads(p.read_text())
            hooks = cfg.get("hooks") or {}
            if hooks.get("enabled") and hooks.get("token"):
                return hooks
        except Exception:
            pass
    return None


# ── agent 上下文切换（转发共享核心实现,2026-08-18）──────────────
# _ACTIVE_AGENT_CONFIG 在 tools/aimail_base.py 维护;本地仅保留镜像
# 引用(amail.py _patch_config 读取),不再有独立 _openclaw_profile_config。

_ACTIVE_AGENT_CONFIG: Optional[dict] = None


def set_agent_context(agent_id: str, system_id: str = "") -> None:
    """把当前 agent 的 config 挂到公共核心的注入点上（转发共享实现）。

    preprocess_mail_payload()（aimail_base）与 6 工具函数
    （aimail_tools）内部都调用 _load_profile_config() —— 公共版读
    _CONFIG_LOADER 注入点，共享 set_agent_context 设置后两处同时生效
    （同一函数对象）。同时设 AIMAIL_AGENT_EMAIL（共享 _resolve_agent_email
    的第一优先来源）——日志 agentmail.{email}.log 按 agent 落位。

    2026-08-18 起转发 tools/aimail_base.set_agent_context（平台无关，
    兜底 MCP 服务共用）——单一权威。_ACTIVE_AGENT_CONFIG 在共享核心维护。
    """
    _ab.set_agent_context(agent_id, system_id)
    global _ACTIVE_AGENT_CONFIG
    _ACTIVE_AGENT_CONFIG = _ab._ACTIVE_AGENT_CONFIG

# ── 从 aimail_base 转发（复用面）────────────────────────────
preprocess_mail_payload = _ab.preprocess_mail_payload
process_inbound_mail = _ab.process_inbound_mail
parse_amail_persona = _ab.parse_amail_persona
_extract_board_gateway = _ab._extract_board_gateway
register_board_gateway = _ab._register_board_gateway
store_board_credential = _ab._store_board_credential
email_for_agent = _ab.email_for_agent                  # 地址派生（注册/注销脚本共用）
register_agent_email = _ab.register_agent_email        # 注册链
deregister_agent_email = _ab.deregister_agent_email    # 注销链

# gateway_api 标准客户端转发
GatewayClient = _gw.GatewayClient
load_gateway_config_api = _gw.load_gateway_config
whoami = _gw.whoami
create_api_key = _gw.create_api_key


def make_client(api_key: str, system_id: str = ""):
    """按 api_key 构造标准 API 客户端。"""
    gw = load_gateway_config(system_id) if system_id else load_gateway_config(detect_system_id())
    if not gw:
        raise RuntimeError("agentmail_gateway.json not found")
    return GatewayClient(gw["gateway_url"], api_key)


def load_board_module():
    """加载 aimail_board.py 函数体（裁剪顶层 registry.register 注册块）。

    Hermes 的 aimail_board.py 顶层注册块引用了本文件不存在的 handler
    （依赖 Hermes 运行时特殊加载），CLI/MCP 场景用 ast 定位并删除注册块后
    exec，只取其函数体（board_* 系列）。所有 OpenClaw 侧
    消费者（amail.py / amail_mcp_server.py）统一走此入口。

    同进程内缓存（sys.modules）——避免多次 exec 产生不同函数对象副本。
    """
    cached = sys.modules.get("aimail_board")
    if cached is not None and hasattr(cached, "board_status"):
        return cached

    import ast as _ast
    import importlib.util as _ilu

    board_path = os.path.join(_TOOLS, "aimail_board.py")
    src = open(board_path, encoding="utf-8").read()
    tree = _ast.parse(src)
    drop = []
    for node in tree.body:
        if (isinstance(node, _ast.Expr) and isinstance(node.value, _ast.Call)
                and isinstance(node.value.func, _ast.Attribute)
                and node.value.func.attr == "register"):
            drop.append((node.lineno, node.end_lineno))
    lines = src.splitlines()
    src = "\n".join(ln for i, ln in enumerate(lines, 1)
                    if not any(a <= i <= b for a, b in drop))
    spec = _ilu.spec_from_file_location("aimail_board", board_path)
    board = _ilu.module_from_spec(spec)
    sys.modules["aimail_board"] = board
    exec(compile(src, board_path, "exec"), board.__dict__)
    return board


build_message = _ab.render_message  # 渲染语义对齐 Hermes webhook.py（共享）


# ── 共享运行时（C2/C5/CLI/MCP 统一入口，修订一处即全局生效）──────

def http_post(url: str, body: dict, token: str = "",
              timeout: int = 30) -> dict:
    """统一 JSON POST（OpenClaw hooks 用 Bearer token）。"""
    import urllib.request
    import urllib.error
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, **json.loads(r.read())}
    except urllib.error.HTTPError as e:
        try:
            return {"status": e.code, **json.loads(e.read())}
        except Exception:
            return {"status": e.code, "error": str(e)}
    except Exception as e:
        return {"status": 0, "error": str(e)}


agent_for_email = _ab.route_agent_for_email  # 多收件人入站路由（共享）


# is_ping/is_pong/ping_id/handle_ping_pong come from aimail_base
# (the shared Hermes + OpenClaw layer) — single implementation.
PONG_PREFIX = "__agentmail_pong__:"


PING_PREFIX = _ab.PING_PREFIX
PONG_PREFIX = _ab.PONG_PREFIX
is_ping = _ab.is_ping
is_pong = _ab.is_pong
ping_id = _ab.ping_id
handle_ping_pong = _ab.handle_ping_pong


# handle_ping_pong is imported from aimail_base (shared impl).


# send_pong is SHARED (aimail_base.send_pong) — no platform-specific
# pong sender. Hermes and OpenClaw both reply via the gateway HTTP send
# API; the injected _CONFIG_LOADER resolves each platform's agent config.
send_pong = _ab.send_pong


def dispatch_to_hooks(hooks_url: str, hooks_token: str, agent_id: str,
                      payload: dict, idempotency_key: str,
                      extra_system_prompt: str = "", headers: dict = None,
                      system_id: str = "") -> dict:
    """统一入站投递链：set context → preprocess → build_message → POST /hooks/agent。

    C2（bridge）与 C5（poll）共用，修订富化/组装/注入逻辑只改此处。
    persona 能力差异由 PERSONA_SUPPORTED 驱动（Hermes 保留派生地址，
    OpenClaw 归一为基础地址），处理框架与 Hermes 完全一致。
    返回 hooks 响应（含 status；200/201/202 = 受理成功）。
    """
    set_agent_context(agent_id, system_id)
    enriched = preprocess_mail_payload(dict(payload), headers or {})
    # persona 差异由共享开关驱动（aimail_base.PERSONA_SUPPORTED），
    # preprocess 内部已按开关归一/保留派生地址——此处无需后处理。
    req = {
        "message": build_message(enriched),
        "agentId": agent_id,
        "idempotencyKey": idempotency_key,
        # 显式 sessionKey:复用固定 hook 会话而非 isolated 新建。
        # OpenClaw 2026.7 起,isolated 会话的 delivery 目标无法从 shared
        # main 桶继承 → 拒绝("Refusing implicit isolated cron delivery")。
        # allowRequestSessionKey=true 时请求可携带 sessionKey,固定前缀
        # 使所有入站邮件汇聚到同一 agent 会话(线程按 idempotencyKey 幂等)。
        "sessionKey": f"agent:{agent_id}:hook:amail",
        # deliver=false:agentmail 的回复经 send_mail 工具回发,不依赖
        # OpenClaw 的聊天渠道投递(delivery 目标解析会因无 previous
        # recipient 拒绝 isolated run)。
        "deliver": False,
    }
    if extra_system_prompt:
        req["extraSystemPrompt"] = extra_system_prompt
    return http_post(hooks_url, req, token=hooks_token, timeout=60)

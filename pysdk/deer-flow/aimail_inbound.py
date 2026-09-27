"""aimail_inbound.py — AIMail 入站端点(2026-08-18 重构)。

预处理并入 DeerFlow 本地 gateway 进程(仿 Hermes 进程内预处理)。

链路:
  aimail-gateway → aimail-bridge(透明代理,跨网 pull / 同内网直连)
    → POST /aimail/inbound
      → HMAC 验签(X-Webhook-Signature, per-address webhook_secret)
      → 共享 process_inbound_mail(aimail 适配层 aimail_deerflow)
      → ping/pong 拦截 → 200 吞掉(不触发 agent)
      → 未拦截 → start_run 内部投递(thread = uuid5("aimail", email),
        会话按地址稳定;assistant_id 从 agentmail.json 读)
      → 立即 200(bridge 即刻 ack pending,agent 后台处理)

依赖:aimail 仓库(pysdk/aimail_base + pysdk/deer-flow/aimail_deerflow)
按共享布局落 agentmail.json(~/.aimail/systems/{sid}/{cleaned_addr}/)。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# 注:router 在下方 _aimail_bootstrap() 之后建立 —— prefix/route 两段都取自契约
# 常量(aimail_contract.INBOUND_PATH), 而常量模块要等核心目录进 sys.path。

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

# 共享核心(aimail_home 等):_find_agent_config 用 _ab.aimail_home()
# 解析 home,必须 import aimail_base——缺了会 NameError。
import aimail_base as _ab  # noqa: E402
import aimail_contract as _contract  # noqa: E402  (入站路径唯一真源 = 契约)

# 契约入站路径(默认 /aimail/inbound)必须**原样**成为 FastAPI 的挂载路径。
# ⚠ 不得拆成 APIRouter(prefix=…) + @router.post(leaf) 两段: 叶子段无前导斜杠时
# FastAPI 把 prefix 与它**直接相接**, 契约里那个 "/" 就没了 ⇒ "/aimail"+"inbound"
# = "/aimailinbound"(2026-09-27 实机取证: 契约路径 404、畸形路径 200)。注册链
# (manage.py)/桥/网关一律按契约路径投递 ⇒ 那一跳永远到不了适配器(可投递性缺陷)。
# 现在整条路径直接取自唯一真源, 挂载路径 == 契约路径(单测
# tests/test_deerflow_inbound_route.py 对此做断言)。
_INBOUND_PATH = _contract.INBOUND_PATH
router = APIRouter(tags=["aimail"])


def _verify_hmac(secret: str, body: bytes, signature: str) -> bool:
    """对照 aimail gateway webhook.rs sign_payload:HMAC-SHA256(body, secret),hex 比较。"""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _find_agent_config(email: str) -> dict | None:
    """遍历共享布局 systems/{sid}/*/agentmail.json,按收件地址匹配。"""
    base = Path(_ab.aimail_home()) / "systems"
    if not base.is_dir():
        return None
    for sys_dir in sorted(base.iterdir()):
        if not sys_dir.is_dir():
            continue
        for addr_dir in sorted(sys_dir.iterdir()):
            aj = addr_dir / "agentmail.json"
            if not aj.is_file():
                continue
            try:
                cfg = json.loads(aj.read_text())
                if cfg.get("email") == email:
                    cfg.setdefault("system_id", sys_dir.name)
                    return cfg
            except Exception:
                continue
    return None


def _thread_id_for(email: str) -> str:
    """按地址稳定派生 thread_id(与旧 dispatch_to_deerflow 同构,会话连续)。"""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"aimail:{email}"))


@router.post(_INBOUND_PATH)
async def aimail_inbound(request: Request) -> JSONResponse:
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)

    # ── 1. 收件地址 + 验签(per-address webhook_secret)──
    # 路由目标 = X-AIMail-Email 头(网关/bridge 按每份投递目标注入的 rcpt 地址;
    # aimail-gateway 只写 X-AIMail-Email)。payload.to 是过滤后的全量列表
    # (外投在前),to[0] 常为外部地址,不能作为路由依据——仅当头缺失时兜底。
    email = request.headers.get("X-AIMail-Email", "")
    if not email and isinstance(payload, dict):
        email = payload.get("to", "")
        if isinstance(email, list):
            email = email[0] if email else ""
    cfg = _find_agent_config(email)
    if not cfg:
        # 响应不回显地址(未认证探测者可按 200/401 差异+回显枚举注册状态);
        # 具体地址只进服务端日志
        logger.warning("aimail: no agent config for %s", email)
        return JSONResponse({"status": "no_agent"})

    sig = request.headers.get("X-Webhook-Signature", "")
    if not _verify_hmac(cfg.get("webhook_secret", ""), body, sig):
        logger.warning("aimail: bad signature for %s", email)
        return JSONResponse({"error": "bad signature"}, status_code=401)

    # ── 2. 共享入站预处理(与 Hermes 同一实现)──
    # 身份解析 → persona 归一 → 富化 → 附件落盘 → 存储;最后一步 ping/pong 拦截。
    import aimail_deerflow as _base  # 适配层:注入点赋值 + 身份注入 + 转发共享函数

    agent_id = cfg.get("agent_id", "") or "default"
    try:
        _base.set_agent_context(agent_id, cfg.get("system_id", ""))
    except Exception as e:
        logger.warning("aimail: set_agent_context failed for %s: %s", email, e)
        return JSONResponse({"status": "no_local_config", "agent": agent_id, "detail": str(e)})

    try:
        enriched = _base.process_inbound_mail(payload, dict(request.headers))
    except Exception as e:
        logger.exception("aimail: preprocess failed for %s", email)
        return JSONResponse({"error": f"preprocess failed: {e}"}, status_code=500)
    if enriched is None:
        return JSONResponse({"status": "intercepted"})  # ping/pong,不触发 agent

    # ── 3. 内部投递:start_run(后台任务,立即返回)──
    # 完整渲染(共享 render_message = json.dumps(payload) 语义,与 Hermes/
    # OpenClaw 一致):agent 需要 sender/recipients/my_aimail_addr 才知道回复谁。
    from app.gateway.run_models import RunCreateRequest
    from app.gateway.services import start_run

    content = _base.render_message(enriched)
    run_body = RunCreateRequest(
        assistant_id=cfg.get("assistant_id") or "lead_agent",
        input={"messages": [{"role": "user", "content": content}]},
        config={"configurable": {"thread_id": _thread_id_for(email)}},
        metadata={
            "idempotency_key": f"aimail:{payload.get('mail_id', '')}",
            "aimail_email": email,
        },
        multitask_strategy="reject",
        if_not_exists="create",
    )
    try:
        record = await start_run(run_body, _thread_id_for(email), request)
        logger.info("aimail: delivered %s → thread %s (run %s)",
                    email, record.thread_id, getattr(record, "run_id", "?"))
    except Exception as e:
        logger.exception("aimail: start_run failed for %s", email)
        return JSONResponse({"error": f"start_run failed: {e}"}, status_code=502)

    return JSONResponse({"status": "delivered", "email": email})


# ═══════════════════════════════════════════════════════════════
# agent-scope 定时轮询入口(pull-entry)—— install/启动收尾接的最后一根线
# ═══════════════════════════════════════════════════════════════
# 地址级激活码兑回来的地址**没有 push 路径**(网关给它写 webhook_url = NULL),
# 只能自己定时 pull; 而 DeerFlow 宿主是长驻应用 ⇒ 在**应用启动**时把这条线接上
# (由 manage.py 的 app.py 补丁在 include_router 之后调 start_pull_on_startup(app),
# 见 patch_backend_app)。判定/间隔/关停/失败不 ack 全部复用共享核心
# (aimail_base: is_agent_scope_binding / resolve_agent_pull_settings /
# start_agent_pull_entries), 语义与 TS mail-core poll-entry.ts 逐条对齐。
#
# pull 与 push 共用**同一条入站链**: 投递按 push 的原样请求(同一契约路径 +
# per-address HMAC 头)打回本机入站端点 ⇒ 上面那个 aimail_inbound 处理函数
# (验签 → 共享预处理 → start_run)逐字生效。

#: 本机入站端点(注册链同源: manage.py 用同一环境变量拼 webhook_url)。
_DEERFLOW_INBOUND_BASE_ENV = "DEERFLOW_INBOUND_URL"


def _inbound_base() -> str:
    return os.environ.get(_DEERFLOW_INBOUND_BASE_ENV) or (
        "http://127.0.0.1:%d" % _contract.INBOUND_PORTS["deerflow"])


def _pull_replay_target(cfg: dict) -> dict:
    """解析"把这封 pull 到的投递打回本机入站端点"所需的 (url, secret)。

    绑定自身的 webhook_url/webhook_secret(注册链落盘, 唯一信任源)优先;
    url 缺失则回退到宿主当前入站端点(环境变量 / 契约端口 + 契约路径)。
    """
    default_url = _inbound_base().rstrip("/") + _contract.INBOUND_PATH
    return _ab.resolve_inbound_replay_target(cfg, default_url=default_url)


def _on_pull_email(cfg: dict, mail: dict) -> dict:
    """pull 来的信按 push 原样请求打回本机入站端点(同一条链)。

    失败抛异常 ⇒ start_polling 不 ack ⇒ 下轮重拉(不丢件)。
    """
    target = _pull_replay_target(cfg)
    if not target.get("ok"):
        raise RuntimeError("no local inbound endpoint for %s (%s) — not acking"
                           % (cfg.get("email", ""), target.get("reason", "")))
    return _ab.replay_inbound_delivery(
        cfg, mail, url=target["url"], secret=target["secret"])


def _pull_system_id() -> str:
    """本机 system_id(AIMAIL_SYSTEM_ID / 平台指针; 空 = 扫全部系统)。

    与注册链同源: 用适配层 forward 的 detect_system_id(manage.py 对账也用同一个),
    取不到就退到共享核心的 resolve_system_id_for_email 指针兜底。
    """
    try:
        import aimail_deerflow as _df
        sid = _df.detect_system_id()
        if sid:
            return str(sid)
    except Exception:
        pass
    try:
        return _ab.resolve_system_id_for_email("") or ""
    except Exception:
        return os.environ.get("AIMAIL_SYSTEM_ID", "")


def start_pull_on_startup(app) -> bool:
    """把 agent-scope 轮询挂到 FastAPI 的启动/关停钩子上(长驻宿主生命周期)。

    由 app.py 补丁在 include_router 之后调用(manage.py:patch_backend_app)。
    启动收尾起循环、关停收尾停循环; 幂等(重复调用只挂一次); 任何异常都不许
    拖垮宿主启动。
    """
    if getattr(app, "_aimail_pull_wired", False):
        return True
    handles: list = []

    async def _aimail_pull_startup() -> None:
        try:
            handles.extend(_ab.start_agent_pull_entries(
                system_id=_pull_system_id(),
                on_email=_on_pull_email,
                target_for=_pull_replay_target,
                log=lambda line: logger.info("%s", line),
            ))
        except Exception as e:  # noqa: BLE001 — 接线失败不拖垮宿主
            logger.warning("aimail: pull entry start failed: %s", e)
        logger.info("aimail: agent-scope pull entries: %d started", len(handles))

    async def _aimail_pull_shutdown() -> None:
        _ab.stop_agent_pull_entries(handles)

    try:
        app.add_event_handler("startup", _aimail_pull_startup)
        app.add_event_handler("shutdown", _aimail_pull_shutdown)
        app._aimail_pull_wired = True
        logger.info("aimail: pull entry wired to app startup/shutdown")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("aimail: pull entry wiring failed: %s", e)
        return False

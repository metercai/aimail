"""aimail_inbound.py — AIMail 入站端点(2026-08-18 重构)。

预处理并入 DeerFlow 本地 gateway 进程(仿 Hermes 进程内预处理),取代
独立接收进程 amail_deerflow_bridge.py(8798,已退役删除)。

链路:
  aimail-gateway → aimail-bridge(透明代理,跨网 pull / 同内网直连)
    → POST /aimail/inbound
      → HMAC 验签(X-Webhook-Signature, per-address webhook_secret)
      → 共享 process_inbound_mail(aimail 适配层 amail_base)
      → ping/pong 拦截 → 200 吞掉(不触发 agent)
      → 未拦截 → start_run 内部投递(thread = uuid5("amail", email),
        会话按地址稳定;assistant_id 从 agentmail.json 读)
      → 立即 200(bridge 即刻 ack pending,agent 后台处理)

依赖:aimail 仓库(pysdk/aimail_base + tools/deer-flow/amail_base)
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
router = APIRouter(prefix="/aimail", tags=["aimail"])

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


def _verify_hmac(secret: str, body: bytes, signature: str) -> bool:
    """对照 amail webhook.rs sign_payload:HMAC-SHA256(body, secret),hex 比较。"""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _find_agent_config(email: str) -> dict | None:
    """遍历共享布局 systems/{sid}/*/agentmail.json,按收件地址匹配。"""
    base = Path.home() / ".aimail" / "systems"
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
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"amail:{email}"))


@router.post("/inbound")
async def aimail_inbound(request: Request) -> JSONResponse:
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)

    # ── 1. 收件地址 + 验签(per-address webhook_secret)──
    # 路由目标 = X-AIMail-Email 头(网关/bridge 按每份投递目标注入的 rcpt 地址;
    # 旧名 X-Amail-Email 过渡回退)。payload.to 现在是过滤后的全量列表(外投在前),
    # to[0] 常为外部地址,不能作为路由依据——仅当头缺失时兜底。
    email = request.headers.get("X-AIMail-Email", "") or request.headers.get("X-Amail-Email", "")
    if not email and isinstance(payload, dict):
        email = payload.get("to", "")
        if isinstance(email, list):
            email = email[0] if email else ""
    cfg = _find_agent_config(email)
    if not cfg:
        logger.warning("aimail: no agent config for %s", email)
        return JSONResponse({"status": "no_agent", "email": email})

    sig = request.headers.get("X-Webhook-Signature", "")
    if not _verify_hmac(cfg.get("webhook_secret", ""), body, sig):
        logger.warning("aimail: bad signature for %s", email)
        return JSONResponse({"error": "bad signature"}, status_code=401)

    # ── 2. 共享入站预处理(与 Hermes/OpenClaw 同一实现)──
    # 身份解析 → persona 归一 → 富化 → 附件落盘 → 存储;最后一步 ping/pong 拦截。
    import amail_base as _base  # 适配层:注入点赋值 + 身份注入 + 转发共享函数

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
    # OpenClaw 一致):agent 需要 sender/recipients/my_amail_addr 才知道回复谁。
    from app.gateway.run_models import RunCreateRequest
    from app.gateway.services import start_run

    content = _base.render_message(enriched)
    run_body = RunCreateRequest(
        assistant_id=cfg.get("assistant_id") or "lead_agent",
        input={"messages": [{"role": "user", "content": content}]},
        config={"configurable": {"thread_id": _thread_id_for(email)}},
        metadata={
            "idempotency_key": f"amail:{payload.get('mail_id', '')}",
            "amail_email": email,
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

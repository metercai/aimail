#!/usr/bin/env python3
"""amail_openclaw_bridge.py — OpenClaw 入站接收端(bridge 转发目标)。

aimail-bridge(拉取器,透明转发)→ 本服务:
  1. HMAC 验签(X-Webhook-Signature,webhook_secret)
  2. 共享入站预处理 process_inbound_mail(身份/persona/富化/存储,
     最后一步 ping/pong 拦截——pong 回环走全链末端)
  3. 未拦截 → 按收件地址路由 agent → dispatch_to_hooks(共享投递链)

接受路径: /hook 与 /webhooks/amail-inbound(bridge 全 URL 路由可指向
任意端点,本服务兼容两路径;pull/push 两模式均可用)。

共享逻辑(富化/组装/投递/ping-pong/http)统一在 amail_base。

用法:
  python3 amail_openclaw_bridge.py [--port 8799] [--system-id SID]
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

import amail_base as _base            # noqa: E402


def verify_hmac(secret: str, body: bytes, signature: str, timestamp: str) -> bool:
    """对照 amail webhook.rs sign_payload：HMAC-SHA256(body, secret)，hex 比较。
    timestamp 参与与否以实现核对为准（当前按 body-only 校验）。"""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class BridgeHandler(BaseHTTPRequestHandler):
    bridge = None  # 由 serve() 注入

    def log_message(self, *a):  # 静默默认日志
        pass

    def _send_json(self, code: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path not in ("/hook", "/webhooks/amail-inbound"):
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            self._send_json(400, {"error": "invalid body"})
            return

        # ── 1. HMAC 验签 ──
        sig = self.headers.get("X-Webhook-Signature", "")
        ts = self.headers.get("X-AIMail-Timestamp", "") or self.headers.get("X-Mailrelay-Timestamp", "")  # 旧名过渡回退
        secret = self.bridge["webhook_secret"]
        if not verify_hmac(secret, body, sig, ts):
            self._send_json(401, {"error": "bad signature"})
            return

        # ── 2. 共享入站预处理(与 Hermes preprocess 同一实现)──
        # 身份解析 → persona → 富化 → 存储,最后一步 ping/pong 拦截。
        # 拦截(ping/pong)返回 None → 200 吞掉,不触发 agent;
        # pong 由共享 send_pong 出站,回环走完整入站链(设计意图)。
        # 预处理需要 agent 配置注入(set_agent_context)——先按收件地址
        # 解析 agent,再调共享链(与 poll 批级调用同入口)。
        # 路由目标 = X-AIMail-Email 头(网关/bridge 按每份投递目标注入的
        # rcpt 地址; 旧名 X-Amail-Email 过渡回退)。payload.to 现在是过滤后的全量列表(外投在前),
        # to[0] 常为外部地址,不能作为路由依据——仅当头缺失时兜底。
        email = self.headers.get("X-AIMail-Email", "") or self.headers.get("X-Amail-Email", "")  # 旧名过渡回退
        if not email:
            email = payload.get("to", "")
            if isinstance(email, list):
                email = email[0] if email else ""
        agent_id = _base.agent_for_email(self.bridge["registry"], email)
        if not agent_id:
            self._send_json(200, {"status": "no_agent", "email": email})
            return
        try:
            _base.set_agent_context(agent_id, self.bridge["system_id"])
        except Exception as e:
            self._send_json(200, {"status": "no_local_config", "agent": agent_id, "detail": str(e)})
            return
        try:
            enriched = _base.process_inbound_mail(payload, dict(self.headers))
        except Exception as e:
            self._send_json(500, {"error": f"preprocess failed: {e}"})
            return
        if enriched is None:
            self._send_json(200, {"status": "intercepted"})
            return

        # ── 3. 共享投递链(传原始 body,富化由投递链内部完成)──
        try:
            resp = _base.dispatch_to_hooks(
                self.bridge["hooks_url"], self.bridge["hooks_token"], agent_id,
                dict(payload), idempotency_key=f"amail:{payload.get('mail_id', '')}",
                extra_system_prompt=self.bridge.get("extra_system_prompt", ""),
                headers=dict(self.headers),
                system_id=self.bridge["system_id"],
            )
        except RuntimeError as e:
            self._send_json(200, {"status": "no_local_config", "agent": agent_id, "detail": str(e)})
            return
        if resp.get("status") in (200, 201, 202):
            self._send_json(200, {"status": "accepted", "runId": resp.get("runId", "")})
        else:
            self._send_json(502, {"status": "hook_rejected", "detail": resp})


def _resolve_webhook_secret(system_id: str, gw: dict) -> str:
    """解析 webhook_secret: 优先 agentmail.json 落盘值(注册时生成并落盘,
    与云端一致),回退 gateway config。为空时验签必 401。"""
    secret = ""
    try:
        import glob
        addr_dir = os.path.expanduser(f"~/.agentmail/systems/{system_id}")
        for aj in glob.glob(os.path.join(addr_dir, "*", "agentmail.json")):
            try:
                data = json.load(open(aj))
            except Exception:
                continue
            s = data.get("webhook_secret", "")
            if s:
                secret = s
                break
    except Exception:
        pass
    return secret or gw.get("webhook_secret", "")


def serve(system_id: str, port: int, hooks_url: str) -> None:
    gw = _base.load_gateway_config(system_id)
    if not gw:
        raise SystemExit(f"gateway config not found (agentmail_gateway.json) for {system_id}")
    hooks = _base.load_openclaw_hooks()
    if not hooks:
        raise SystemExit("OpenClaw hooks not configured (token missing)")
    registry = _base.load_agents_registry(system_id)

    bridge = {
        "system_id": system_id,
        "webhook_secret": _resolve_webhook_secret(system_id, gw),
        "registry": registry,
        "hooks_url": hooks_url,
        "hooks_token": hooks["token"],
        "extra_system_prompt": "",  # board 角色文本注入点（P1 board 接入后填充）
    }
    if not bridge["webhook_secret"]:
        print("WARNING: webhook_secret empty — inbound HMAC verification will reject all deliveries")
    BridgeHandler.bridge = bridge
    srv = ThreadingHTTPServer(("127.0.0.1", port), BridgeHandler)
    print(f"bridge listening on 127.0.0.1:{port} (system {system_id})")
    srv.serve_forever()


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenClaw amail push bridge")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--system-id", default=os.environ.get("AIMAIL_SYSTEM_ID", ""))
    ap.add_argument("--hooks-url", default="http://127.0.0.1:18789/hooks/agent")
    args = ap.parse_args()
    system_id = args.system_id or _base.detect_system_id()
    serve(system_id, args.port, args.hooks_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())

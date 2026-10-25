"""aimail_inbound.py — AIMail inbound endpoint (2026-08-18 refactor).

Preprocessing merged into DeerFlow local gateway process (mimicking Hermes in-process preprocessing).

Chain:
  aimail-gateway -> aimail-bridge (transparent proxy, cross-network pull / same intranet direct connection)
    → POST /aimail/inbound
      → HMAC signature verification (X-Webhook-Signature, per-address webhook_secret)
      → Shared process_inbound_mail (aimail adapter layer aimail_deerflow)
      → ping/pong interception → 200 swallowed (does not trigger agent)
      → Not intercepted → start_run internal delivery (thread = uuid5("aimail", email),
        session stable by address; assistant_id read from agentmail.json)
      → Immediate 200 (bridge acks pending immediately, agent processes in background)

Dependencies: aimail repository (pysdk/aimail_base + pysdk/deer-flow/aimail_deerflow)
Place agentmail.json according to shared layout (~/.aimail/systems/{sid}/{cleaned_addr}/).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import stat
import socket
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Note: router is established after _aimail_bootstrap() below —— both prefix/route segments are taken from the contract
# constants (aimail_contract.INBOUND_PATH), and the constants module must wait for the core directory to enter sys.path.

# ── aimail runtime core location (bundle / site-packages / repo dev; no longer depends on repo path) ──
def _aimail_bootstrap():
    """Locate aimail runtime core, assemble sys.path."""
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

# Shared core (aimail_home etc.): _find_agent_config uses _ab.aimail_home()
# Resolving home, must import aimail_base —— missing it will cause NameError.
import aimail_base as _ab  # noqa: E402
import aimail_contract as _contract  # noqa: E402  (inbound path single source of truth = contract)

# Contract inbound path (default /aimail/inbound) must become the FastAPI mount path **as-is**.
# ⚠ Must not be split into APIRouter(prefix=…) + @router.post(leaf) two segments: when the leaf segment has no leading slash
# FastAPI **directly concatenates** the prefix with it, and the "/" in the contract is lost ⇒ "/aimail"+"inbound"
# = "/aimailinbound" (2026-09-27 live evidence: contract path 404, malformed path 200). Registration chain
# (manage.py)/bridge/gateway all deliver according to contract path ⇒ that hop never reaches the adapter (deliverability defect).
# Now the entire path is taken directly from the single source of truth, mount path == contract path (unit test
# tests/test_deerflow_inbound_route.py asserts this).
_INBOUND_PATH = _contract.INBOUND_PATH
router = APIRouter(tags=["aimail"])


def _verify_hmac(secret: str, body: bytes, signature: str) -> bool:
    """Compare with aimail gateway webhook.rs sign_payload: HMAC-SHA256(body, secret), hex comparison."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _find_agent_config(email: str) -> dict | None:
    """Iterate shared layout systems/{sid}/*/agentmail.json, match by recipient address."""
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
    """Stably derive thread_id by address (isomorphic to old dispatch_to_deerflow, session continuous)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"aimail:{email}"))


@router.post(_INBOUND_PATH)
async def aimail_inbound(request: Request) -> JSONResponse:
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)

    # ── 1. Recipient address + signature verification (per-address webhook_secret) ──
    # Routing target = X-AIMail-Email header (gateway/bridge injects rcpt address per delivery target;
    # aimail-gateway only writes X-AIMail-Email). payload.to is the filtered full list
    # (external delivery first), to[0] is often an external address, cannot be used as routing basis —— only fallback when head is missing.
    email = request.headers.get("X-AIMail-Email", "")
    if not email and isinstance(payload, dict):
        email = payload.get("to", "")
        if isinstance(email, list):
            email = email[0] if email else ""
    cfg = _find_agent_config(email)
    if not cfg:
        # Response does not echo address (unauthenticated prober can enumerate registration status by 200/401 difference + echo);
        # specific address only goes to server-side logs
        logger.warning("aimail: no agent config for %s", email)
        return JSONResponse({"status": "no_agent"})

    sig = request.headers.get("X-Webhook-Signature", "")
    if not _verify_hmac(cfg.get("webhook_secret", ""), body, sig):
        logger.warning("aimail: bad signature for %s", email)
        return JSONResponse({"error": "bad signature"}, status_code=401)

    # ── 2. Shared inbound preprocessing (same implementation as Hermes) ──
    # Identity resolution → persona normalization → enrichment → attachment disk write → storage; last step ping/pong interception.
    import aimail_deerflow as _base  # Adapter layer: injection point assignment + identity injection + forward shared functions

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
        return JSONResponse({"status": "intercepted"})  # ping/pong, does not trigger agent

    # ── 3. Internal delivery: start_run (background task, returns immediately) ──
    # Full rendering (shared render_message = json.dumps(payload) semantics, consistent with Hermes/
    # OpenClaw): agent needs sender/recipients/my_aimail_addr to know who to reply to.
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
# agent-scope scheduled polling entry (pull-entry) —— the last wire connected by install/startup finishing
# ═══════════════════════════════════════════════════════════════
# Addresses redeemed by address-level activation codes **have no push path** (gateway writes webhook_url = NULL for them),
# Only self-scheduled pull; and the DeerFlow host is a long-running application ⇒ wire this up at **application startup**
# (called by manage.py's app.py patch after include_router to call start_pull_on_startup(app),
# see patch_backend_app). Judgment/interval/shutdown/failure-no-ack all reuse the shared core
# (aimail_base: is_agent_scope_binding / resolve_agent_pull_settings /
# start_agent_pull_entries), semantics aligned line-by-line with TS mail-core poll-entry.ts.
#
# pull and push share **the same inbound chain**: delivery is sent back to the local inbound endpoint as the original push request (same contract path +
# per-address HMAC header) ⇒ the aimail_inbound handler above
# (signature verification → shared preprocessing → start_run) applies verbatim.

#: Local inbound endpoint. The host gateway (this process) serves inbound; its
#: port is discovered at runtime, NOT taken from a contract default (owner ruling
#: 2026-10-25: host-gateway ports carry no contract default — the adapter
#: discovers the actual port and the confirmed callback reports the real URL).
_DEERFLOW_INBOUND_BASE_ENV = "DEERFLOW_INBOUND_URL"

# Discovered gateway base (host + port) — set once the bind is observed.
_aimail_gateway_base: str | None = None
# Snapshot of the process's listening sockets at lifespan-startup time (pre-bind);
# the NEW listening socket after this snapshot is the gateway's bind result.
_aimail_pre_bind_socks: list = []


def _process_listen_socks() -> list:
    """Listening sockets owned by THIS process (fd-based; no netns-wide scan).

    Returns a sorted, de-duplicated list of (host, port). Only sockets this
    process actually owns appear, so the pre/post diff isolates the gateway's
    own bind (spike-verified against a kernel-assigned port).
    """
    out = set()
    try:
        fd_names = os.listdir("/proc/self/fd")
    except OSError:
        return []
    seen_ino = set()
    for fd in fd_names:
        try:
            st = os.stat("/proc/self/fd/%s" % fd)
        except OSError:
            continue
        if not stat.S_ISSOCK(st.st_mode) or st.st_ino in seen_ino:
            continue
        seen_ino.add(st.st_ino)
        s = None
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                s = socket.fromfd(int(fd), family, socket.SOCK_STREAM)
                break
            except OSError:
                continue
        if s is None:
            continue
        try:
            sn = s.getsockname()
            if sn and len(sn) >= 2 and sn[1]:
                out.add((sn[0], sn[1]))
        finally:
            try:
                s.close()
            except OSError:
                pass
    return sorted(out)


def _discover_new_listen_sock(pre_bind: list) -> tuple | None:
    """The process's newly-listening socket vs the pre-bind snapshot, or None."""
    pre = {(h, p) for h, p in pre_bind}
    for (h, p) in _process_listen_socks():
        if (h, p) not in pre:
            return (h, p)
    return None


def _inbound_base() -> str | None:
    """Authoritative local inbound base (host + port) for this gateway process.

    Resolution order (owner ruling 2026-10-25 — never a contract default):
      1. ``DEERFLOW_INBOUND_URL`` env override (explicit, authoritative);
      2. the discovered bind (``_aimail_gateway_base``, set once observed);
      3. ``None`` (unknown — the caller must NOT fabricate a port).
    """
    env = os.environ.get(_DEERFLOW_INBOUND_BASE_ENV)
    if env:
        return env.rstrip("/")
    return _aimail_gateway_base


def _pull_replay_target(cfg: dict) -> dict:
    """Resolve the (url, secret) needed to "bounce this pulled delivery back to the local inbound endpoint".

    deer-flow specific (in-process gateway): the URL is the **live** endpoint
    (``DEERFLOW_INBOUND_URL`` override / the discovered bind — the port the
    gateway actually serves on), NOT the registration-time best-known seed
    persisted in the binding (owner ruling 2026-10-25: no contract default; the
    runtime is the source of truth). The secret stays the binding's
    ``webhook_secret``: the inbound handler (``aimail_inbound``) reads the
    binding itself to verify the signature, so the binding is the signature
    truth and the bounce must sign with the same value. When the endpoint is
    unknown (no env, bind not yet observed) there is NO valid target — return
    not-ok so the pull entry is not started on a fabricated port (mail stays
    pending, re-pulled once the endpoint is known).
    """
    base = _inbound_base()
    if not base:
        return {"ok": False, "url": "", "secret": "", "reason": "no-local-endpoint"}
    url = base + _contract.INBOUND_PATH
    secret = str(cfg.get("webhook_secret") or "").strip()
    if not secret:
        return {"ok": False, "url": url, "secret": "", "reason": "no-secret"}
    return {"ok": True, "url": url, "secret": secret, "reason": ""}


def _on_pull_email(cfg: dict, mail: dict) -> dict:
    """Send pulled mail back to the local inbound endpoint as the original push request (same chain).

    Failure raises exception ⇒ start_polling does not ack ⇒ re-pull next round (no loss).
    """
    target = _pull_replay_target(cfg)
    if not target.get("ok"):
        raise RuntimeError("no local inbound endpoint for %s (%s) — not acking"
                           % (cfg.get("email", ""), target.get("reason", "")))
    return _ab.replay_inbound_delivery(
        cfg, mail, url=target["url"], secret=target["secret"])


def _pull_system_id() -> str:
    """Local system_id (AIMAIL_SYSTEM_ID / platform pointer; empty = scan all systems).

    Same source as registration chain: uses the adapter layer forward's detect_system_id (manage.py reconciliation uses the same one),
    if unavailable, falls back to the shared core's resolve_system_id_for_email pointer.
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


def _wire_pull_to_lifespan(app, on_startup, on_shutdown) -> None:
    """Wrap (on_startup, on_shutdown) into the **host lifespan** (`app.router.lifespan_context`).

    Why wrap lifespan instead of event hooks (2026-09-28 same image test, host
    fastapi 0.136.1 / starlette 1.3.1 —— see
    `tests/SDK/docker-regression/hosts/e2-deerflow-pull-wiring-findings.md`):
      · `app.add_event_handler("startup", …)` has been removed ⇒ AttributeError (original defect);
      · `@app.on_event("startup")` when the host provides an explicit `lifespan=` **HOOK_FIRED=False**;
      · Wrapping `app.router.lifespan_context` ⇒ **HOOK_FIRED=True**.
    `lifespan_context` is the only startup mechanism retained by star applications (default `_DefaultLifespan` is also
    callable), and works for hosts **without** a custom lifespan ⇒ **main path does not depend on any removed API**.

    Order (none can be missing): host lifespan ENTER first (routing/persistence/endpoints ready —— pull bounces delivery
    bounce back to the host's own inbound endpoint) → our on_startup → **pass through as-is** the state the host yields
    (dropping it would silently change host behavior) → finally reverse shutdown.
    Idempotent: if already wrapped, return directly (`_aimail_pull_wrapped` and `app._aimail_pull_wired` double insurance).
    """
    router = getattr(app, "router", None)
    if router is None or not hasattr(router, "lifespan_context"):
        raise RuntimeError(
            "host app has no router.lifespan_context —— cannot connect agent-scope pull")
    orig = router.lifespan_context
    if getattr(orig, "_aimail_pull_wrapped", False):
        return

    @contextlib.asynccontextmanager
    async def _aimail_pull_lifespan(inner_app):
        async with orig(inner_app) as _state:   # host lifespan starts first
            await on_startup()
            try:
                yield _state                    # pass through host yielded state as-is
            finally:
                await on_shutdown()

    setattr(_aimail_pull_lifespan, "_aimail_pull_wrapped", True)
    router.lifespan_context = _aimail_pull_lifespan


def start_pull_on_startup(app) -> bool:
    """Attach agent-scope polling to the **host lifespan** (long-running host lifecycle).

    Called by the app.py patch after include_router (manage.py:patch_backend_app).
    Start loop at startup finish, stop loop at shutdown finish; idempotent (repeated calls only wire once).

    **Wiring failure must not be silent** (cause of this defect: exception swallowed ⇒ latent until gate check): on failure
    ① loudly log `logger.error` (with exception type and reason) ② return value `False` ③ leave on `app`
    a queryable flag (`_aimail_pull_wired=False` + `_aimail_pull_wire_error=<reason>`).
    Here **do not raise**: the call site is on the module-level import path of the host app.py, raising would drag down the entire host
    (deer-flow gateway) dragging to death —— silence and dragging to death are both not options, so choose "loud + queryable + not dragging down".
    """
    if getattr(app, "_aimail_pull_wired", False):
        return True
    handles: list = []
    discovery_done = False

    def _start_pull_and_report_live() -> None:
        """Start agent-scope pull entries + report `--inbound-live` with the
        CONFIRMED URL (the endpoint this gateway actually serves).

        Called ONCE, after the endpoint is known (env override at wire time, or
        the discovered bind observed by the background task). The confirmed URL
        is authoritative (contract §4.3): the CLI pins its route to it. With
        an unknown endpoint there is NO valid URL — this function must not be
        called (no fabricated port; owner ruling 2026-10-25).
        """
        nonlocal discovery_done
        if discovery_done:
            return
        discovery_done = True
        base = _inbound_base()
        if not base:
            logger.error("aimail: inbound endpoint unknown (no DEERFLOW_INBOUND_URL, "
                         "gateway bind not yet observed) — live callback withheld, "
                         "pull entries unstarted; mail delivery for this system is "
                         "degraded until the endpoint is known")
            return
        try:
            handles.extend(_ab.start_agent_pull_entries(
                system_id=_pull_system_id(),
                on_email=_on_pull_email,
                target_for=_pull_replay_target,
                log=lambda line: logger.info("%s", line),
            ))
        except Exception as e:  # noqa: BLE001 — loop failure does not drag down host, but must be loud
            logger.error("aimail: pull entry start FAILED (%s: %s) —— polling not running",
                         type(e).__name__, e)
        logger.info("aimail: agent-scope pull entries: %d started", len(handles))
        # Inbound notification (owner ruling 2026-09-28 SDK de-bridging + 2026-10-25):
        # the host gateway (this process) serves inbound for these addresses and the
        # endpoint is CONFIRMED (env override / discovered bind) ⇒ report
        #     aimail address -a <addr> --inbound-live --inbound-url <confirmed URL>
        # with the REAL serving URL (contract §4.3 — never a default) ⇒ CLI pins the
        # route. Best-effort, once per address.
        _confirmed_url = base + _INBOUND_PATH
        for _n in _ab.notify_inbound_for_system("live", system_id=_pull_system_id(), url=_confirmed_url):
            _line = "aimail: " + _ab.format_inbound_notify_line(_n, "live")
            if _ab.inbound_notify_is_warning(_n):
                logger.warning("%s", _line)
            else:
                logger.info("%s", _line)

    async def _await_bind_then_start() -> None:
        """Background discovery (no env override): wait for the gateway's bind
        (uvicorn binds AFTER the lifespan startup completes), observe it via the
        process's own listening sockets (spike-verified fd scan + pre/post diff),
        then start pull + report live with the real URL. Bounded (30s): a timeout
        keeps the loud-withheld state — no fabricated port.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 30.0
        while loop.time() < deadline:
            await asyncio.sleep(0.25)
            found = _discover_new_listen_sock(_aimail_pre_bind_socks)
            if found:
                global _aimail_gateway_base
                _aimail_gateway_base = "http://%s:%d" % (found[0], found[1])
                logger.info("aimail: gateway inbound bind observed at %s",
                            _aimail_gateway_base)
                _start_pull_and_report_live()
                return
        logger.error("aimail: gateway bind not observed within 30s (no DEERFLOW_INBOUND_URL) "
                     "— live callback withheld, pull entries unstarted; mail delivery "
                     "degraded until the endpoint is known (set DEERFLOW_INBOUND_URL to "
                     "the gateway's actual base URL to make this deterministic)")

    async def _aimail_pull_startup() -> None:
        # uvicorn binds the gateway AFTER the lifespan startup completes
        # (Server.startup: lifespan.startup() → loop.create_server), so at this
        # point the bind is NOT yet observable. Snapshot the process's current
        # listening sockets (pre-bind baseline); with the env override the
        # endpoint is already known — start immediately; without it, a background
        # task waits for the bind, then starts pull + reports live.
        global _aimail_pre_bind_socks
        _aimail_pre_bind_socks = _process_listen_socks()
        if _inbound_base():
            _start_pull_and_report_live()
            return
        asyncio.get_running_loop().create_task(_await_bind_then_start())

    async def _aimail_pull_shutdown() -> None:
        _ab.stop_agent_pull_entries(handles)
        # Shutdown: inbound no longer served ⇒ same best-effort reverse notification (host's own teardown hook).
        for _n in _ab.notify_inbound_for_system("down", system_id=_pull_system_id()):
            _line = "aimail: " + _ab.format_inbound_notify_line(_n, "down")
            if _ab.inbound_notify_is_warning(_n):
                logger.warning("%s", _line)
            else:
                logger.info("%s", _line)

    try:
        _wire_pull_to_lifespan(app, _aimail_pull_startup, _aimail_pull_shutdown)
    except Exception as e:  # noqa: BLE001 — wiring failure does not crash host, but never silent
        app._aimail_pull_wired = False
        app._aimail_pull_lifespan_wrapped = False
        app._aimail_pull_wire_error = f"{type(e).__name__}: {e}"
        logger.error(
            "aimail: pull entry wiring FAILED (%s: %s) —— agent-scope polling loop not established; "
            "pure address-level (no system-level install) pull receiving will be entirely disabled, manual intervention required",
            type(e).__name__, e)
        return False
    app._aimail_pull_wired = True
    # Flag placed on **app** (not just on the wrapped callable): after patching call sites host
    # will continue include_router, FastAPI's include_router will **re-merge** `lifespan_context`
    # Use `_merge_lifespan_context(self.…, router.…)` to **re-merge** (verified in deer-flow
    # 35 times) ⇒ our wrapper gets **nested into the chain** (behavior preserved: polling still starts) but is no longer the outermost callable,
    # attributes attached to it will be unreadable. App-level flag is unaffected by merge, is a stable queryable artifact.
    app._aimail_pull_lifespan_wrapped = True
    app._aimail_pull_wire_error = None
    logger.info("aimail: pull entry wired to app lifespan (startup/shutdown)")
    return True

# -*- coding: utf-8 -*-
"""The Hermes platform adapter for AIMail (contract §8.3, §9).

Why a platform adapter rather than a webhook: Hermes' webhook platform binds a port per
profile, and a multiplexed gateway forbids that for secondary profiles. The adapter has
no listener at all — the SSE runtime inside the plugin hands it mail, and
``handle_message`` is the gateway's own inbound path (session create/reuse included).

The host classes are imported lazily by :func:`build_adapter_class` so this package
stays importable where Hermes is absent (tests, the wheel smoke check, other hosts).

Ack evidence: ``handle_message`` sets ``event._gateway_accepted = True`` only when the
event really entered a session. That flag is the ONE thing trusted for acking; a host
upgrade that removes it must surface as "never accepted" (fail-closed), never as a
silent ack.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, Optional

#: Delivery timeout when the pull thread waits for the gateway loop.
DELIVER_TIMEOUT_S = 120.0


def build_adapter_class(base_cls, *, event_module=None, name: str = "aimail"):
    """Create the ``AIMailAdapter`` subclass for the host's ``BasePlatformAdapter``.

    ``base_cls`` is the host class; ``event_module`` supplies ``MessageEvent`` /
    ``MessageType`` (defaults to ``gateway.platforms.event``).
    """
    if event_module is None:  # pragma: no cover - the host is present in production
        from gateway.platforms import event as event_module  # type: ignore

    MessageEvent = event_module.MessageEvent
    MessageType = event_module.MessageType

    class AIMailAdapter(base_cls):  # type: ignore[misc,valid-type]
        """Inbound AIMail: SSE inside the plugin, sessions through the gateway."""

        platform_name = name

        def __init__(self, config: Any = None, platform: Any = None, **kwargs: Any) -> None:
            # Measured 2026-11-23: `BasePlatformAdapter.__init__(self, config, platform)`
            # takes TWO required arguments, and the registry calls the factory with the
            # platform config only. Passing just `config` failed every adapter creation
            # ("missing 2 required positional arguments"), so the platform member is
            # resolved from our registered name when the host does not supply it.
            if platform is None:
                try:
                    from gateway.config import Platform  # host-only, lazy
                    platform = Platform(self.platform_name)
                except Exception:  # noqa: BLE001 — a fake base in tests needs no Platform
                    platform = None
            for args in ((config, platform), (config,), ()):
                try:
                    super().__init__(*args)  # type: ignore[call-arg]
                    break
                except TypeError as e:
                    last = e
            else:  # pragma: no cover - only a host that changed its ctor entirely
                raise last
            self._loop: Optional[asyncio.AbstractEventLoop] = None
            self.runtime = None
            self._runtime_lock = threading.Lock()
            #: chat_id → Message-ID of its last inbound mail (outbound threading, §6.6).
            self._last_inbound: Dict[str, str] = {}
            self._inbound_lock = threading.Lock()

        # ── lifecycle ───────────────────────────────────────────────────
        async def connect(self, **_kwargs: Any) -> bool:
            """Capture the gateway loop and start the streams. Binds nothing."""
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None
            self._mark_connected()
            runtime = self.runtime
            if runtime is not None:
                # reconcile() starts threads and must not block the gateway loop.
                asyncio.get_event_loop().run_in_executor(None, runtime.reconcile)
            return True

        async def disconnect(self) -> None:
            runtime = self.runtime
            if runtime is not None:
                runtime.stop()
            self._loop = None
            # Host API: `_mark_connected(*, listener_base=None)` takes no positional flag and
            # `_mark_disconnected()` is the counterpart. Passing `False` raised
            # "BasePlatformAdapter._mark_connected() takes 1 positional argument but 2 were
            # given" on every teardown (measured in the gateway log).
            for name in ("_mark_disconnected", "_mark_connected"):
                fn = getattr(self, name, None)
                if callable(fn):
                    try:
                        fn()
                        break
                    except TypeError:
                        continue

        # ── outbound ────────────────────────────────────────────────────
        async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                       **_kwargs: Any) -> Any:
            """Gateway-level reply → an outbound mail.

            Threading (§6.6/C8): the mail being answered is the last one this session
            received, so the outbound carries ``In-Reply-To``/``References`` for it —
            without that the external side sees a brand-new conversation, and its answer
            would come back as a new thread. ``send_mail`` records the outbound alias, so
            the reply-to-our-reply lands here again.
            """
            try:
                import aimail_tools as _tools
                parent_mid = (str(reply_to or "").strip()
                              or self._last_inbound_mid(str(chat_id or "")))
                result = _tools.send_mail(to=str(_kwargs.get("to") or ""),
                                          subject=str(_kwargs.get("subject") or ""),
                                          body=content or "",
                                          message_id=parent_mid or None)
                ok = bool(isinstance(result, dict) and result.get("success", True))
                detail = str((result or {}).get("error", "")) if isinstance(result, dict) else ""
            except Exception as e:  # noqa: BLE001 — a send failure must be reported, not raised
                ok, detail = False, "%s: %s" % (type(e).__name__, e)
            return _send_result(event_module, ok, detail)

        def _last_inbound_mid(self, chat_id: str) -> str:
            """Message-ID of the last mail delivered into this session ("" when none).

            Kept in memory next to the session itself: the host session lives in this
            process, and a restart starts a fresh conversation anyway (the alias index
            on disk still resolves the thread, only the parent id is unknown ⇒ ``send``
            falls back to no threading rather than guessing).
            """
            with self._inbound_lock:
                return str(self._last_inbound.get(chat_id) or "")

        async def get_chat_info(self, chat_id: str) -> dict:
            """Minimal chat facts for host logs/UI (no mail body, no secrets)."""
            return {"id": chat_id, "name": "AIMail %s" % chat_id, "type": "dm"}

        # ── inbound bridge ──────────────────────────────────────────────
        async def adeliver(self, *, text: str, chat_id: str,
                           meta: Optional[dict] = None) -> bool:
            """Deliver one mail into the gateway; True only when the host accepted it."""
            # Parent id for outbound threading (§6.6): recorded here because this is the
            # one place that sees both the session (chat_id) and the mail identity.
            _mid = str((meta or {}).get("aimail_message_id") or "").strip()
            if _mid:
                with self._inbound_lock:
                    self._last_inbound[str(chat_id or "")] = _mid
            event = MessageEvent(
                text=text,
                message_type=MessageType.TEXT,
                source=self.build_source(chat_id=chat_id, chat_type="dm",
                                         chat_name="AIMail %s" % chat_id,
                                         user_id=chat_id, user_name="aimail"),
                internal=True,
                allow_gateway_control=False,
                metadata=dict(meta or {}),
            )
            await self.handle_message(event)
            return bool(getattr(event, "_gateway_accepted", False))

        def deliver(self, *, text: str, chat_id: str, meta: Optional[dict] = None) -> bool:
            """Thread-safe bridge for the pull loop (runs outside the gateway loop)."""
            coro = self.adeliver(text=text, chat_id=chat_id, meta=meta)
            loop = self._loop
            if loop is not None and loop.is_running():
                try:
                    running = asyncio.get_running_loop()
                except RuntimeError:
                    running = None
                if running is not loop:
                    fut = asyncio.run_coroutine_threadsafe(coro, loop)
                    return bool(fut.result(DELIVER_TIMEOUT_S))
            return bool(asyncio.run(coro))

    return AIMailAdapter


def _send_result(event_module: Any, ok: bool, detail: str = "") -> Any:
    """Build the host's ``SendResult`` without guessing one constructor shape."""
    cls = getattr(event_module, "SendResult", None) or getattr(
        __import__("gateway.platforms.base", fromlist=["SendResult"]), "SendResult", None)
    if cls is not None:
        for kwargs in ({"ok": ok, "detail": detail}, {"success": ok, "error": detail},
                       {"ok": ok}, {}):
            try:
                return cls(**kwargs)
            except TypeError:
                continue
    return {"ok": ok, "detail": detail}


def attach_runtime(adapter: Any, ctx: Any = None, agent_home: str = "",
                   client_for=None, log=None) -> Any:
    """Create and bind the runtime for an adapter instance (called at plugin register)."""
    from .runtime import HermesRuntime

    adapter.runtime = HermesRuntime(adapter, ctx=ctx, agent_home=agent_home,
                                    client_for=client_for, log=log)
    return adapter.runtime

# -*- coding: utf-8 -*-
"""Inbound runtime: aggregate SSE → preprocessing → host session, with honest acks.

Flow per delivery (contract §4.4/§9):

    fresh binding lookup (v2.binding, done inside v2.system_pull)
      → thread root (v2.thread) → chat_id
      → preprocessing (pysdk core: identity/persona/enrichment/attachments/ping-pong)
      → bridge.deliver(text, chat_id)   # the host platform adapter
      → accepted?  yes → {"status": "accepted"} (client acks)
                   no  → raise            (client leaves it pending, counts an error)

Preprocessing BEFORE the host call is a hard rule: the enriched payload is what the
agent must see, ping/pong must never create a session, and a preprocessing failure must
not produce a session that then has to be cleaned up (refactor plan §7.1).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

import aimail_base as _base
from v2 import binding as _binding
from v2 import outbound as _outbound
from v2 import system_pull as _system_pull
from v2 import thread as _thread

logger = logging.getLogger(__name__)

#: Tells the model this mail came from outside (parity: the a2a adapter's framing rule).
INBOUND_MARKER = "[AIMail inbound]"


def render_inbound(enriched: Dict[str, Any]) -> str:
    """Turn the enriched payload into the text the agent receives.

    Kept boring on purpose: the shared email pipeline already normalised the content, and
    the reply protocol lives in the mail SKILL, so the adapter only adds provenance.
    """
    enriched = enriched or {}
    subject = str(enriched.get("subject") or "").strip() or "(no subject)"
    sender = str(enriched.get("from") or enriched.get("sender") or "").strip()
    to = str(enriched.get("to") or enriched.get("my_aimail_addr") or "").strip()
    body = enriched.get("body")
    if not isinstance(body, str):
        body = str(enriched.get("text") or body or "")
    head = [INBOUND_MARKER, "subject: %s" % subject]
    if sender:
        head.append("from: %s" % sender)
    if to:
        head.append("to: %s" % to)
    return "\n".join(head) + "\n\n" + body.strip()


class HermesRuntime:
    """Owns the aggregate streams for ONE profile (one AgentSystemId)."""

    def __init__(self, bridge: Any, *, ctx: Any = None, agent_home: str = "",
                 log: Optional[Callable[[str], None]] = None,
                 client_for: Optional[Callable[[dict], Any]] = None,
                 agent_system_id: str = "") -> None:
        from . import identity as _identity

        self._bridge = bridge
        self._ctx = ctx
        self._log = log or (lambda line: logger.info("%s", line))
        self._client_for = client_for
        self.agent_home = _identity.resolve_agent_home(ctx, agent_home)
        self.agent_system_id = agent_system_id or _identity.agent_system_id(ctx, self.agent_home)
        self.workspace_path = _identity.workspace_path(ctx, self.agent_home)
        self.handles: List[_system_pull.SystemPullHandle] = []
        self._lock = __import__("threading").RLock()

    # ── streams ──────────────────────────────────────────────────────────
    def reconcile(self) -> List[_system_pull.SystemPullHandle]:
        """(Re)arm one stream per (AgentSystemId, Gateway) from the bindings on disk."""
        with self._lock:
            _system_pull.stop_all(self.handles)
            self.handles = _system_pull.start_reconcile(
                agent_system_id=self.agent_system_id,
                on_email=self.deliver,
                client_for=self._client_for,
                log=self._log,
            )
            self._log("[aimail-hermes] armed %d stream(s) for agent:%s (profile home %s)"
                      % (len(self.handles), self.agent_system_id, self.agent_home))
            return list(self.handles)

    def stop(self) -> None:
        with self._lock:
            _system_pull.stop_all(self.handles)
            self.handles = []

    # ── one delivery ─────────────────────────────────────────────────────
    def deliver(self, cfg: Optional[dict], mail: dict) -> dict:
        """Hand one mail to the host session. Returns the typed client result.

        A host refusal RAISES on purpose: the shared client loop acks everything that
        returns normally, so "not accepted" must not be expressed as a return value
        (that is the difference between "mail stays pending" and "mail silently lost").
        """
        cfg = cfg or {}
        payload = mail.get("body") if isinstance(mail.get("body"), dict) else {}
        headers = mail.get("headers") if isinstance(mail.get("headers"), dict) else {}
        # A reply to OUR reply carries our outbound Message-ID as references[0]; the
        # binding's alias index turns that back into the conversation it belongs to (§6.6).
        root = _outbound.resolve_root(payload, headers, _outbound.binding_dir(cfg))
        chat_id = _thread.chat_id_for(root)

        enriched = _base.process_inbound_mail(payload, headers)
        if enriched is None:
            # The pipeline consumed the frame (ping/pong): handled, no session, and the
            # delivery must NOT be re-fetched forever.
            self._log("[aimail-hermes] %s: consumed by the inbound pipeline (no session)"
                      % (mail.get("email") or cfg.get("email") or ""))
            return _binding.accepted_result("consumed-by-pipeline")

        text = render_inbound(enriched)
        accepted = self._bridge.deliver(
            text=text,
            chat_id=chat_id,
            meta={
                "aimail_email": str(cfg.get("email") or mail.get("email") or ""),
                "aimail_thread_root": root,
                "aimail_message_id": _outbound.normalize(payload.get("message_id")
                                                         or headers.get("message-id")),
                "aimail_gateway": str(cfg.get("gateway_url") or ""),
                "aimail_delivery_id": mail.get("id"),
            },
        )
        if accepted is not True:
            raise RuntimeError("host did not accept delivery id=%s (chat_id=%s)"
                               % (mail.get("id"), chat_id))
        return _binding.accepted_result(str(chat_id))

# -*- coding: utf-8 -*-
"""Email-thread identity and host-session mapping (contract §9.4-§9.7).

Why a module: the ONE rule every host adapter must apply is "same email thread ⇒ same
session, new email thread ⇒ new session". Hermes expresses a session by a platform
``chat_id``; DeerFlow expresses it by a ``thread_id``. Both derive from the same
``thread_root``, so the rule lives here, not in each adapter.

Thread root (parity: tssdk ``runtime/session-router.ts``):
    references[0] → in_reply_to → message_id

A reply carries ``References`` whose first entry is the thread's original Message-ID, so
"reply to the same thread" resolves to the same root even after many hops; a fresh mail
has neither and falls back to its own Message-ID.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Mapping, Optional

#: Prefix of the synthetic chat id Hermes sessions are keyed by ("aimail:<hash>").
CHAT_ID_PREFIX = "aimail:"


def _first_ref(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return str(value[0]).strip() if value else ""
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split()[0].strip("<>").strip()


def _header(headers: Mapping[str, Any], *names: str) -> str:
    """Case-insensitive header read (hosts hand over lower-cased or original names)."""
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    for name in names:
        if name.lower() in lowered:
            value = lowered[name.lower()]
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            return str(value or "").strip()
    return ""


def thread_root(payload: Optional[Mapping[str, Any]] = None,
                headers: Optional[Mapping[str, Any]] = None) -> str:
    """Resolve the stable thread identity for one delivery.

    Both arguments are optional and equivalent in intent: the gateway's pull payload
    carries ``references``/``in_reply_to``/``message_id`` at the top level, while other
    hosts expose only raw ``headers``. Whichever is present wins, in the documented
    order, and an empty result means "no thread information" (⇒ a new thread).
    """
    payload = payload or {}
    headers = headers or {}
    refs = payload.get("references")
    first = _first_ref(refs)
    if not first:
        first = _first_ref(headers.get("references") or _header(headers, "references"))
    in_reply_to = str(payload.get("in_reply_to") or "").strip()
    if not in_reply_to:
        in_reply_to = _header(headers, "in-reply-to", "in_reply_to")
    message_id = str(payload.get("message_id") or "").strip()
    if not message_id:
        message_id = _header(headers, "message-id", "message_id")
    root = (first or in_reply_to or message_id).strip().strip("<>").strip()
    return root


def chat_id_for(root: str) -> str:
    """Deterministic Hermes ``chat_id`` for a thread root.

    The host keys sessions by ``chat_id``, so this value IS the session identity: the
    same thread root must always produce the same string across restarts and processes
    (no counter, no random suffix), must be short, and must contain nothing a host's
    session key cannot carry. A 12-hex digest of the root gives all three.
    """
    value = str(root or "").strip()
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else "0" * 12
    return CHAT_ID_PREFIX + digest


def thread_id_for(gateway_system_id: str, address: str, root: str) -> str:
    """Deterministic DeerFlow ``thread_id`` (uuid5-shaped, contract §9.7).

    Scoped by GatewaySystemId + address so two mailboxes never collide, and by the thread
    root so replies land in the same thread.
    """
    import uuid

    seed = "%s|%s|%s" % (str(gateway_system_id or ""), str(address or "").lower(),
                         str(root or ""))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "aimail-thread:" + seed))


def route(meta: Optional[Mapping[str, Any]], workspace_path: str = "") -> Dict[str, Any]:
    """Follow-up vs new session decision (parity: tssdk ``routeThread``).

    Follow up only when a session is recorded for this thread AND it belongs to the same
    workspace; otherwise the delivery starts a new session in the binding's workspace
    (contract §9.6/§9.7).
    """
    meta = meta or {}
    session_id = str(meta.get("session_id") or "").strip()
    meta_ws = str(meta.get("workspace_path") or "").strip()
    want_ws = str(workspace_path or "").strip()
    if session_id and (not meta_ws or not want_ws or meta_ws == want_ws):
        return {"kind": "followup", "session_id": session_id}
    return {"kind": "new", "session_id": ""}

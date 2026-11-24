# -*- coding: utf-8 -*-
"""Outbound Message-ID aliases: keep a reply in the thread it replies to (§6.6).

The problem this solves is one email-header fact:

    an external party replying to OUR reply sends ``References: <our-outbound-id> …``

``v2.thread.thread_root`` takes ``references[0]`` as the thread root, which is right for a
conversation we only ever receive — but the first entry of a reply-to-our-reply is the
Message-ID *we* minted, so the naive root changes on every round trip and every reply
would open a NEW host session (contract C8).

So every outbound mail records one alias ``outbound message-id → thread root``, written
next to the binding that sent it. An inbound reply then chases its root through the
aliases, and a reply-to-our-reply lands back on the original thread.

Deliberate policies (contract §6.6):

* **scoped per binding** — the alias index lives in the binding's own directory, so an
  address under another profile/AgentSystemId can never resolve here. No cross-profile,
  no cross-ASID guessing: an unknown id is simply unknown.
* **no subject matching** — ``Subject: Re: …`` is never used to infer a thread (two
  unrelated conversations share subjects constantly). Off by default means never.
* **unknown reference ⇒ new thread** — the chase stops and the raw root (the unknown id
  itself) is used. It is stable across deliveries, just not our thread.
* **bounded and cycle-safe** — the chase is capped, and a cycle (a corrupted store cannot
  produce one through ``record``, but a hand-edited file can) degrades to "no alias".

Parity: tssdk ``runtime/session-router.ts`` (``recordOutboundAlias``/``resolveThreadRoot``).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import aimail_base as _base
from v2 import thread as _thread

logger = logging.getLogger(__name__)

#: File name inside the binding directory.
ALIAS_FILE = "outbound-aliases.json"

#: Keep the index small: a mailbox can send thousands of mails, and only recent threads
#: can still receive a reply that matters. Oldest entries are dropped first.
ALIAS_LIMIT = 512

#: Root chase depth. A reply chain is one hop per outbound round trip; 8 is far beyond
#: any real conversation and still bounded.
MAX_HOPS = 8

_LOCK = threading.Lock()


def normalize(message_id: Any) -> str:
    """Canonical Message-ID for a key: no angle brackets, no stray whitespace."""
    return str(message_id or "").strip().strip("<>").strip()


def binding_dir(config: Optional[Mapping[str, Any]]) -> str:
    """Directory that owns this binding (``…/systems/<sid>/<addr>``), or ``""``.

    Derived from ``config_path`` because that is the one field every activation writes
    and every host config already carries — no new persisted state is invented.
    """
    cfg = dict(config or {})
    path = str(cfg.get("config_path") or cfg.get("_config_path") or "").strip()
    if not path:
        return ""
    return str(Path(path).parent)


def _alias_path(directory: str) -> Path:
    return Path(directory) / ALIAS_FILE


def _load(directory: str) -> Dict[str, Any]:
    if not directory:
        return {}
    try:
        raw = json.loads(_alias_path(directory).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a missing/corrupt index means "no aliases"
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(directory: str, data: Dict[str, Any]) -> bool:
    if not directory:
        return False
    try:
        Path(directory).mkdir(parents=True, exist_ok=True)
        _base.atomic_write_private(_alias_path(directory),
                                   json.dumps(data, ensure_ascii=False, indent=2))
        return True
    except Exception as e:  # noqa: BLE001 — threading is best-effort, never fail a send
        logger.warning("[aimail] outbound alias write failed (%s): %s", directory, e)
        return False


def record(directory: str, message_id: Any, root: Any) -> bool:
    """Record ``message_id → root`` for an outbound mail. Idempotent.

    Returns True when the index was written. A message that IS its own root (a brand-new
    conversation) still gets an entry: it costs one slot and keeps the store uniform.
    """
    mid, target = normalize(message_id), normalize(root)
    if not directory or not mid:
        return False
    if not target:
        target = mid
    with _LOCK:
        data = _load(directory)
        aliases = data.get("aliases") if isinstance(data.get("aliases"), dict) else {}
        order = [normalize(m) for m in (data.get("order") or []) if normalize(m)]
        aliases[mid] = target
        order = [m for m in order if m != mid] + [mid]
        dropped = order[:-ALIAS_LIMIT] if len(order) > ALIAS_LIMIT else []
        for gone in dropped:
            aliases.pop(gone, None)
        order = order[-ALIAS_LIMIT:] if dropped else order
        return _save(directory, {"aliases": aliases, "order": order})


def lookup(directory: str, message_id: Any) -> str:
    """Root recorded for an outbound Message-ID, or ``""`` when this binding never sent it."""
    mid = normalize(message_id)
    if not directory or not mid:
        return ""
    aliases = _load(directory).get("aliases")
    if not isinstance(aliases, dict):
        return ""
    return normalize(aliases.get(mid))


def resolve_root(payload: Optional[Mapping[str, Any]] = None,
                 headers: Optional[Mapping[str, Any]] = None,
                 directory: str = "") -> str:
    """Thread root for one delivery, with outbound aliases folded in.

    Same resolution order as ``v2.thread.thread_root`` (``references[0]`` →
    ``in_reply_to`` → ``message_id``), then the root is chased through this binding's
    aliases so a reply to our reply returns to the conversation it belongs to.
    """
    root = _thread.thread_root(payload, headers)
    if not root or not directory:
        return root
    seen = {root}
    current = root
    for _ in range(MAX_HOPS):
        mapped = lookup(directory, current)
        if not mapped or mapped in seen:
            break
        seen.add(mapped)
        current = mapped
    return current


def threading(root: Any, parent_message_id: Any = "") -> Dict[str, str]:
    """``In-Reply-To``/``References`` for an outbound mail in thread ``root``.

    ``parent_message_id`` is the mail being answered (the last one we received in this
    thread); it comes first in ``References`` so the recipient's client threads our reply
    under the right conversation. With no parent we still anchor on the root, which is
    what a brand-new conversation needs.
    """
    anchor = normalize(parent_message_id) or normalize(root)
    if not anchor:
        return {}
    refs = [anchor]
    root_id = normalize(root)
    if root_id and root_id not in refs:
        refs.insert(0, root_id)
    return {"In-Reply-To": "<%s>" % anchor,
            "References": " ".join("<%s>" % r for r in refs)}


def self_check(directory: str) -> Dict[str, Any]:  # pragma: no cover — used by status tools
    """Shape/reachability of the alias index, for diagnostics (no secrets, no bodies)."""
    data = _load(directory)
    aliases = data.get("aliases") if isinstance(data.get("aliases"), dict) else {}
    return {"dir": directory, "path": str(_alias_path(directory)) if directory else "",
            "size": len(aliases), "limit": ALIAS_LIMIT,
            "writable": bool(directory) and os.access(directory, os.W_OK)}

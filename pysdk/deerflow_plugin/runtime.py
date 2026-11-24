# -*- coding: utf-8 -*-
"""Inbound runtime for DeerFlow: aggregate SSE → host run (contract §9, §11.1).

Per delivery:

    v2.system_pull (fresh binding lookup inside) →
      thread root (v2.thread) → deterministic thread_id →
      preprocessing (shared email pipeline; ping/pong never starts a run) →
      POST /api/threads/{tid}/runs   (Idempotency-Key = aimail:<message_id>) →
        run record returned  ⇒ accepted (the client acks)
        anything else        ⇒ raise (the client leaves it pending)

Why the host's own HTTP surface and not an in-process call: the run API is the same one the
UI uses, it owns thread creation and idempotency, and it needs no private host object from
us. The gateway talks to itself over loopback — we never open an inbound listener, which is
the whole point of removing the push path.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

import aimail_base as _base
from v2 import binding as _binding
from v2 import outbound as _outbound
from v2 import system_pull as _system_pull
from v2 import thread as _thread

logger = logging.getLogger(__name__)

#: Inbound provenance marker (parity with the Hermes adapter).
INBOUND_MARKER = "[AIMail inbound]"

#: Where the host's own HTTP API listens inside the image (uvicorn default).
DEFAULT_API_BASE = "http://127.0.0.1:8001"


def render_inbound(enriched: Dict[str, Any]) -> str:
    enriched = enriched or {}
    subject = str(enriched.get("subject") or "").strip() or "(no subject)"
    sender = str(enriched.get("from") or enriched.get("sender") or "").strip()
    body = enriched.get("body")
    if not isinstance(body, str):
        body = str(enriched.get("text") or body or "")
    head = [INBOUND_MARKER, "subject: %s" % subject]
    if sender:
        head.append("from: %s" % sender)
    return "\n".join(head) + "\n\n" + body.strip()


def _post(url: str, payload: dict, *, headers: Optional[dict] = None,
          timeout: float = 60.0) -> tuple:
    data = json.dumps(payload).encode()
    hdrs = {"content-type": "application/json", "accept": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, method="POST", headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001 — transport failure is the caller's signal
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def _post_when_ready(url: str, payload: dict, *, headers: Optional[dict] = None,
                     deadline_s: float = 60.0) -> tuple:
    """POST to the host's own API, waiting out its startup window.

    The extension installs while the host app is still inside its lifespan startup, so the
    first delivery can arrive before uvicorn accepts connections. One refusal would leave
    the mail pending with no second chance: inside a live SSE stream an unacked delivery is
    only re-offered on the next connect. Only "nothing is listening" (status 0) is waited
    for — a real HTTP answer is returned at once, and the deadline keeps us bounded.
    """
    deadline = time.monotonic() + max(0.0, float(deadline_s))
    delay = 0.25
    while True:
        status, body = _post(url, payload, headers=headers)
        if status != 0 or time.monotonic() >= deadline:
            return status, body
        time.sleep(delay)
        delay = min(delay * 2.0, 4.0)

class DeerFlowRuntime:
    """Owns the aggregate streams for the gateway instance."""

    def __init__(self, config: Any = None, *, agent_home: str = "",
                 log: Optional[Callable[[str], None]] = None,
                 client_for: Optional[Callable[[dict], Any]] = None,
                 api_base: str = "") -> None:
        from . import identity as _identity

        self._cfg = dict(config or {})
        self._log = log or (lambda line: logger.info("%s", line))
        self._client_for = client_for
        self.api_base = (api_base or str(self._cfg.get("api_base") or "")
                         or os.environ.get("AIMAIL_DEERFLOW_API") or DEFAULT_API_BASE).rstrip("/")
        self.agent_home = _identity.resolve_home(self._cfg, agent_home)
        self.agent_system_id = str(self._cfg.get("agent_system_id") or "") or \
            _identity.agent_system_id(self._cfg, self.agent_home)
        self.workspace_path = _identity.workspace_path(self._cfg, self.agent_home)
        self.handles: list = []

    # ── streams ──────────────────────────────────────────────────────────
    def start(self) -> list:
        # Contract §7.1: the core needs to know this instance's identity and space when an
        # activation writes a binding (core never imports a host layer).
        try:
            _base.set_binding_context_provider(lambda: {
                "agent_system_id": self.agent_system_id,
                "workspace_path": self.workspace_path,
                "address_model": "multi",
            })
        except Exception as e:  # noqa: BLE001
            self._log("[aimail-deerflow] binding context provider not installed: %s" % e)
        self.handles = _system_pull.start_reconcile(
            agent_system_id=self.agent_system_id,
            on_email=self.deliver,
            client_for=self._client_for,
            log=self._log,
        )
        self._log("[aimail-deerflow] armed %d stream(s) for agent:%s (home %s, api %s)"
                  % (len(self.handles), self.agent_system_id, self.agent_home, self.api_base))
        return self.handles

    def stop(self) -> None:
        _system_pull.stop_all(self.handles)
        self.handles = []

    # ── one delivery ─────────────────────────────────────────────────────
    def deliver(self, cfg: Optional[dict], mail: dict) -> dict:
        cfg = cfg or {}
        payload = mail.get("body") if isinstance(mail.get("body"), dict) else {}
        headers = mail.get("headers") if isinstance(mail.get("headers"), dict) else {}
        # Reply-to-our-reply: references[0] is OUR outbound Message-ID, so the binding
        # alias index maps it back to the original thread (§6.6) — otherwise the reply
        # opens a brand-new thread on every round trip.
        root = _outbound.resolve_root(payload, headers, _outbound.binding_dir(cfg))
        gateway_system_id = str(cfg.get("system_id") or "")
        address = str(cfg.get("email") or mail.get("email") or "")
        thread_id = _thread.thread_id_for(gateway_system_id, address, root)

        enriched = _base.process_inbound_mail(payload, headers)
        if enriched is None:
            self._log("[aimail-deerflow] %s: consumed by the inbound pipeline (no run)" % address)
            return _binding.accepted_result("consumed-by-pipeline")

        # The thread must exist before a run names it; the host's create is idempotent.
        status, body = _post_when_ready("%s/api/threads" % self.api_base,
                                        {"thread_id": thread_id,
                                         "metadata": {"aimail_email": address}})
        if status not in (200, 201):
            raise RuntimeError("thread create failed for %s (status=%s)" % (thread_id, status))

        run_body = {
            "input": {"messages": [{"role": "user", "content": render_inbound(enriched)}]},
            "metadata": {"aimail_email": address, "aimail_thread_root": root},
        }
        message_id = str(payload.get("message_id") or "")
        idem = "aimail:%s" % (message_id or "delivery-%s" % mail.get("id"))
        status, run = _post_when_ready("%s/api/threads/%s/runs" % (self.api_base, thread_id),
                                       run_body, headers={"Idempotency-Key": idem})
        if status in (200, 201) and isinstance(run, dict) and run.get("run_id"):
            self._log("[aimail-deerflow] run %s accepted for %s (thread %s)"
                      % (run.get("run_id"), address, thread_id))
            return _binding.accepted_result(thread_id)
        # A refusal must not return normally: the shared client acks every normal return.
        raise RuntimeError("run rejected for %s (status=%s body=%s)"
                           % (address, status, str(run)[:160]))

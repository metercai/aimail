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


def _get(url: str, *, timeout: float = 30.0) -> tuple:
    """GET one host resource. Same envelope as ``_post`` (status, parsed body)."""
    req = urllib.request.Request(url, method="GET", headers={"accept": "application/json"})
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


def _landing_verification_enabled() -> bool:
    """Read-back verification is on unless explicitly disabled (old hosts may lack the route)."""
    raw = str(os.environ.get("AIMAIL_DEERFLOW_VERIFY_LANDING", "")).strip().lower()
    return raw not in ("0", "false", "off", "no")


def _landing_problem(record: Any) -> str:
    """Why one persisted run record still means "not delivered" (empty = fine).

    DeerFlow persists a run row before executing it, and its ``RunStatus`` enum is
    ``pending | running | success | error | timeout | interrupted``: a 200 carrying a terminal
    *failure* status is the host telling us the message will never be processed, so acking it
    would drop the mail. ``failed``/``cancelled`` stay in the set for hosts that spell it so.
    """
    status = str((record or {}).get("status") or "").strip().lower() if isinstance(record, dict) else ""
    if status in ("error", "timeout", "interrupted", "failed", "cancelled", "canceled"):
        return "run status=%s" % status
    return ""


def _detail(body: Any) -> str:
    """The host's own words for a refusal (lowercased), for telling "not found" cases apart.

    A 404 is ambiguous: deerflow answers ``{"detail": "Thread not found"}`` for a deleted thread and
    ``{"detail": "Not Found"}`` for a route the deployment does not have. Only the first is a
    session-gone signal; treating the second as one would invent redirects that never happened.
    """
    if isinstance(body, dict):
        for key in ("detail", "error", "message"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
    return ""


def _thread_presence(api_base: str, thread_id: str) -> str:
    """Has the host got this thread at all: ``present`` | ``gone`` | ``unknown``.

    C17 (§9.3) wants the session-gone redirect to be *observable*, not inferred: every delivery used
    to POST the thread unconditionally (the host's create is idempotent), so the plugin never knew a
    thread had been deleted and could not say so. This probe is what turns "re-attached" into a fact
    with a reason attached. An auth refusal, or a host without the route, is ``unknown`` — never
    ``gone``.
    """
    status, body = _get("%s/api/threads/%s" % (api_base, thread_id))
    if status == 200:
        return "present"
    if status == 404 and "thread" in _detail(body):
        return "gone"
    return "unknown"
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

        # C17 §9.3: ask BEFORE re-creating whether the thread is still there. The create below is
        # idempotent, so without this probe the plugin cannot tell "re-attached after the session was
        # deleted" from "the usual delivery" — and ops end up with a silent redirect.
        presence = _thread_presence(self.api_base, thread_id) if _landing_verification_enabled() else "unknown"
        if presence == "gone":
            self._log("[aimail-deerflow] session-gone: thread %s is gone (root %s) — re-attaching in "
                      "place for %s (contract §9.3, reason=session-gone)"
                      % (thread_id, root or "(none)", address))

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
            run_id = str(run.get("run_id"))
            # §8.3 "已投递" vs "宿主已接管": DeerFlow returns a durable run id and exposes a
            # read-back for it, so ask the host what became of the run it just accepted. A healthy
            # record ⇒ ack; a terminal-failure status ⇒ raise (no ack), so the mail is retried.
            if _landing_verification_enabled():
                check_status, record = _get(
                    "%s/api/threads/%s/runs/%s" % (self.api_base, thread_id, run_id))
                if check_status in (401, 403):
                    # Auth/scope, not delivery: the deployment may disable auth
                    # (DEER_FLOW_AUTH_DISABLED=1, which is what makes our own POST pass) while a
                    # read stays scoped to another owner. Accept on admission, and say so.
                    self._log("[aimail-deerflow] landing-unverified for run %s (read-back status=%s)"
                              " — accepting on admission; set AIMAIL_DEERFLOW_VERIFY_LANDING=0 to silence"
                              % (run_id, check_status))
                elif check_status == 404 and "run" in _detail(record):
                    # The thread create AND the run POST both answered 2xx from this same app, so
                    # the route exists: a 404 naming the RUN means the run we were handed is not
                    # there. That is the "acked and dropped" case — refuse to ack, let it be re-pulled.
                    raise RuntimeError("run %s is not readable after a 2xx admission (read-back 404)"
                                       " — not acking" % run_id)
                elif check_status == 404:
                    # Route absent (older host): nothing to verify against, not a delivery verdict.
                    self._log("[aimail-deerflow] landing-unverified for run %s (no read-back route)"
                              " — accepting on admission; set AIMAIL_DEERFLOW_VERIFY_LANDING=0 to silence"
                              % run_id)
                elif check_status != 200:
                    raise RuntimeError("landing unverified for run %s (read-back status=%s)"
                                       % (run_id, check_status))
                else:
                    problem = _landing_problem(record)
                    if problem:
                        raise RuntimeError("run %s did not land (%s)" % (run_id, problem))
            self._log("[aimail-deerflow] run %s accepted for %s (thread %s)"
                      % (run_id, address, thread_id))
            return _binding.accepted_result(thread_id)
        # A refusal must not return normally: the shared client acks every normal return.
        raise RuntimeError("run rejected for %s (status=%s body=%s)"
                           % (address, status, str(run)[:160]))

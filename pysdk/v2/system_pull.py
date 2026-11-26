# -*- coding: utf-8 -*-
"""One stream per ``(AgentSystemId, Gateway)`` — the V2 pull entry (contract §6/§8).

Responsibilities kept deliberately small:

  * own the loop's lifecycle (start / stop / join / stats) for exactly ONE group;
  * build its client through ``pull_client.build_agent_system_client`` (never by hand);
  * hand every delivery to the caller's ``on_email`` and let the SHARED client loop do
    the ack bookkeeping (``accepted`` ⇒ ack, ``skip`` ⇒ leave pending, raise ⇒ error);
  * stop loudly on an auth-fatal streak instead of retrying a dead key forever.

Not responsibilities (they live with the host adapters): preprocessing, session
creation, thread routing, and deciding whether a delivery was accepted.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, Optional

import aimail_base as _base
from . import binding as _binding
from . import pull_client as _pull_client

#: Consecutive 401/403 before the loop stops (parity: tssdk ``AUTH_FATAL_STREAK``).
AUTH_FATAL_STREAK = 3

#: Audit lines asserted by the L2 gate (S2/S4) — keep them byte-stable.
RECONCILE_LINE = "[aimail-pull] SSE reconcile: %d gateway stream(s) from %d binding(s)"
GROUP_LINE = ("[aimail-pull] aggregate agent:%s gw=%s members=[%s] (rep %s, %d/%d active)")
SKIP_LINE = "[aimail-pull] skip %s: %s (mail stays pending)"
ACK_LINE = "[aimail-pull] ack %d id(s) gw=%s"
AUTH_FATAL_LINE = "[aimail-v2] auth fatal on gw=%s (status=%s, streak=%d) — stream stopped"
NO_BINDING_LINE = ("[aimail-pull] no agent-scope binding — polling not started "
                   "(push path unchanged; address-code activation enables it)")


class SystemPullHandle:
    """Handle for one aggregate stream (shape mirrors the legacy ``AgentPullHandle``)."""

    def __init__(self, agent_system_id: str, gateway_url: str, settings: dict) -> None:
        self.agent_system_id = agent_system_id
        self.gateway_url = gateway_url
        self.email = "agent:%s" % agent_system_id  # audit label (the rep address is not the key)
        self.started = True
        self.reason = str(settings.get("reason", "enabled"))
        self.interval_ms = int(settings.get("interval_ms", _base.DEFAULT_PULL_INTERVAL_MS))
        self.limit = int(settings.get("limit", _base.DEFAULT_PULL_LIMIT))
        self._stats: Dict[str, int] = {"pulled": 0, "acked": 0, "errors": 0, "skipped": 0}
        self._thread: Optional[threading.Thread] = None
        self._stop: Optional[threading.Event] = None
        self._fatal = ""

    # ── lifecycle ────────────────────────────────────────────────────────
    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()

    def alive(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def join(self, timeout: Optional[float] = None) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def fatal(self) -> str:
        """Non-empty when the loop stopped because the gateway rejected the key."""
        return self._fatal

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<SystemPullHandle %s gw=%s %s>" % (
            self.agent_system_id, self.gateway_url, self.reason)


def start_system_pull(*, agent_system_id: str, gateway_url: str, api_key: str,
                      representative_email: str, members: list,
                      on_email: Callable[[dict, dict], Any],
                      settings: Optional[dict] = None,
                      client=None,
                      env: Optional[dict] = None,
                      log: Optional[Callable[[str], None]] = None,
                      stop_event: Optional[threading.Event] = None,
                      thread_name: str = "") -> SystemPullHandle:
    """Start the aggregate stream for one ``(AgentSystemId, Gateway)`` group.

    ``on_email(cfg, mail)`` receives the CURRENT binding (re-read per delivery) and the
    mail envelope; returning ``{"status": "skip", ...}`` leaves the mail pending.
    """
    log = log or (lambda line: None)
    env = dict(os.environ if env is None else env)
    asid = str(agent_system_id or "").strip()
    gw = str(gateway_url or "").rstrip("/")
    if not asid:
        raise ValueError("start_system_pull requires agent_system_id")
    if not gw:
        raise ValueError("start_system_pull requires gateway_url")

    rep = str(representative_email or "").strip()
    resolved = settings or _base.resolve_agent_pull_settings(
        {"system_id": "agent:" + asid, "api_key": api_key,
         "gateway_url": gw, "agent_system_id": asid, "email": rep}, env=env)
    handle = SystemPullHandle(asid, gw, resolved)
    if not resolved.get("enabled"):
        handle.started = False
        log("[aimail-pull] aggregate agent:%s gw=%s: pull disabled (%s) — no stream"
            % (asid, gw, resolved.get("reason", "")))
        return handle


    if members is None:
        # Parity with tssdk `startSystemPull`: the group's bindings ARE the anchor claims, so a
        # caller that forgets them would send an anchor-less aggregate pull — refused by the gateway
        # with 403 `anchor_required` and indistinguishable from a dead key. Resolve them here.
        try:
            members = _binding.iter_active(asid)
        except Exception:  # noqa: BLE001 — a broken systems dir must not stop arming
            members = []
    client = client or _pull_client.build_agent_system_client(
        gw, api_key, asid, identity=rep, members=members)
    stop_event = stop_event if stop_event is not None else threading.Event()
    handle._stop = stop_event

    def _deliver(mail: dict):
        """Fresh binding lookup → on_email → typed result (the client acks on it)."""
        log("[aimail-pull] delivery id=%s to=%s gw=%s" % (mail.get("id"),
                                                          mail.get("email", ""), gw))
        cfg, skip = _binding.fresh_target(mail.get("email", ""), gw)
        if skip:
            log(SKIP_LINE % (mail.get("email", ""), skip))
            return _binding.skip_result(skip)
        return on_email(cfg, mail)

    def _run() -> None:
        stats = handle._stats
        log("[aimail-pull] stream start agent:%s gw=%s mode=%s interval=%ss"
            % (asid, gw, resolved.get("mode"), resolved.get("interval_ms", "") and
               int(resolved.get("interval_ms")) / 1000.0))
        interval_s = resolved.get("interval")
        if interval_s is None:
            interval_s = int(resolved.get("interval_ms", _base.DEFAULT_PULL_INTERVAL_MS)) / 1000.0
        try:
            got = client.start_polling(
                _deliver,
                interval=float(interval_s),
                limit=int(resolved.get("limit", _base.DEFAULT_PULL_LIMIT)),
                stop_event=stop_event,
                mode=resolved.get("mode"),
            )
            if isinstance(got, dict):
                for k, v in got.items():
                    if isinstance(v, int):
                        stats[k] = v
            log("[aimail-pull] stream ended agent:%s gw=%s pulled=%s acked=%s errors=%s skipped=%s"
                % (asid, gw, stats.get("pulled"), stats.get("acked"),
                   stats.get("errors"), stats.get("skipped")))
            if stats.get("acked"):
                log(ACK_LINE % (stats["acked"], gw))
        except Exception as e:  # noqa: BLE001 — one group never takes the host down
            handle._fatal = str(e)
            if "auth" in str(e).lower():
                log(AUTH_FATAL_LINE % (gw, getattr(e, "status", "n/a"), AUTH_FATAL_STREAK))
                # The gateway is the authority on a binding's claims: it rejected this key, so the
                # members stop being armable in this process (parity: tssdk poll-entry).
                try:
                    import v2.binding as _binding_mod
                    for _m in (members or []):
                        _binding_mod.refuse_after_gateway_rejection(str(_m.get("email") or ""))
                    log("[aimail-v2] gw=%s: gateway rejected the key — members=%s are no longer "
                        "armed; re-bind with an activation code"
                        % (gw, ",".join(str(m.get("email") or "") for m in (members or []))))
                except Exception:  # noqa: BLE001 — bookkeeping must not mask the fatal
                    pass
            else:
                log("[aimail-v2] stream failed on gw=%s: %s" % (gw, e))

    handle._thread = threading.Thread(
        target=_run, name=thread_name or ("aimail-pull:agent:%s:%s" % (asid, gw)), daemon=True)
    handle._thread.start()
    log(GROUP_LINE % (asid, gw, ",".join(str(m.get("email", "")) for m in members or []), rep,
                      sum(1 for m in (members or []) if m.get("paused") is not True),
                      len(members or [])))
    return handle


def start_reconcile(*, agent_system_id: str, on_email: Callable[[dict, dict], Any],
                    api_key_for: Optional[Callable[[dict], str]] = None,
                    settings_for: Optional[Callable[[dict], dict]] = None,
                    client_for: Optional[Callable[[dict], Any]] = None,
                    env: Optional[dict] = None,
                    log: Optional[Callable[[str], None]] = None,
                    system_id: str = "") -> list:
    """Arm ONE stream per ``(AgentSystemId, Gateway)`` from the bindings on disk.

    The single production entry a host adapter calls on load, on binding-created, and
    after pause/resume/unbind. It prints the standardized reconcile line the L2 gate
    asserts (S2/S4).
    """
    log = log or (lambda line: None)
    asid = str(agent_system_id or "").strip()
    active = _binding.iter_active(asid, system_id=system_id) if asid else []
    # Group over WITH-paused rows so an all-paused group is reported as such (contract
    # §13.1 removes a paused address from the aggregate set; it does not hide the group).
    groupable = (_binding.iter_active(asid, system_id=system_id, include_paused=True)
                 if asid else [])
    groups = _binding.group_by_gateway(groupable)
    armed = [g for g in groups.values() if _binding.representative(g)]
    log(RECONCILE_LINE % (len(armed), len(active)))
    if not groups:
        log(NO_BINDING_LINE)
        return []
    handles = []
    for (group_asid, gw), members in groups.items():
        rep = _binding.representative(members)
        if rep is None:
            log("[aimail-pull] aggregate agent:%s gw=%s: all members paused — no stream (members: %s)"
                % (group_asid, gw, ", ".join(str(m.get("email", "")) for m in members)))
            continue
        key = str((api_key_for(rep) if api_key_for else rep.get("api_key")) or "")
        if not key:
            log("[aimail-pull] aggregate agent:%s gw=%s: representative has no key — no stream"
                % (group_asid, gw))
            continue
        handle = start_system_pull(
            agent_system_id=group_asid,
            gateway_url=gw,
            api_key=key,
            representative_email=str(rep.get("email") or ""),
            members=members,
            on_email=on_email,
            settings=(settings_for(rep) if settings_for else None),
            client=(client_for(rep) if client_for else None),
            env=env,
            log=log,
        )
        if handle.started:
            handles.append(handle)
    return handles


def stop_all(handles) -> None:
    """Idempotent shutdown (process exit / plugin unload)."""
    for h in handles or []:
        try:
            h.stop()
        except Exception:  # noqa: BLE001 — shutdown must not raise
            pass

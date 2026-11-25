# -*- coding: utf-8 -*-
"""Binding selection, grouping and the fresh per-delivery lookup (contract §6/§7/§9).

Three questions this module answers, and nothing else:

1. **May this binding arm at all?** — a V2 binding (``agent_system_id`` present), with a
   key, not paused, and owned by THIS AgentSystemId. A binding owned by another instance
   is skipped loudly instead of being pulled (contract §5.3: re-activate, don't share).
2. **How do bindings group into streams?** — one stream per
   ``(agent_system_id, gateway_url)``; the group's representative is the first ACTIVE
   binding by address, so the choice is deterministic across restarts.
3. **Where does THIS delivery go, right now?** — the arm-time group is a snapshot; a
   pause/archive that happens while the stream runs must take effect on the very next
   mail, so every delivery re-reads the binding (tssdk P0-6/P0-7).

Parity: tssdk ``poll-entry.ts`` (``isV2AgentScopeBinding``, ``startAgentSystemGroups``,
``freshDeliveryTarget``, ``countActiveAgentSystemBindings``) and ``stream/plan.ts``.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

import aimail_base as _base

#: Skip reasons (parity: TS ``PullDeliveryResult`` skip reasons).
SKIP_PAUSED = "paused"
SKIP_UNKNOWN = "unknown"

#: The only value a real activation writes into ``binding_source`` (parity: TS
#: ``BINDING_SOURCE_ACTIVATION``). A binding is a CACHE of the capability the gateway issued at
#: activation; a hand-written file is useless without that key, and this stamp is what lets the SDK
#: report the ones that were not produced by ``activate_address_code_persist``.
BINDING_SOURCE_ACTIVATION = "activate-address-code"

#: The seven fields a V2 binding must carry (contract §7.1).
REQUIRED_BINDING_FIELDS = ("email", "gateway_url", "domain", "system_id",
                           "agent_system_id", "api_key", "agent_id")


def binding_provenance(cfg) -> tuple:
    """Judge one binding's provenance. Returns ``(ok, reason)``; never raises.

    The judgement is unconditional: nothing has shipped yet, so an unstamped binding cannot be a
    legacy artefact — it is hand-written or a writer bug. ``is_armable`` refuses it and says why.
    """
    if not isinstance(cfg, dict):
        return (False, "no binding")
    missing = [f for f in REQUIRED_BINDING_FIELDS if not str(cfg.get(f) or "").strip()]
    if missing:
        return (False, "missing required field(s): " + ", ".join(missing))
    source = str(cfg.get("binding_source") or "").strip()
    if not source:
        return (False, "no activation stamp (binding_source) — this file was not written by "
                       "activate_address_code_persist; re-bind with an activation code")
    if source != BINDING_SOURCE_ACTIVATION:
        return (False, "unknown binding_source %r (expected %s)" % (source, BINDING_SOURCE_ACTIVATION))
    at = str(cfg.get("activated_at") or "").strip()
    if not at:
        return (False, "activated_at is missing")
    try:
        from datetime import datetime as _dt
        _dt.fromisoformat(at.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return (False, "activated_at is not a timestamp (%r)" % at)
    return (True, "")


def is_v2(cfg: dict) -> bool:
    """A V2 binding — the aggregate/ack rules apply only to these."""
    return _base.is_v2_agent_scope_binding(cfg)


def is_v1(cfg: dict) -> bool:
    """A V1 (activation-artefact) binding — legacy per-address pull only."""
    return _base.is_v1_agent_scope_binding(cfg)


def _report_provenance(cfg: dict, reason: str) -> None:
    """Say it once per process per address — a silent refusal looks like "mail stopped"."""
    email = str(cfg.get("email") or "").strip().lower()
    key = (email, reason)
    if key in _REPORTED_PROVENANCE:
        return
    _REPORTED_PROVENANCE.add(key)
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "[aimail] %s: binding REFUSED — provenance not proven (%s); "
        "re-bind with an activation code (aimail_address)",
        email or "<no address>", reason)


#: (address, reason) tuples already reported — one line per case per process.
_REPORTED_PROVENANCE: set = set()

#: Addresses whose key the GATEWAY already rejected (401/403, auth-fatal) in this process.
#: The local stamp is self-consistency; the authority is the gateway, and when it says no the
#: binding must not be re-armed (otherwise the runtime keeps retrying a key that was refused).
_REFUSED_BY_GATEWAY: set = set()


def refuse_after_gateway_rejection(email: str) -> None:
    """Remember that the gateway rejected this address's key (call on an auth-fatal pull error)."""
    e = str(email or "").strip().lower()
    if e:
        _REFUSED_BY_GATEWAY.add(e)


def is_refused_by_gateway(email: str) -> bool:
    """Did the gateway already reject this address in this process?"""
    return str(email or "").strip().lower() in _REFUSED_BY_GATEWAY


def reset_gateway_rejections() -> None:
    """Test hook: forget gateway verdicts."""
    _REFUSED_BY_GATEWAY.clear()


def is_armable(cfg: dict, agent_system_id: str = "") -> bool:
    """Can this binding supply or join an aggregate stream for *agent_system_id*?"""
    if not is_v2(cfg):
        return False
    if not str(cfg.get("api_key") or ""):
        return False
    if cfg.get("paused") is True:
        return False
    want = str(agent_system_id or "").strip()
    if want and str(cfg.get("agent_system_id") or "").strip() != want:
        return False
    if is_refused_by_gateway(cfg.get("email")):
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[aimail] %s: binding REFUSED — the gateway already rejected this key (401/403); "
            "re-bind with an activation code (aimail_address)", str(cfg.get("email") or ""))
        return False
    # Provenance is enforced at ARMING, the point where a file turns into "we accept mail for this
    # address". No switch: an unstamped binding never arms, and the refusal names the reason.
    ok, reason = binding_provenance(cfg)
    if not ok:
        _report_provenance(cfg, reason)
        return False
    return True


def iter_active(agent_system_id: str, gateway_url: str = "",
                system_id: str = "", include_paused: bool = False) -> List[dict]:
    """V2 bindings for this AgentSystemId, deduped by address (sorted).

    Copied binding files (the same address present under several system dirs) collapse to
    one entry — otherwise a duplicated file would arm a second stream.

    ``include_paused=True`` keeps paused rows so a caller can report "all members paused"
    instead of showing zero bindings; such rows never produce a stream (contract §13.1:
    pause removes the address from the aggregate set).
    """
    want_asid = str(agent_system_id or "").strip()
    want_gw = str(gateway_url or "").rstrip("/")
    seen: Dict[str, dict] = {}
    for cfg in _base.iter_agentmail_configs(system_id):
        if include_paused:
            ok = (is_v2(cfg) and bool(str(cfg.get("api_key") or ""))
                  and (not want_asid
                       or str(cfg.get("agent_system_id") or "").strip() == want_asid))
        else:
            ok = is_armable(cfg, want_asid)
        if not ok:
            continue
        if want_gw and str(cfg.get("gateway_url") or "").rstrip("/") != want_gw:
            continue
        email = str(cfg.get("email") or "").strip().lower()
        if not email or email in seen:
            continue
        seen[email] = cfg
    return [seen[e] for e in sorted(seen)]


def group_by_gateway(bindings: Iterable[dict]) -> Dict[Tuple[str, str], List[dict]]:
    """Group armable bindings into ``{(agent_system_id, gateway_url): [members…]}``.

    Members stay sorted by address, so ``members[0]`` is the stable representative
    (parity: tssdk takes the first ACTIVE binding after sorting).
    """
    groups: Dict[Tuple[str, str], List[dict]] = {}
    for cfg in bindings:
        asid = str(cfg.get("agent_system_id") or "").strip()
        gw = str(cfg.get("gateway_url") or "").rstrip("/")
        if not asid or not gw:
            continue
        groups.setdefault((asid, gw), []).append(cfg)
    for key, members in groups.items():
        groups[key] = sorted(members, key=lambda c: str(c.get("email") or "").lower())
    return groups


def representative(members: Iterable[dict]) -> Optional[dict]:
    """First ACTIVE member (by address) or ``None`` when the whole group is paused."""
    for cfg in members or []:
        if cfg.get("paused") is not True:
            return cfg
    return None


def count_active(agent_system_id: str, gateway_url: str = "") -> int:
    """Audit accounting for the ``SSE reconcile`` line (deduped addresses)."""
    return len(iter_active(agent_system_id, gateway_url))


def find_by_email(email: str) -> Optional[dict]:
    """Locate a binding by address across all system dirs (first match wins)."""
    want = str(email or "").strip().lower()
    if not want:
        return None
    for cfg in _base.iter_agentmail_configs(""):
        if str(cfg.get("email") or "").strip().lower() == want:
            return cfg
    return None


def fresh_target(email: str, gateway_url: str = "") -> Tuple[Optional[dict], Optional[str]]:
    """Re-read the binding at delivery time.

    Returns ``(cfg, skip)``:
      * ``(cfg, None)``       ⇒ deliver;
      * ``(cfg, 'paused')``   ⇒ the address exists but is paused (no ack, not an error);
      * ``(None, 'unknown')`` ⇒ no binding for this address (no ack, not an error);
      * ``(cfg, 'unknown')``  ⇒ a binding exists for another Gateway ⇒ this delivery is
        not addressed here (contract §9.1: route by ``gateway_url + recipient``).
    """
    cfg = find_by_email(email)
    if cfg is None:
        return None, SKIP_UNKNOWN
    if cfg.get("paused") is True:
        return cfg, SKIP_PAUSED
    want_gw = str(gateway_url or "").rstrip("/")
    if want_gw and str(cfg.get("gateway_url") or "").rstrip("/") != want_gw:
        return cfg, SKIP_UNKNOWN
    return cfg, None


def skip_result(reason: str) -> dict:
    """The typed skip the client understands (not acked, not an error, re-evaluated)."""
    return {"status": "skip", "reason": reason}


def accepted_result(detail: str = "") -> dict:
    """The typed acceptance the client acks on."""
    out = {"status": "accepted"}
    if detail:
        out["detail"] = detail
    return out

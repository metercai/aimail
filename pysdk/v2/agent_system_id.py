# -*- coding: utf-8 -*-
"""Stable local client identity for one Agent instance (contract §5).

    AgentSystemId = "asid_" + sha256("aimail-agent-system-v1\\0" + home + "\\0" + fp)[:32]

Why it exists: the Gateway authorizes an aggregate SSE stream per *client instance*,
not per address and not per GatewaySystemId (contract §6.1). A host that cannot name
itself cannot aggregate.

Parity: tssdk `src/core/agent-system-id.ts` — same seed string, same truncation, same
``asid_`` prefix, same three environment seams, so a py host and a ts host on the same
home+fingerprint derive the SAME id (asserted by the cross-language test).

Not a secret (contract §5.1): it is derived from public machine facts and is never
stored as an identity file. Only ``binding.agent_system_id`` carries it.
"""
from __future__ import annotations

import hashlib
import os
import platform
import socket
from typing import Callable, Optional

#: Derivation seed. MUST match tssdk / bridge byte for byte.
SEED = "aimail-agent-system-v1"

#: Env seams (same names as tssdk, so both SDKs can be driven by one fixture).
ENV_AGENT_SYSTEM_ID = "AIMAIL_AGENT_SYSTEM_ID"
ENV_HARDWARE_FINGERPRINT = "AIMAIL_HARDWARE_FINGERPRINT"
ENV_AGENT_HOME = "AIMAIL_AGENT_HOME"

_MACHINE_ID_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")

_cached: Optional[str] = None
_agent_home_resolver: Optional[Callable[[], Optional[str]]] = None


def derive_agent_system_id(agent_home: str, hardware_fingerprint: str) -> str:
    """Pure derivation (exposed for tests and host-specific fingerprint seams).

    Raises ``ValueError`` on an empty home or fingerprint instead of degrading to a
    silently shared identity: two hosts deriving the same id would aggregate each
    other's mail, which is exactly the failure this identity exists to prevent.
    """
    home = os.path.realpath(str(agent_home or "").strip() or "/")
    if not str(agent_home or "").strip():
        raise ValueError("agent system id: empty agent home")
    fp = str(hardware_fingerprint or "").strip()
    if not fp:
        raise ValueError("agent system id: empty hardware fingerprint")
    digest = hashlib.sha256()
    digest.update((SEED + "\0").encode("utf-8"))
    digest.update(home.encode("utf-8"))
    digest.update(b"\0")
    digest.update(fp.encode("utf-8"))
    return "asid_" + digest.hexdigest()[:32]


def _read_machine_id() -> str:
    for path in _MACHINE_ID_PATHS:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
            if value:
                return value
        except OSError:
            continue
    return ""


def hardware_fingerprint() -> str:
    """Best-effort machine fingerprint: hostname+platform+arch+machine-id.

    Stable across restarts, distinguishes machines, contains no secrets.
    ``AIMAIL_HARDWARE_FINGERPRINT`` overrides it (test / fleet-imaging seam).
    """
    override = str(os.environ.get(ENV_HARDWARE_FINGERPRINT) or "").strip()
    if override:
        return override
    machine = _read_machine_id()
    return "|".join([
        socket.gethostname(),
        platform.system().lower() or platform.platform(),
        platform.machine(),
        machine or "no-machine-id",
    ])


def set_agent_home_resolver(fn: Optional[Callable[[], Optional[str]]]) -> None:
    """Host seam: derive from the AGENT home (profile/instance), not the OS user's home.

    Without it, several agents of the same OS user share one identity and the Gateway
    aggregates their mailboxes together (tssdk hit this with DSH/Pi/OpenClaw on one
    machine; Hermes hits it per profile).
    """
    global _agent_home_resolver, _cached
    _agent_home_resolver = fn
    _cached = None


def reset_for_tests() -> None:
    """Drop the process cache after changing env/resolver."""
    global _cached
    _cached = None


def get_agent_system_id(agent_home: str = "") -> str:
    """Process-cached AgentSystemId.

    Resolution order for the home: explicit argument → ``set_agent_home_resolver`` →
    ``AIMAIL_AGENT_HOME`` → the OS home. ``AIMAIL_AGENT_SYSTEM_ID`` overrides the whole
    derivation (fixture seam, same as tssdk).
    """
    override = str(os.environ.get(ENV_AGENT_SYSTEM_ID) or "").strip()
    if override:
        return override
    global _cached
    if _cached:
        return _cached
    home = str(agent_home or "").strip()
    if not home:
        try:
            home = str((_agent_home_resolver() if _agent_home_resolver else "") or "").strip()
        except Exception:  # noqa: BLE001 — a broken resolver must not break the host
            home = ""
    if not home:
        home = str(os.environ.get(ENV_AGENT_HOME) or "").strip()
    if not home:
        home = os.path.expanduser("~")
    _cached = derive_agent_system_id(home, hardware_fingerprint())
    return _cached

# -*- coding: utf-8 -*-
"""The ONE constructor for an AgentSystemId aggregate pull client (contract §8.1).

Why a single constructor: the TS client's defect B2 was exactly "a second entry built a
plain client and hit the per-address endpoint without the aggregation header", so the
aggregate stream never delivered while everything looked wired. Python reproduces the
same hazard if any module hand-builds a client, so aggregate callers use this function
and nothing else.

Endpoints (contract §6.1/§8.1):
    GET  /api/v1/agent-system/pending/stream
    POST /api/v1/agent-system/pending
    POST /api/v1/agent-system/pending/ack
Header: ``X-Agent-System-Id: <asid>`` on all three legs.
"""
from __future__ import annotations

from typing import Optional

from aimail_base import PULL_HTTP_TIMEOUT_S

#: Aggregate endpoint paths — identical to tssdk ``pull-contract.ts`` / ``system-pull.ts``.
AGENT_SYSTEM_STREAM_PATH = "/api/v1/agent-system/pending/stream"
AGENT_SYSTEM_PENDING_PATH = "/api/v1/agent-system/pending"
AGENT_SYSTEM_ACK_PATH = "/api/v1/agent-system/pending/ack"

#: Header the gateway authorizes the aggregate scope with.
AGENT_SYSTEM_HEADER = "X-Agent-System-Id"


def build_agent_system_client(gateway_url: str, api_key: str, agent_system_id: str,
                              identity: str = "", timeout: Optional[float] = None):
    """Aggregate pull client for one ``(AgentSystemId, Gateway)`` pair.

    ``identity`` is the signing identity (the representative address), NOT the
    AgentSystemId: the signature scheme signs with the address key, while the header
    tells the gateway which instance's aggregate scope is being read.
    """
    from aimail_tools import _GatewayClient

    asid = str(agent_system_id or "").strip()
    if not asid:
        raise ValueError("aggregate pull client requires agent_system_id")
    return _GatewayClient(
        gateway_url,
        api_key,
        timeout=int(timeout if timeout is not None else PULL_HTTP_TIMEOUT_S),
        identity=identity,
        stream_path=AGENT_SYSTEM_STREAM_PATH,
        pending_path=AGENT_SYSTEM_PENDING_PATH,
        ack_path=AGENT_SYSTEM_ACK_PATH,
        extra_headers={AGENT_SYSTEM_HEADER: asid},
    )


def is_aggregate_client(client) -> bool:
    """True when a client is wired for aggregation (used by tests and host asserts)."""
    paths = (getattr(client, "stream_path", ""), getattr(client, "pending_path", ""),
             getattr(client, "ack_path", ""))
    headers = getattr(client, "extra_headers", {}) or {}
    return (paths[0] == AGENT_SYSTEM_STREAM_PATH
            and paths[1] == AGENT_SYSTEM_PENDING_PATH
            and paths[2] == AGENT_SYSTEM_ACK_PATH
            and bool(headers.get(AGENT_SYSTEM_HEADER)))

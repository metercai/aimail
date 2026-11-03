"""aimail_board — Board query tools for A2A Board collaboration.

B1 (2026-11-03): the agent-facing board handle is the board's email
address. The internal 20-hex board_id is derived locally
(``sha256(lower(address))[:20]``, identical to the gateway's
``derive_board_id``) and never appears in tool arguments — the 20-hex id
still fills the gateway URL path (zero gateway change).
"""
from __future__ import annotations
import hashlib
import json
import logging
import re
from typing import Optional, Tuple

from aimail_tools import _GatewayClient
from aimail_base import _load_profile_config, _board_gateways, _board_creds_path


logger = logging.getLogger(__name__)
_TOOLSET = "agentmail"

# ═══════════════════════════════════════════════════════════════
# a2a_board toolset — board query tools for role prompts
# ═══════════════════════════════════════════════════════════════

def _board_id_of(board_address: str) -> str:
    """Board id from the board's email address — ``sha256(lower(addr))[:20]``,
    the same derivation the gateway uses (``derive_board_id``, board/models.rs).
    Pure and deterministic; the address is unique, memorable and stable, and
    the id is a pure function of it.
    """
    address = (board_address or "").strip().lower()
    if not address:
        return ""
    return hashlib.sha256(address.encode("utf-8")).hexdigest()[:20]


def _normalize_board(handle: str) -> Tuple[str, str]:
    """Validate an agent-supplied board handle → (board_id, error).

    Accepts a board address (``*.a2a@domain``). Non-address input (a 20-hex
    id, a ``t_``/``b_`` task id) is refused locally with an actionable error
    — previously it silently produced a wrong URL path (N4).
    """
    h = (handle or "").strip()
    if not h:
        return "", "board (the board's .a2a@ address) is required"
    if re.fullmatch(r"[0-9a-fA-F]{20}", h) or h.startswith("t_") or h.startswith("b_"):
        return "", (f"board must be the board's .a2a@ address (got \"{h}\"); "
                    "pass the address shown in the board invitation, not a board id or task id")
    if ".a2a@" not in h.lower():
        return "", f"board must be a board address like xk9mp2q.a2a@mail.example.com (got \"{h}\")"
    return _board_id_of(h), ""


def _resolve_gateway_url(board_id: str) -> str:
    """Return gateway URL for this board (registered from invite mail, else default)."""
    cfg = _load_profile_config()
    if not cfg or not board_id:
        return cfg.get("gateway_url", "") if cfg else ""
    gateway_url = _board_gateways.get(board_id, "")
    if not gateway_url:
        gateway_url = cfg.get("gateway_url", "")
    return gateway_url


def _get_board_token(board_id: str) -> Optional[str]:
    """Get board token from persisted creds file."""
    try:
        creds_path = _board_creds_path()
        if creds_path is not None and creds_path.exists():
            creds = json.loads(creds_path.read_text())
            return creds.get(board_id, {}).get("token")
    except Exception:
        pass
    return None


def _board_client(board_id: str, cfg: dict):
    """Board client construction: with board token → Bearer channel (api_key set
    to empty, no HMAC signature — board contract is Bearer+email, gateway
    board/handlers.rs); without token → api_key HMAC channel (system-level)."""
    gateway_url = _resolve_gateway_url(board_id)
    token = _get_board_token(board_id)
    if token:
        return _GatewayClient(gateway_url, ""), token
    return _GatewayClient(gateway_url, cfg.get("api_key", ""),
                          identity=cfg.get("email", "")), token


def _board_request(client, method, path, email, token: str = "", **kwargs):
    """Board API request.

    Board auth (gateway board/handlers.rs): Authorization: Bearer
    <member token> + member email must both match. When the caller has a
    board token it is sent as Bearer (client's HMAC signature headers are
    not part of the board contract). The member email rides the query
    string as the second credential.
    """
    import urllib.parse as _up
    sep = "&" if "?" in path else "?"
    path = f"{path}{sep}email={_up.quote(email)}"
    if token:
        kwargs.setdefault("headers", {})["Authorization"] = f"Bearer {token}"
    return client._request(method, path, **kwargs)


def _board_call(board: str, method: str, path: str, body: Optional[dict] = None) -> str:
    """Shared tool plumbing: profile → board handle → client → request."""
    cfg = _load_profile_config()
    if not cfg:
        return json.dumps({"error": "no profile config"})
    board_id, err = _normalize_board(board)
    if err:
        return json.dumps({"error": err})
    client, token = _board_client(board_id, cfg)
    email = cfg.get("email", "")
    try:
        r = _board_request(client, method, path.format(board_id=board_id), email,
                           token=token or "", body=body or {})
        return json.dumps(r, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def board_status(board: str) -> str:
    """Get Board status overview: pipeline distribution + dependencies + owners."""
    return _board_call(board, "GET", "/api/v1/board/{board_id}/status")


def board_task_list(board: str, status: str = "", assignee: str = "") -> str:
    """Filter task list by conditions. Supports status, assignee filtering."""
    import urllib.parse
    params = []
    if status:
        params.append(f"status={urllib.parse.quote(status)}")
    if assignee:
        params.append(f"assignee={urllib.parse.quote(assignee)}")
    query = ("?" + "&".join(params)) if params else ""
    return _board_call(board, "GET", f"/api/v1/board/{{board_id}}/tasks{query}")


def board_task_show(task_id: str, board: str) -> str:
    """Query one task's full details (body, status, assignee, reviewer, …)."""
    import urllib.parse
    tid = urllib.parse.quote(task_id or "")
    return _board_call(board, "GET", f"/api/v1/board/{{board_id}}/task/{tid}")


def board_heartbeat(task_id: str, board: str, note: str = "") -> str:
    """Send heartbeat to update task timestamp (keep a long task alive)."""
    import urllib.parse
    tid = urllib.parse.quote(task_id or "")
    return _board_call(board, "POST",
                       f"/api/v1/board/{{board_id}}/task/{tid}/heartbeat?actor=toolset",
                       body={"note": note})


def board_members(board: str, email: str = "") -> str:
    """List Board members. Optional filter by email."""
    import urllib.parse
    query = f"?email={urllib.parse.quote(email)}" if email else ""
    return _board_call(board, "GET", f"/api/v1/board/{{board_id}}/members{query}")


def board_roles(board: str, role: str = "") -> str:
    """Get Board role permission table. Optional filter by role."""
    import urllib.parse
    query = f"?role={urllib.parse.quote(role)}" if role else ""
    return _board_call(board, "GET", f"/api/v1/board/{{board_id}}/roles{query}")

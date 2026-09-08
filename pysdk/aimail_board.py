"""aimail_board — Board query tools for A2A Board collaboration."""
from __future__ import annotations
import json
import logging
from typing import Optional

from aimail_tools import _GatewayClient
from aimail_base import _load_profile_config, _board_gateways, _board_creds_path


logger = logging.getLogger(__name__)
_TOOLSET = "agentmail"


# ═══════════════════════════════════════════════════════════════
# a2a_board toolset — board query tools for role prompts
# ═══════════════════════════════════════════════════════════════

def _resolve_board(task_id: str) -> str:
    """Extract board_id from task_id."""
    if task_id.startswith("t_"):
        parts = task_id.split("_", 2)
        if len(parts) >= 2:
            return parts[1]
    if task_id.startswith("board:"):
        parts = task_id.split(":", 2)
        if len(parts) >= 2:
            return parts[1]
    return ""

def _resolve_gateway_url(task_id: str) -> str:
    """Return gateway URL for the board of this task."""
    board_id = _resolve_board(task_id)
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
        import json as _json
        creds_path = _board_creds_path()
        if creds_path.exists():
            creds = _json.loads(creds_path.read_text())
            return creds.get(board_id, {}).get("token")
    except Exception:
        pass
    return None


def _board_client(board_key: str, cfg: dict):
    """Board 客户端构造:有 board token → Bearer 通道(api_key 置空,不发
    HMAC 签名——board 契约是 Bearer+email,网关 board/handlers.rs);
    无 token → api_key HMAC 通道(兼容系统级调用)。"""
    board_id = _resolve_board(board_key) if not board_key.startswith("b_") else board_key
    gateway_url = _resolve_gateway_url(board_key)
    token = _get_board_token(board_id) if board_id else None
    if token:
        return _GatewayClient(gateway_url, ""), token
    return _GatewayClient(gateway_url, cfg.get("api_key", "")), token


def board_task_show(task_id: str) -> str:
    """查询任务详情。返回 task 的所有字段（body、status、assignee、reviewer 等）。"""
    cfg = _load_profile_config()
    if not cfg:
        return "{\"error\": \"no profile config\"}"
    board_id = _resolve_board(task_id)
    if not board_id:
        return "{\"error\": \"cannot resolve board_id from task_id\"}"
    client, token = _board_client(task_id, cfg)
    _email = cfg.get("email", "")
    try:
        r = _board_request(client, "GET", f"/api/v1/board/{board_id}/task/{task_id}", _email, token=token)
        return json.dumps(r, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def board_task_list(board: str, status: str = "", assignee: str = "") -> str:
    """按条件过滤 task 列表。支持 status、assignee 过滤。常用于巡视。"""
    import urllib.parse
    cfg = _load_profile_config()
    if not cfg:
        return "{\"error\": \"no profile config\"}"
    board_id = _resolve_board(board) if not board.startswith("b_") else board
    client, token = _board_client(board_id, cfg)
    _email = cfg.get("email", "")
    params = {}
    if status:
        params["status"] = status
    if assignee:
        params["assignee"] = assignee
    query = "&".join(f"{k}={urllib.parse.quote(v)}" for k, v in params.items())
    path = f"/api/v1/board/{board_id}/tasks" + (f"?{query}" if query else "")
    try:
        r = _board_request(client, "GET", path, _email, token=token)
        return json.dumps(r, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})



def board_members(board_id: str, email: str = "") -> str:
    """列出 Board 成员。可选按 email 过滤。"""
    import json, urllib.parse
    cfg = _load_profile_config()
    if not cfg:
        return json.dumps({"error": "no profile config"})
    client, token = _board_client(board_id, cfg)
    _email = cfg.get("email", "")
    try:
        path = f"/api/v1/board/{board_id}/members"
        if email:
            path += f"?email={urllib.parse.quote(email)}"
        return json.dumps(_board_request(client, "GET", path, _email, token=token), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})

def board_roles(board_id: str, role: str = "") -> str:
    """获取 Board 角色权限表。可选按 role 过滤返回该角色的成员和权限。"""
    import json, urllib.parse
    cfg = _load_profile_config()
    if not cfg:
        return json.dumps({"error": "no profile config"})
    client, token = _board_client(board_id, cfg)
    _email = cfg.get("email", "")
    try:
        path = f"/api/v1/board/{board_id}/roles"
        if role:
            path += f"?role={urllib.parse.quote(role)}"
        return json.dumps(_board_request(client, "GET", path, _email, token=token), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})

def board_status(board_id: str) -> str:
    """获取 Board 状态总览：管线分布 + 依赖关系 + 负责人。"""
    cfg = _load_profile_config()
    if not cfg: return json.dumps({"error": "no profile config"})
    client, token = _board_client(board_id, cfg)
    _email = cfg.get("email", "")
    try:
        return json.dumps(_board_request(client, "GET", f"/api/v1/board/{board_id}/status", _email, token=token), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})

def board_heartbeat(task_id: str, note: str = "") -> str:
    """发心跳更新任务时间戳。长任务期间定期调用，让Board/Orchestrator知道任务仍在进行。"""
    cfg = _load_profile_config()
    if not cfg:
        return "{\"error\": \"no profile config\"}"
    board_id = _resolve_board(task_id)
    if not board_id:
        return "{\"error\": \"cannot resolve board_id from task_id\"}"
    client, token = _board_client(task_id, cfg)
    _email = cfg.get("email", "")
    try:
        r = _board_request(client, "POST", f"/api/v1/board/{board_id}/task/{task_id}/heartbeat?actor=toolset", _email, token=token,
                            body={"note": note})
        return json.dumps(r, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


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

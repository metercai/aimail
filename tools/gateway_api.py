#!/usr/bin/env python3
"""Gateway API client + config helpers — shared by setup_system.py, deploy_bridge.py."""
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# Config paths
# ═══════════════════════════════════════════════════════════════

def gateway_config_path(system_id: str = "") -> Path:
    """Return path to agentmail_gateway.json for a system_id."""
    from aimail_base import aimail_home  # lazy import (avoid module-load cycle)
    base = aimail_home() / "systems"
    return (base / system_id if system_id else base) / "agentmail_gateway.json"


def load_gateway_config(system_id: str = "") -> Optional[dict]:
    """Load gateway connection config from ~/.agentmail/systems/{sid}/agentmail_gateway.json.

    Canonical name: agentmail_gateway.json (scripts/ write this).
    """
    path = gateway_config_path(system_id)
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return None


# ═══════════════════════════════════════════════════════════════
# API client
# ═══════════════════════════════════════════════════════════════

class GatewayClient:
    """Thin HTTP client for agentmail gateway API."""

    def __init__(self, gateway_url: str, api_key: str, timeout: int = 30):
        self.gateway_url = gateway_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.gateway_url}{path}", data=data,
            headers={"Content-Type": "application/json"},
        )
        if self.api_key:
            req.add_header("X-Api-Key", self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except Exception:
                return {"error": f"HTTP {e.code}", "status": e.code}
        except Exception as e:
            return {"error": str(e)}

    def activate_system(self, code: str, system_name: str = "",
                        domain: str = "") -> dict:
        """POST /api/v1/activate-system."""
        body = {"code": code}
        if system_name:
            body["system_name"] = system_name
        if domain:
            body["domain"] = domain
        return self._post("/api/v1/activate-system", body)

    def register_email(self, system_id: str, email: str,
                       webhook_url: str = "", webhook_secret: str = "",
                       manager_address: str = "", generate_code: bool = True) -> dict:
        """POST /api/v1/admin/systems/:sid/addresses."""
        body = {"email": email, "generate_code": generate_code}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if webhook_secret:
            body["webhook_secret"] = webhook_secret
        if manager_address:
            body["manager_address"] = manager_address
        return self._post(f"/api/v1/admin/systems/{system_id}/addresses", body)


# ═══════════════════════════════════════════════════════════════
# Standalone API functions (no client instance needed)
# ═══════════════════════════════════════════════════════════════

def whoami(gw: str, ak: str) -> dict:
    """GET /api/v1/whoami — return API key metadata."""
    req = urllib.request.Request(f"{gw}/api/v1/whoami",
        headers={"X-Api-Key": ak})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def create_api_key(gw: str, ak: str, system_id: str, email: str,
                   scopes: list, category: str) -> dict:
    """POST /api/v1/admin/api-keys. Returns {raw_key, error, detail, status}."""
    data = json.dumps({
        "system_id": system_id, "email_address": email,
        "scopes": scopes, "category": category,
    }).encode()
    req = urllib.request.Request(f"{gw}/api/v1/admin/api-keys", data=data,
        headers={"X-Api-Key": ak, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            return {"raw_key": "", "error": body.get("error", ""),
                    "detail": body.get("detail", ""), "status": e.code}
        except Exception:
            return {"raw_key": "", "error": f"HTTP {e.code}", "status": e.code}
    except Exception as e:
        return {"raw_key": "", "error": str(e)}

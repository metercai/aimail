#!/usr/bin/env python3
"""Gateway API client + config helpers — shared by setup_system.py, deploy_bridge.py."""
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from aimail_base import compute_api_signature  # v1 signature (single source)


# ═══════════════════════════════════════════════════════════════
# Config paths
# ═══════════════════════════════════════════════════════════════

def gateway_config_path(system_id: str = "") -> Path:
    """Return path to aimail_gateway.json for a system_id."""
    from aimail_base import aimail_home  # lazy import (avoid module-load cycle)
    base = aimail_home() / "systems"
    return (base / system_id if system_id else base) / "aimail_gateway.json"


LEGACY_CONFIG_NAME = "agentmail_gateway.json"  # pre-2026-09-04 name


def _migrate_legacy_config(path: Path) -> None:
    """Rename agentmail_gateway.json → aimail_gateway.json once (best effort)."""
    legacy = path.parent / LEGACY_CONFIG_NAME
    if legacy.is_file() and not path.is_file():
        try:
            legacy.rename(path)
            import os as _os
            _os.chmod(path, 0o600)
        except Exception:
            pass


def load_gateway_config(system_id: str = "") -> Optional[dict]:
    """Load gateway connection config from ~/.aimail/systems/{sid}/aimail_gateway.json.

    Canonical name: aimail_gateway.json (2026-09-04 rename, aligned with the
    gateway binary name). Legacy agentmail_gateway.json is auto-migrated on
    first read.
    """
    path = gateway_config_path(system_id)
    _migrate_legacy_config(path)
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
    """Thin HTTP client for aimail gateway API.

    v1 signature auth (docs/API-SIGNATURE-PROTOCOL.md): the raw API key never
    crosses the wire; each request carries X-Api-Identity + X-Api-Timestamp +
    X-Api-Signature. ``identity`` is the key's email (address-scoped) or its
    system_id (system-level keys, empty domain_addr).
    """

    def __init__(self, gateway_url: str, api_key: str, timeout: int = 30,
                 identity: str = ""):
        self.gateway_url = gateway_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.identity = identity

    def _auth_headers(self, method: str, path: str,
                      data: bytes) -> dict:
        h = {}
        if not self.api_key:
            return h
        if self.identity:
            h["X-Api-Identity"] = self.identity
        sig = compute_api_signature(self.api_key, method, path, data)
        if sig:
            h.update(sig)
        return h

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.gateway_url}{path}", data=data,
            headers={"Content-Type": "application/json"},
        )
        for k, v in self._auth_headers("POST", path, data).items():
            req.add_header(k, v)
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
        """POST /api/v1/activate-system (public — no auth)."""
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

def whoami(gw: str, ak: str, identity: str = "") -> dict:
    """GET /api/v1/whoami — return API key metadata."""
    path = "/api/v1/whoami"
    headers = {}
    if identity:
        headers["X-Api-Identity"] = identity
    headers.update(compute_api_signature(ak, "GET", path, b"") or {})
    req = urllib.request.Request(f"{gw}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def create_api_key(gw: str, ak: str, system_id: str, email: str,
                   scopes: list, category: str) -> dict:
    """POST /api/v1/admin/api-keys. Returns {raw_key, error, detail, status}.

    ``system_id`` doubles as the caller's identity: system-level keys
    (admin/system_admin) have an empty domain_addr, so their identity IS the
    system_id.
    """
    data = json.dumps({
        "system_id": system_id, "email_address": email,
        "scopes": scopes, "category": category,
    }).encode()
    path = "/api/v1/admin/api-keys"
    req = urllib.request.Request(f"{gw}{path}", data=data,
        headers={"Content-Type": "application/json",
                 "X-Api-Identity": system_id,
                 **(compute_api_signature(ak, "POST", path, data) or {})})
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

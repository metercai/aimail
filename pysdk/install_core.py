#!/usr/bin/env python3
"""install_core — system-level install/activation core (pysdk side).

Architecture (2026-09 user ruling): install implementation lives in the SDKs —
pysdk (Python platforms: hermes/deer-flow) and tssdk (TS platforms: dsh/pi/
openclaw) each carry a PARITY implementation of the same core (same protocol,
same semantics, same on-disk layout under ~/.aimail/systems/{sid}/). The CLI is
a thin dispatcher over these SDK cores, never the owner of install logic.

Function surface mirrors tssdk/packages/mail-core/src/install.ts 1:1:
    install_system          ↔ installSystem          (dual path A/B + reset)
    save_system_config      ↔ saveSystemConfig
    create_agent_admin_key  ↔ createAgentAdminKey    (agent_admin downgrade)
    detect_system_for_home  ↔ detectSystemForHome    (unique-owner reuse)
    activate: GatewayClient.activate_system (public POST /api/v1/activate-system)

Known divergence: detect_webhook_host (public webhook callback reachability) is
Python-host only (hermes/deer-flow deliver via gateway->webhook); TS hosts
receive via the local bridge route and do not need it.

History: migrated verbatim from cli/setup_system.py (2026-09) so the CLI keeps
its env-contract spawn wrapper while the logic itself ships inside the SDK.
"""
from __future__ import annotations

import json
import logging
import os
import socket
from pathlib import Path

# Flat core layout: this file sits next to gateway_api.py / aimail_base.py
# (repo pysdk/ or site-packages/aimail/) — sibling imports only.
from aimail_base import aimail_home  # noqa: E402
from gateway_api import (  # noqa: E402
    GatewayClient,
    create_api_key,
    gateway_config_path,
    load_gateway_config,
)

logger = logging.getLogger("aimail_install")


# ═══════════════════════════════════════════════════════════════
# Webhook host auto-detection (Python hosts only)
# ═══════════════════════════════════════════════════════════════

def detect_webhook_host(gateway_url: str) -> str:
    """Determine the reachable host for gateway → host webhook callbacks.

    Compares ``gateway_url``'s host against local interfaces to choose the
    correct callback address:

    - Same machine (loopback or own IP) → ``127.0.0.1``
    - Same LAN (private IP, different host) → our LAN IP
    - Remote (public IP) → our external IP or LAN fallback

    Returns the best host string.  Failing everything, returns ``127.0.0.1``.
    """
    from urllib.parse import urlparse
    try:
        gateway_host = urlparse(gateway_url).hostname or ""
    except Exception:
        gateway_host = ""

    if not gateway_host:
        return "127.0.0.1"

    # ── Detect our primary LAN IP ────────────────────────────
    lan_ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except OSError:
        pass

    if not lan_ip:
        try:
            import subprocess as _sp
            out = _sp.check_output(
                ["ip", "-4", "-brief", "addr", "show", "scope", "global"],
                text=True, timeout=3,
            )
            for line in out.splitlines():
                parts = line.strip().split()
                for p in parts:
                    if "/" in p and p[0].isdigit():
                        ip = p.split("/")[0]
                        if not ip.startswith("127."):
                            lan_ip = ip
                            break
                if lan_ip:
                    break
        except Exception:
            pass

    import ipaddress as _ipaddr

    def _is_loopback(host: str) -> bool:
        return host in ("127.0.0.1", "localhost", "::1", "ip6-localhost")

    def _is_private(host: str) -> bool:
        try:
            return _ipaddr.ip_address(host).is_private
        except ValueError:
            return False

    if _is_loopback(gateway_host):
        return "127.0.0.1"

    if lan_ip and gateway_host == lan_ip:
        return lan_ip

    if _is_private(gateway_host):
        if lan_ip:
            return lan_ip
        return "127.0.0.1"

    # ── Hostname (not IP): try DNS resolution ──
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as _dns:
            resolved = _dns.submit(
                socket.gethostbyname, gateway_host
            ).result(timeout=5)
        if _is_loopback(resolved):
            return "127.0.0.1"
        if lan_ip and resolved == lan_ip:
            return lan_ip
        if _is_private(resolved):
            return lan_ip if lan_ip else "127.0.0.1"
    except Exception:
        pass

    # ── Public IP: try external detection ──
    try:
        import urllib.request as _ur
        req = _ur.Request(
            "https://ifconfig.me", headers={"User-Agent": "curl/7.0"}
        )
        with _ur.urlopen(req, timeout=5) as resp:
            external_ip = resp.read().decode().strip()
            if external_ip and not _is_private(external_ip):
                logger.info(
                    "Detected external IP %s for webhook callback "
                    "(gateway at %s is public)", external_ip, gateway_host
                )
                return external_ip
    except Exception:
        pass

    if lan_ip:
        logger.warning(
            "Gateway at %s is public but cannot detect external IP. "
            "Using LAN IP %s — gateway must be able to reach this address. "
            "Set AIMAIL_WEBHOOK_HOST to override.", gateway_host, lan_ip
        )
        return lan_ip

    return "127.0.0.1"


# ═══════════════════════════════════════════════════════════════
# Gateway config persistence
# ═══════════════════════════════════════════════════════════════

def save_system_config(
    gateway_url: str,
    admin_key: str,
    system_id: str,
    domain: str = "",
    system_name: str = "",
    save_raw_snapshots: bool = True,
    manager_address: str = "",
    webhook_host: str = "",
    system_home: str = "",
) -> Path:
    """Persist gateway connection config to ~/.aimail/systems/{sid}/aimail_gateway.json.

    system_home = 系统/平台根(hermes=~/.hermes, openclaw=~/.openclaw),
    用于平台反查与 install 复用归属判定。
    File is chmod 0600 (holds admin_key). Mirrors tssdk saveSystemConfig.
    """
    cfg = {
        "gateway_url": gateway_url,
        "admin_key": admin_key,
        "system_id": system_id,
        "system_name": system_name,
        "save_raw_snapshots": save_raw_snapshots,
    }
    if domain:
        cfg["domain"] = domain
    if manager_address:
        cfg["manager_address"] = manager_address
    if webhook_host:
        cfg["webhook_host"] = webhook_host
    if system_home:
        cfg["system_home"] = system_home

    gateway_path = gateway_config_path(system_id)
    gateway_path.parent.mkdir(parents=True, exist_ok=True)
    with open(gateway_path, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(gateway_path, 0o600)  # contains admin_key — user-only
    return gateway_path


# ═══════════════════════════════════════════════════════════════
# Agent-admin key downgrade
# ═══════════════════════════════════════════════════════════════

def create_agent_admin_key(
    gateway_url: str, system_admin_key: str, system_id: str,
    manager_address: str,
) -> str:
    """Create agent_admin key and replace admin_key in gateway config.

    Returns the agent_admin key on success, or the original system key when
    creation failed (system key stays usable). Mirrors tssdk
    createAgentAdminKey.
    """
    result = create_api_key(
        gateway_url, system_admin_key, system_id,
        manager_address, ["agent_admin"], "agent_admin",
    )
    raw = result.get("raw_key", "")
    if not raw:
        logger.warning(
            "Failed to create agent_admin key: %s %s — keeping system key",
            result.get("error", ""), result.get("detail", ""),
        )
        return system_admin_key

    # Replace in config file
    cfg_path = gateway_config_path(system_id)
    if cfg_path.is_file():
        with open(cfg_path) as f:
            cfg = json.load(f)
        cfg["admin_key"] = raw
        with open(cfg_path, "w") as f:
            json.dump(cfg, f, indent=2)
        os.chmod(cfg_path, 0o600)
    logger.info("agent_admin key created and saved")
    return raw


# ═══════════════════════════════════════════════════════════════
# Install orchestration — dual path (A: admin_key reset / B: product code)
# ═══════════════════════════════════════════════════════════════

def install_system(
    gateway_url: str,
    system_id: str = "",
    admin_key: str = "",
    product_code: str = "",
    system_name: str = "",
    domain: str = "",
    save_raw_snapshots: bool = True,
    manager_address: str = "",
    webhook_host: str = "",
    webhook_base_url: str = "",
    webhook_secret: str = "",
    system_home: str = "",
) -> dict:
    """Unified system install entry — parity with tssdk installSystem.

    Provide gateway_url + system_id + ONE of (admin_key, product_code).

    Path A (admin_key): reset semantics — empty params inherit existing
    business fields from the current config; only core connection params
    (gateway_url/admin_key/system_id) are rewritten; prev-only fields
    (default_agent_name/bridge_port/mode/…) are preserved back.
    Path B (product_code): server activation → config write → downgrade.
    """
    if not gateway_url:
        return {"success": False, "error": "gateway_url is required"}

    if not webhook_host:
        webhook_host = os.environ.get("AIMAIL_WEBHOOK_HOST", "")
    if not webhook_host:
        # reset 场景(admin_key 路径 + 已有配置):跳过探测,继承已有值
        # (实测:探测出 IPv6 地址会覆盖 NAT 公网 webhook_host)
        if admin_key:
            try:
                _p = gateway_config_path(system_id)
                if _p.is_file():
                    webhook_host = json.loads(_p.read_text()).get("webhook_host", "")
            except Exception:
                pass
    if not webhook_host:
        webhook_host = detect_webhook_host(gateway_url)

    # ── Path A: admin_key provided (already-activated system) ──
    if admin_key:
        if not system_id:
            return {"success": False, "error": "system_id is required for admin_key path"}
        # reset 语义:已有配置存在时,空参数继承已有值——只重写核心连接参数,
        # 不覆盖业务字段(domain/bridge_port/mode/save_raw_snapshots/webhook_host)
        prev = {}
        try:
            p = gateway_config_path(system_id)
            if p.is_file():
                prev = json.loads(p.read_text())
        except Exception:
            pass
        save_system_config(
            gateway_url=gateway_url, admin_key=admin_key, system_id=system_id,
            domain=domain or prev.get("domain", "admin.local"),
            system_name=system_name or prev.get("system_name", ""),
            save_raw_snapshots=save_raw_snapshots if save_raw_snapshots or "save_raw_snapshots" not in prev else prev.get("save_raw_snapshots", False),
            manager_address=manager_address or prev.get("manager_address", ""),
            webhook_host=webhook_host or prev.get("webhook_host", ""),
            system_home=system_home or prev.get("system_home", ""),
        )
        # save_system_config 只写核心字段——把 prev 中未覆盖的业务字段全补回
        # (通用保护:default_agent_name/bridge_port/mode/任意未来新增字段)
        _p = gateway_config_path(system_id)
        try:
            if _p.is_file():
                _cfg = json.loads(_p.read_text())
                _written = {"gateway_url", "admin_key", "system_id", "system_name",
                            "save_raw_snapshots", "domain", "manager_address",
                            "webhook_host", "system_home"}
                for _k, _v in prev.items():
                    if _k not in _cfg and _k not in _written:
                        _cfg[_k] = _v
                _p.write_text(json.dumps(_cfg, indent=2, ensure_ascii=False))
        except Exception:
            pass
        agent_key = create_agent_admin_key(
            gateway_url, admin_key, system_id, manager_address,
        )
        return {"success": True, "system_id": system_id, "path": "admin_key",
                "admin_key": agent_key}

    # ── Path B: product_code provided (new system activation) ──
    if product_code:
        return _activate_system(
            gateway_url=gateway_url, product_code=product_code,
            system_id=system_id, system_name=system_name, domain=domain,
            save_raw_snapshots=save_raw_snapshots,
            manager_address=manager_address, webhook_host=webhook_host,
            system_home=system_home,
        )

    return {"success": False, "error": "Either admin_key or product_code is required"}


def _activate_system(
    gateway_url: str,
    product_code: str,
    system_id: str,
    system_name: str,
    domain: str,
    save_raw_snapshots: bool,
    manager_address: str,
    webhook_host: str,
    system_home: str,
) -> dict:
    """Activate a product code on the server, create the system, save config.

    Server success = {"status": "activated", "raw_key": ...}. Note the server
    response has NO ``success`` field (result.get("success") → None must not be
    misread as failure — activation could succeed while raw_key is missing,
    observed 2026-08-18 DeerFlow).
    """
    if not product_code:
        return {"success": False, "error": "product_code is required"}

    client = GatewayClient(gateway_url, "")
    result = client.activate_system(
        code=product_code,
        system_name=system_name,
        domain=domain,
    )

    status = result.get("status", 0)
    is_ok = (
        result.get("success") in (True, "true", "ok")
        or str(status).lower() in ("activated", "200", "201")
        or bool(result.get("raw_key"))
    )
    if not is_ok:
        return {"success": False,
                "error": result.get("error", f"Activation failed (HTTP {status})"),
                "status": status}

    admin_key = result.get("raw_key", "")
    created_system_id = result.get("system_id", system_id)
    created_domain = result.get("domain", domain)

    if not admin_key:
        return {"success": False, "error": "No admin_key returned from server",
                "status": status}

    save_system_config(
        gateway_url=gateway_url,
        admin_key=admin_key,
        system_id=created_system_id,
        domain=created_domain,
        system_name=system_name or result.get("system_name", ""),
        save_raw_snapshots=save_raw_snapshots,
        manager_address=manager_address,
        webhook_host=webhook_host,
        system_home=system_home,
    )
    logger.info("Gateway config saved to %s", gateway_config_path(created_system_id))
    # Downgrade to agent_admin key
    agent_key = create_agent_admin_key(
        gateway_url, admin_key, created_system_id, manager_address,
    )
    return {
        "success": True,
        "system_id": created_system_id,
        "admin_key": agent_key,
        "gateway_url": gateway_url,
        "domain": created_domain,
        "system_name": system_name or result.get("system_name", ""),
        "path": "activation",
    }


# ═══════════════════════════════════════════════════════════════
# Reuse detection (unique home ownership)
# ═══════════════════════════════════════════════════════════════

def _norm_home(home: str) -> str:
    return str(Path(home).expanduser()).rstrip("/") if home else ""


def detect_system_for_home(system_home: str) -> str:
    """system_home → owning system id: scan systems/*/ configs; a UNIQUE match
    returns that sid; zero or multiple → '' (never guess). Mirrors tssdk
    detectSystemForHome and the former CLI runtime_core.sid_from_system_home.
    """
    if not system_home:
        return ""
    base = aimail_home() / "systems"
    target = _norm_home(system_home)
    found = ""
    if not base.is_dir() or not target:
        return ""
    for d in sorted(base.iterdir()):
        if not (d.is_dir() and (d / "aimail_gateway.json").is_file()):
            continue
        try:
            cfg = json.loads((d / "aimail_gateway.json").read_text())
        except Exception:
            continue
        if _norm_home(cfg.get("system_home", "")) == target:
            if found:  # second owner → ambiguous, don't guess
                return ""
            found = d.name
    return found

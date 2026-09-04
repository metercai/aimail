#!/usr/bin/env python3
"""
setup_system.py — system activation/configuration (admin-key reuse or product-code)

Called by aimail install/reset with the INTEGRATE_* env contract, or imported
as `from setup_system import setup`. Outputs JSON (with system_id) on success;
prefixes fatal errors with __ERROR__.

Depends on runtime core (pysdk/): gateway_api (GatewayClient, create_api_key,
gateway_config_path, load_gateway_config) via runtime_core.load_core().
"""
import json
import logging
import os
import socket
import sys
from pathlib import Path

# Ensure scripts/ dir is on path for gateway_api and local imports
_script_dir = str(Path(__file__).resolve().parent)
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)
# 运行时核心(repo pysdk/ 优先 > pip aimail 兜底)
from runtime_core import load_core  # noqa: E402
load_core()

from gateway_api import GatewayClient, create_api_key, gateway_config_path, load_gateway_config

logger = logging.getLogger("amail_setup")


# ── Agent admin key helper ──────────────────────────────────────

def _downgrade_to_agent_admin_key(
    gateway_url: str, system_admin_key: str, system_id: str,
    manager_address: str,
) -> str:
    """Create agent_admin key and replace admin_key in gateway config.
    Returns the agent_admin key on success, or the original key on failure.
    """
    result = create_api_key(
        gateway_url, system_admin_key, system_id,
        manager_address, ["agent_admin"], "agent_admin",
    )
    raw = result.get("raw_key", "")
    if not raw:
        logger.warning(
            "[amail_setup] Failed to create agent_admin key: %s %s — keeping system key",
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
    logger.info("[amail_setup] agent_admin key created and saved")
    return raw


# ═══════════════════════════════════════════════════════════════
# Webhook host auto-detection
# ═══════════════════════════════════════════════════════════════

def _detect_webhook_host(gateway_url: str) -> str:
    """Determine the reachable host for gateway → Hermes webhook callbacks.

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
                    "[amail_setup] Detected external IP %s for webhook callback "
                    "(gateway at %s is public)", external_ip, gateway_host
                )
                return external_ip
    except Exception:
        pass

    if lan_ip:
        logger.warning(
            "[amail_setup] Gateway at %s is public but cannot detect external IP. "
            "Using LAN IP %s — gateway must be able to reach this address. "
            "Set AIMAIL_WEBHOOK_HOST to override.", gateway_host, lan_ip
        )
        return lan_ip

    return "127.0.0.1"


# ═══════════════════════════════════════════════════════════════
# Gateway config persistence
# ═══════════════════════════════════════════════════════════════

def _save_gateway_config(
    gateway_url: str,
    admin_key: str,
    system_id: str,
    domain: str = "",
    system_name: str = "",
    save_raw_snapshots: bool = False,
    manager_address: str = "",
    webhook_host: str = "",
    system_home: str = "",
) -> None:
    """Save amail gateway connection config to standalone JSON file.

    Writes to ~/.aimail/systems/{system_id}/aimail_gateway.json.
    system_home = 系统/平台根(hermes=~/.hermes, openclaw=~/.openclaw),
    用于 CLI 平台反查(2026-08-16 用户定调;与 agent_home=具体 agent home
    语义区分)。
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


# ═══════════════════════════════════════════════════════════════
# System initialization (product code path)
# ═══════════════════════════════════════════════════════════════

def init_system(
    product_code: str,
    system_id: str,
    system_name: str,
    domain: str = "",
    gateway_url: str = "",
    save_raw_snapshots: bool = False,
    manager_address: str = "",
    webhook_host: str = "",
    system_home: str = "",
) -> dict:
    """Initialize a system using a product activation code.

    Takes a pre-generated product activation code, activates it on
    the server, creates the system + default domain + quotas, and returns a
    system_admin API key.
    """
    if not gateway_url:
        cfg = load_gateway_config()
        gateway_url = cfg.get("gateway_url", "") if cfg else ""
    if not gateway_url:
        return {"success": False, "error": "gateway_url is required"}
    if not product_code:
        return {"success": False, "error": "product_code is required"}

    client = GatewayClient(gateway_url, "")
    result = client.activate_system(
        code=product_code,
        system_name=system_name or None,
        domain=domain or None,
    )

    status = result.get("status", 0)
    # 成功判定:网关激活成功响应为 {"status":"activated","raw_key":...},
    # 无 success 字段(result.get("success") → None 会被误判失败——
    # 2026-08-18 实测 DeerFlow 激活时踩中:系统已建但 raw_key 丢失)。
    # 失败响应带 error/非 200 status。
    is_ok = (
        result.get("success") in (True, "true", "ok")
        or str(status).lower() in ("activated", "200", "201")
        or bool(result.get("raw_key"))
    )
    if not is_ok:
        return {"success": False, "error": result.get("error", f"Activation failed (HTTP {status})"), "status": status}

    admin_key = result.get("raw_key", "")
    created_system_id = result.get("system_id", system_id)
    created_domain = result.get("domain", domain)

    if not admin_key:
        return {"success": False, "error": "No admin_key returned from server", "status": status}

    _save_gateway_config(
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
    logger.info("[amail_setup] Gateway config saved to %s", gateway_config_path())
    # Downgrade to agent_admin key
    agent_key = _downgrade_to_agent_admin_key(
        gateway_url, admin_key, created_system_id, manager_address,
    )
    return {
        "success": True,
        "system_id": created_system_id,
        "admin_key": agent_key,
        "gateway_url": gateway_url,
        "domain": created_domain,
        "system_name": system_name or result.get("system_name", ""),
    }


# ═══════════════════════════════════════════════════════════════
# Unified setup entry point
# ═══════════════════════════════════════════════════════════════

def setup(
    gateway_url: str,
    system_id: str,
    admin_key: str = "",
    product_code: str = "",
    system_name: str = "",
    domain: str = "",
    save_raw_snapshots: bool = False,
    manager_address: str = "",
    webhook_host: str = "",
    webhook_base_url: str = "",
    webhook_secret: str = "",
    system_home: str = "",
) -> dict:
    """Unified integration entry point.

    Provide gateway_url + system_id + ONE of (admin_key, product_code).
    Auto-detects the path and saves config.
    """
    if not gateway_url:
        return {"success": False, "error": "gateway_url is required"}

    if not webhook_host:
        webhook_host = os.environ.get("AIMAIL_WEBHOOK_HOST", "")
    if not webhook_host:
        # reset 场景(admin_key 路径 + 已有配置):跳过探测,继承已有值
        # (实测 2026-08-16:探测出 IPv6 地址覆盖了 NAT 公网 webhook_host)
        if admin_key:
            try:
                _p = gateway_config_path(system_id)
                if _p.is_file():
                    webhook_host = json.loads(_p.read_text()).get("webhook_host", "")
            except Exception:
                pass
    if not webhook_host:
        webhook_host = _detect_webhook_host(gateway_url)

    # Path A: admin_key provided (already-activated system)
    if admin_key:
        if not system_id:
            return {"success": False, "error": "system_id is required for admin_key path"}
        # reset 语义(2026-08-16 用户定调):已有配置存在时,空参数继承
        # 已有值——只重写核心连接参数(gateway_url/admin_key/system_id),
        # 不覆盖业务字段(domain/bridge_port/mode/save_raw_snapshots/
        # webhook_host)。实测 bug:此前空参数用默认值(admin.local/False/
        # 探测 webhook_host)破坏了 reset。
        prev = {}
        try:
            p = gateway_config_path(system_id)
            if p.is_file():
                prev = json.loads(p.read_text())
        except Exception:
            pass
        _save_gateway_config(
            gateway_url=gateway_url, admin_key=admin_key, system_id=system_id,
            domain=domain or prev.get("domain", "admin.local"),
            system_name=system_name or prev.get("system_name", ""),
            save_raw_snapshots=save_raw_snapshots if save_raw_snapshots or "save_raw_snapshots" not in prev else prev.get("save_raw_snapshots", False),
            manager_address=manager_address or prev.get("manager_address", ""),
            webhook_host=webhook_host or prev.get("webhook_host", ""),
            system_home=system_home or prev.get("system_home", ""),
        )
        # _save_gateway_config 只写核心字段——reset 时把 prev 中未覆盖的
        # 业务字段全补回(通用保护:default_agent_name/bridge_port/mode/
        # 任意未来新增字段,2026-08-16 实测 default_agent_name 曾丢)
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
        agent_key = _downgrade_to_agent_admin_key(
            gateway_url, admin_key, system_id, manager_address,
        )
        return {"success": True, "system_id": system_id, "path": "admin_key", "admin_key": agent_key}

    # Path B: product_code provided (new system activation)
    if product_code:
        result = init_system(
            product_code=product_code, system_id=system_id, system_name=system_name,
            domain=domain, gateway_url=gateway_url,
            save_raw_snapshots=save_raw_snapshots, manager_address=manager_address,
            webhook_host=webhook_host, system_home=system_home,
        )
        if result.get("success"):
            result["path"] = "activation"
        return result

    return {"success": False, "error": "Either admin_key or product_code is required"}


# ═══════════════════════════════════════════════════════════════
# CLI entry point — called by integrate.sh Step 4
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    kwargs = dict(
        gateway_url=os.environ.get("INTEGRATE_GATEWAY_URL", ""),
        system_id=os.environ.get("INTEGRATE_SYSTEM_ID", ""),
        domain=os.environ.get("INTEGRATE_AIMAIL_DOMAIN", "") or "",
        save_raw_snapshots=os.environ.get("INTEGRATE_SAVE_SNAPSHOTS", "false") == "true",
        manager_address=os.environ.get("INTEGRATE_MANAGER_ADDRESS", "") or "",
        webhook_host=os.environ.get("INTEGRATE_WEBHOOK_HOST", "") or "",
        system_name=os.environ.get("INTEGRATE_SYSTEM_NAME", "") or "",
        system_home=os.environ.get("INTEGRATE_SYSTEM_HOME", "") or "",
    )
    if os.environ.get("INTEGRATE_USE_PRODUCT_CODE", "") == "true":
        kwargs["product_code"] = os.environ.get("INTEGRATE_PRODUCT_CODE", "")
    else:
        kwargs["admin_key"] = os.environ.get("INTEGRATE_ADMIN_KEY", "")
    result = setup(**kwargs)
    display = {k: v for k, v in result.items() if k not in ("success", "path")}
    print(json.dumps(display, indent=2, ensure_ascii=False))
    if not result.get("success"):
        err = result.get("error") or result.get("detail") or "Unknown error"
        print(f"__ERROR__:{err}")
        sys.exit(1)

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

from gateway_api import (GatewayClient, create_api_key, gateway_config_path,
                         load_gateway_config, whoami)

logger = logging.getLogger("aimail_setup")


# ── Agent admin key helper ──────────────────────────────────────

def _persist_system_raw_key(system_id: str, key: str) -> None:
    """把**原始系统级 key** 落盘到 {AIMAIL_HOME}/systems/{sid}/.system_raw_key.key (0600, 三层收口)。

    cli/README.md 承诺"install 派生受限 agent_admin key 落盘, 原始 key 存
    系统层 .system_raw_key.key" —— 但激活+降级路径此前**没实现**(只有
    deploy_bridge 写该文件), 于是降级后 cfg 里只剩受限 key, 管理级操作
    (repair / address 管理 / key 轮换)再无凭据可用。

    幂等: 同值 → 跳过; 已有**不同**值 → 保留旧值(可能是权威)并 debug 记录。
    """
    if not key:
        return
    home = Path(os.environ.get("AIMAIL_HOME") or (Path.home() / ".aimail"))
    d = home / "systems" / system_id          # 三层收口: 归系统层
    p = d / ".system_raw_key.key"
    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        if p.is_file():
            cur = p.read_text().strip()
            if cur == key or cur:
                logger.debug("[aimail_setup] raw system key already on disk for %s", system_id)
                return
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key + "\n")
        logger.info("[aimail_setup] raw system key saved (%s)", p)
    except Exception as e:  # 不阻断安装: 落盘失败只告警
        logger.warning("[aimail_setup] failed to persist raw system key: %s", e)


def _downgrade_to_domain_admin_key(
    gateway_url: str, system_admin_key: str, system_id: str,
    domain: str,
) -> str:
    """Create a DOMAIN-scoped admin key and replace admin_key in gateway config.
    Returns the new key on success, or the original key on failure.

    Contract (2026-09-21, replaces the manager-bound ``agent``-category key).
    The agent runtime MUST be able to register its own addresses, and the
    gateway's ``POST /api/v1/admin/systems/{sid}/addresses`` requires
    ``scope IN (system, agent_admin)`` **and** passes
    ``require_domain_match(key, <bare domain>)`` (``core/api/auth.rs``
    ``check_domain_access``). A key whose ``email_address`` is the *manager's*
    address — outside the system's domain — is therefore rejected with
    ``403 "API key email '…' does not match target '…' — cross-address access
    denied"``, so **no address could ever be registered** (production
    regression, found 2026-09-21: every registration after an install 403'd).

    The shape the gateway documents for exactly this job is the domain-category
    key: ``core/api/keys.rs`` — "EXCEPTION: domain-category keys (bare domain
    email) may carry system scope for domain-level administration", and a
    system-level key (empty email) may de-escalate to one
    (``is_system_to_domain``). It keeps the least-privilege intent: the identity
    is narrowed to one bare domain and the key cannot mint system/platform keys
    (``create_api_key`` level rule).

    Notes:
      * a system with no domain has nothing to narrow to → keep the system key
        and say so (this shape needs a bare domain);
      * ``whoami`` is consulted first when the gateway answers: an agent-level
        key cannot be downgraded, and an already domain-scoped key MUST NOT be
        re-created (same-level creation is rejected by the gateway).
    """
    # 0) 传入的 key 若已是受限级(复用路径下 cfg 里存的就是降级后的 key), 网关会以
    #    "cannot create scopes at level 1 or above" 拒绝 —— 这不是失败, 而是"无需
    #    降级"。用 whoami 预检 + 错误文本双判(whoami 对某些 key/identity 组合可能
    #    取不到作用域, 故以错误文本为准, 保证判定确定)。
    try:
        me = whoami(gateway_url, system_admin_key, system_id)
        scopes = me.get("scopes") if isinstance(me, dict) else None
        if isinstance(scopes, list) and scopes:
            low = [str(s).lower() for s in scopes]
            if all(s in ("agent", "agent_admin") for s in low):
                logger.info("[aimail_setup] key already agent-scoped (%s) — downgrade not needed",
                            ",".join(scopes))
                return system_admin_key
            _email = str(me.get("email") or "")
            if "system" in low and not _email:
                # whoami 证明这是**系统级** key(空 email) ⇒ 立刻落盘。
                # 覆盖 admin-key 复用路径: 该路径下我们同样"拿到了系统级 key",
                # 落盘与随后降级是否成功无关(2026-09-22 契约: 拿到即落盘)。
                _persist_system_raw_key(system_id, system_admin_key)
            if "system" in low and _email and "@" not in _email and _email == domain:
                logger.info("[aimail_setup] key already domain-scoped (%s) — downgrade not needed",
                            _email)
                return system_admin_key
    except Exception:
        pass

    if not domain:
        # 没有域可收窄 ⇒ 无法造 domain 形 key;保留系统级 key(不降级)并说明,
        # 不制造"看起来降级了、实则域外身份"的坏 key。
        logger.info(
            "[aimail_setup] system %s has no domain — skipping the least-privilege "
            "downgrade (the domain-category key needs the bare domain); "
            "the config keeps the system-level key", system_id,
        )
        return system_admin_key

    result = create_api_key(
        gateway_url, system_admin_key, system_id,
        domain, ["system"], "domain",
    )
    raw = result.get("raw_key", "")
    if not raw:
        err = f"{result.get('error', '')} {result.get('detail', '')}".lower()
        if "privilege level" in err or "at or above" in err:
            # 传入的 key 本身就是受限级 ⇒ 无需降级, 更**不得**把它当成系统 key 落盘
            logger.info("[aimail_setup] key already agent/domain-scoped — downgrade not needed")
            return system_admin_key
        # 高可见: 降级失败会**放大权限**(agent 侧继续用系统级 key), 不能只当普通 warning。
        # 注意此时功能仍可用: 系统级 key 不受域匹配限制, 注册/管理操作照旧(只是没收到最小权限)。
        logger.error(
            "[aimail_setup] domain-scoped key NOT created (%s %s) — "
            "FALLING BACK TO SYSTEM KEY: the agent runtime keeps system-level "
            "privileges instead of the intended least-privilege scope "
            "(registration still works — a system key is unrestricted). "
            "Re-run `aimail repair` / install once the gateway accepts it.",
            result.get("error", ""), result.get("detail", ""),
        )
        return system_admin_key

    # 1) 降级成功 ⇒ 传入的 key 确证是系统级 ⇒ 落盘(cli/README.md:121 契约)。
    #    注意: 受限 key **不会**被写进系统层 .system_raw_key.key(其余分支保持只读), 而系统级 key
    #    的落盘在"拿到/被 whoami 证明"的当口就已完成(见本函数开头与激活分支)。
    _persist_system_raw_key(system_id, system_admin_key)

    # Replace in config file
    cfg_path = gateway_config_path(system_id)
    if cfg_path.is_file():
        with open(cfg_path) as f:
            cfg = json.load(f)
        cfg["admin_key"] = raw
        with open(cfg_path, "w") as f:
            json.dump(cfg, f, indent=2)
    logger.info("[aimail_setup] domain-scoped admin key created and saved (domain=%s)", domain)
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
                    "[aimail_setup] Detected external IP %s for webhook callback "
                    "(gateway at %s is public)", external_ip, gateway_host
                )
                return external_ip
    except Exception:
        pass

    if lan_ip:
        logger.warning(
            "[aimail_setup] Gateway at %s is public but cannot detect external IP. "
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
    save_raw_snapshots: bool = True,
    manager_address: str = "",
    webhook_host: str = "",
    system_home: str = "",
) -> None:
    """Save AIMail gateway connection config to standalone JSON file.

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
        # 绝对化(2026-09-25 G2): 相对路径入 cfg ⇒ 换 cwd 后归属反查/指针判定漂移
        cfg["system_home"] = os.path.abspath(os.path.expanduser(str(system_home)))

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
    save_raw_snapshots: bool = True,
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

    # 契约(cli/README): 平台在激活时下发的**系统级 key** 必须当场落盘到
    # systems/{sid}/.system_raw_key.key。放在这里=拿到即落盘, 与后续降级是否成功无关
    # —— 否则降级失败(或早退)会让系统级 key 只剩云端哈希、本地永久不可得
    # (2026-09-22 实测: shared-default-6b9fc46c 就是这样丢的)。
    _persist_system_raw_key(created_system_id, admin_key)

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
    logger.info("[aimail_setup] Gateway config saved to %s", gateway_config_path())
    # Downgrade to the least-privilege DOMAIN-scoped key — see
    # _downgrade_to_domain_admin_key for why the identity must be the domain.
    agent_key = _downgrade_to_domain_admin_key(
        gateway_url, admin_key, created_system_id, created_domain,
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
    save_raw_snapshots: bool = True,
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
            # 系统名的权威来源:显式 -n(INTEGRATE_NAME_EXPLICIT=true)或既有
            # cfg;env 派生值不得覆写(2026-09-11 D 缺陷:机器级 .env 的旧
            # 系统名会污染复用系统)。
            system_name=(
                system_name if os.environ.get("INTEGRATE_NAME_EXPLICIT") == "true"
                else (prev.get("system_name") or system_name)
            ),
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
        # Least-privilege domain-scoped key; the identity is the bare domain
        # (explicit arg, else the config being reused) — see
        # _downgrade_to_domain_admin_key for why the manager address is wrong.
        agent_key = _downgrade_to_domain_admin_key(
            gateway_url, admin_key, system_id,
            domain or prev.get("domain", ""),
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
        save_raw_snapshots=os.environ.get("INTEGRATE_SAVE_SNAPSHOTS", "true") != "false",
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

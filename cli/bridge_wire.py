"""bridge_wire — CLI-owned bridge route wiring for the inbound live/down notification.

Phase 2a (2026-09-28). `aimail address -a <agent> [-s <sid>] --inbound-live|--inbound-down`
tells this machine's bridge "route this address to my local receive endpoint" (live) or
"stop routing it" (down). The SDK calls the CLI once per inbound event; *what to do* is
decided here (the command does not hardcode "bridge" — another action can hang off the
same switch later).

WHY THIS MODULE EXISTS (do not replace it with the SDK's bridge helpers)
    The SDK's bridge wiring (`aimail_base.ensure_bridge_route` and friends) is scheduled
    for deletion in Phase 1. A CLI that called into those symbols would break the moment
    the SDK side is removed, so the CLI owns its own bridge client here. Nothing in this
    module imports pysdk/aimailsdk.

CONTRACT (read out of the bridge repo — ~/aimail-bridge/src/admin.rs + router.rs)
    The admin API is served on the bridge's configured address, regardless of mode:
      GET    /health                -> {"status","uptime_secs","version"}
      GET    /api/v1/routes         -> [{"email","host","port"}, ...]
      POST   /api/v1/routes         <- {"email","host","port"}    (upsert; idempotent)
      DELETE /api/v1/routes/:email  -> "ok"                       (withdraw)
    `host` carries the FULL absolute URL: `ProfileRoute::from_url` keeps the path
    verbatim, whereas the legacy `ProfileRoute::new` branch rebuilds
    `http://{host}:{port}/<bridge default inbound path>` — feeding it a value that
    already contains a port produces the undeliverable `http://host:port:80/...`
    (admin.rs:137 rejects port == 0, hence the SDK's `port: 80` placeholder). This
    module therefore only ever sends a validated absolute http(s) URL and refuses to
    invent one.

READ-ONLY discipline
    No file is written anywhere: the bridge's own config file is the "a bridge is
    declared here" signal, the system config supplies the admin port, and the binding
    supplies the target. Nothing in here touches the SDK, agent registration, or any
    config file.

Outcome states reuse the existing route vocabulary (no new words are minted):
    ok        — the bridge accepted the upsert / withdrawal
    skipped   — nothing to route (a pull binding: the gateway fetches the mail)
    no_bridge — no bridge declared on this machine: a deliberate no-op
    failed    — declared but unusable: fail closed, change nothing, say why

`up` and `down` are NOT symmetric (2026-09-28 ruling): the route is keyed by the
address (`DELETE /api/v1/routes/:email`), so a withdrawal needs the email and a
reachable bridge and nothing else. A binding whose local endpoint is empty (pull) or
unusable must therefore still withdraw any route created back when it was push — the
old "empty webhook_url ⇒ skipped" shortcut left that route standing until the bridge's
own health prune (up to 180s, longer if delivery keeps looking fine), i.e. mail kept
being pushed to a host that had stopped receiving. Only `up` cares about the URL.
"""

from __future__ import annotations

import json
import socket
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

#: Admin API host. The bridge binds to the machine's own address and the admin API is
#: IP-restricted to localhost by default (config.rs `admin_allowed_ips`), so the CLI
#: always talks to the loopback address — same judge as the SDK side.
ADMIN_HOST = "127.0.0.1"
#: Fallback admin port when neither the system config nor the bridge config says otherwise.
DEFAULT_ADMIN_PORT = 38081

STATE_OK = "ok"
STATE_SKIPPED = "skipped"
STATE_NO_BRIDGE = "no_bridge"
STATE_FAILED = "failed"

ACTION_LIVE = "live"
ACTION_DOWN = "down"


# ── helpers ────────────────────────────────────────────────────────────────────

def _valid_port(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if 0 < n < 65536 else 0


def _port_from_bind(bind: str) -> int:
    """Port part of a bridge `bind` value ("0.0.0.0:38080", "[::1]:38081", "38081")."""
    s = (bind or "").strip()
    if not s:
        return 0
    if s.startswith("["):
        _, _, rest = s.partition("]")
        return _valid_port(rest.lstrip(":") if rest.startswith(":") else "")
    if ":" in s:
        return _valid_port(s.rsplit(":", 1)[1])
    return _valid_port(s)


def load_declaration(gateway_cfg: dict, bridge_cfg_path) -> dict:
    """Read-only: does this machine declare a bridge, and where is its admin API?

    `declared` is true when the bridge's own config file is in place, or when the system
    config carries an explicit `bridge_admin_port`. `usable` is false when something is
    declared but broken (unreadable/invalid TOML) — that case must fail closed, never be
    mistaken for "no bridge here". Never raises.
    """
    out = {
        "declared": False, "usable": True, "reason": "", "mode": "", "bind": "",
        "admin_host": ADMIN_HOST, "admin_port": DEFAULT_ADMIN_PORT,
        "source": "", "gateway_port": 0,
    }
    gw_port = _valid_port((gateway_cfg or {}).get("bridge_admin_port"))
    out["gateway_port"] = gw_port
    if gw_port:
        out["declared"] = True
        out["admin_port"] = gw_port
        out["source"] = "system config bridge_admin_port"

    path = Path(bridge_cfg_path)
    try:
        present = path.is_file()
    except OSError:
        present = False
    if not present:
        if not out["declared"]:
            out["reason"] = f"no bridge config at {path} and no bridge_admin_port declared"
        return out

    try:
        with open(path, "rb") as fh:
            toml_cfg = tomllib.load(fh)
    except Exception as e:  # noqa: BLE001 — a broken declaration is a fail-closed state
        out.update(declared=True, usable=False, source=str(path),
                   reason=f"bridge config {path} is unreadable/invalid TOML "
                          f"({e.__class__.__name__}: {e}) — nothing changed")
        return out

    out["declared"] = True
    out["source"] = str(path)
    out["mode"] = str(toml_cfg.get("mode", "") or "")
    out["bind"] = str(toml_cfg.get("bind", toml_cfg.get("addr", "")) or "")
    if not gw_port:
        bind_port = _port_from_bind(out["bind"])
        if bind_port:
            out["admin_port"] = bind_port
    return out


def tcp_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Real TCP probe: is something accepting connections on host:port?"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001 — any failure means "not reachable"
        return False


def admin_health(host: str, port: int, timeout: float = 1.5):
    """GET /health → the parsed JSON dict when the admin API answers, else None.

    Informational only: it proves a real bridge is on the port (and gives its version for
    the receipt line). Reachability itself is the TCP probe, so the CLI and the SDK judge
    "is the bridge here" identically.
    """
    try:
        with urllib.request.urlopen(f"http://{host}:{int(port)}/health", timeout=timeout) as r:
            data = json.loads(r.read().decode() or "{}")
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def admin_reachable(host: str, port: int, timeout: float = 0.5) -> tuple:
    """(reachable, detail) — reachable = the admin port accepts connections."""
    if tcp_open(host, port, timeout=timeout):
        health = admin_health(host, port)
        if health and health.get("status"):
            return True, f"admin {host}:{port} is up (health status={health.get('status')}, "\
                         f"version={health.get('version', '?')})"
        return True, f"admin {host}:{port} is up (no /health contract answer)"
    return False, f"admin {host}:{port} is not reachable"


def validate_target(value) -> tuple:
    """(absolute_url, reason) — the target must already be a deliverable absolute URL.

    `reason` is empty when the value is accepted. A bare `host:port` (or `host:port/path`)
    is refused instead of being prefixed: prefixing it would produce exactly the
    undeliverable `http://host:port:80/...` shape measured on 2026-09-27. The accepted URL
    is passed through verbatim — never rebuilt.
    """
    raw = ("" if value is None else str(value)).strip()
    if not raw:
        return "", "the binding carries no webhook_url (pull mode: the gateway fetches " \
                   "the mail, there is no local endpoint to route to)"
    try:
        u = urlparse(raw)
        port = u.port              # raises on a malformed/out-of-range port
    except ValueError as e:
        return "", f"'{raw}' is not a usable URL ({e})"
    scheme = (u.scheme or "").lower()
    if scheme not in ("http", "https"):
        return "", f"not an absolute http(s) URL: '{raw}' has no http(s):// scheme " \
                   f"(a bare host:port cannot be delivered to)"
    if not u.netloc or not u.hostname:
        return "", f"'{raw}' carries no host"
    if port is not None and not _valid_port(port):
        return "", f"'{raw}' carries an invalid port"
    return raw, ""


# ── bridge client ──────────────────────────────────────────────────────────────

def _admin_request(method: str, host: str, port: int, path: str, body=None,
                   timeout: float = 5.0) -> tuple:
    """(ok, detail) — one admin call. Never raises; the reason travels back as text."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://{host}:{int(port)}{path}", data=data,
        headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, (r.read().decode(errors="replace") or "").strip()
    except urllib.error.HTTPError as e:
        return False, f"bridge answered HTTP {e.code} ({e.reason})"
    except Exception as e:  # noqa: BLE001
        return False, f"{e.__class__.__name__}: {e}"


def upsert_route(email: str, target_url: str, host: str = ADMIN_HOST,
                 port: int = DEFAULT_ADMIN_PORT) -> tuple:
    """POST /api/v1/routes {email, host, port} — idempotent upsert (bridge-side table)."""
    # `port` is the placeholder the bridge ignores when `host` is a full URL; 80 only
    # because admin.rs rejects 0. `host` is the validated absolute URL, verbatim.
    return _admin_request("POST", host, port, "/api/v1/routes",
                          {"email": email, "host": target_url, "port": 80})


def withdraw_route(email: str, host: str = ADMIN_HOST,
                   port: int = DEFAULT_ADMIN_PORT) -> tuple:
    """DELETE /api/v1/routes/:email — withdraw this address's route."""
    from urllib.parse import quote
    return _admin_request("DELETE", host, port, f"/api/v1/routes/{quote(str(email), safe='')}")


# ── the three-step decision ────────────────────────────────────────────────────

def sync_route(action: str, email: str, target_url, gateway_cfg: dict,
               bridge_cfg_path, admin_host: str = ADMIN_HOST) -> dict:
    """Decide what this machine must do for one address, and do it. Never raises.

    1. for `action="live"` (`up`) only, the binding's local endpoint must be a deliverable
       absolute URL. An EMPTY value is a legitimate pull binding (the gateway fetches the
       mail) ⇒ `skipped` + reason; a non-empty value that cannot be delivered to is a
       defect ⇒ `failed` (refused, with the reason) — never a silent pass. `down` does NOT
       look at the URL at all: the route is keyed by email, so a withdrawal must happen
       even when the binding's endpoint went empty (2026-09-28 — the empty⇒skip shortcut
       used to strand a route created while the binding was still push);
    2. a declared bridge must be reachable — otherwise `failed` with the reason and
       NOTHING is touched (fail closed: a half-configured bridge must not be written to);
    3. no bridge declared ⇒ `no_bridge`: a deliberate no-op, not an error.

    `ok` means the bridge accepted the upsert (`action="live"`) or the withdrawal
    (`action="down"`).
    """
    act = ACTION_DOWN if str(action).lower() == ACTION_DOWN else ACTION_LIVE
    out = {
        "state": STATE_FAILED, "action": act, "email": str(email or ""),
        "target": "", "reason": "", "admin_port": DEFAULT_ADMIN_PORT,
        "mode": "", "bind": "", "source": "",
    }

    if act != ACTION_DOWN:
        raw = ("" if target_url is None else str(target_url)).strip()
        target, why = validate_target(target_url)
        if not target:
            out.update(state=STATE_SKIPPED if not raw else STATE_FAILED, reason=why)
            return out
        out["target"] = target

    decl = load_declaration(gateway_cfg, bridge_cfg_path)
    out.update(admin_port=decl["admin_port"], mode=decl["mode"],
               bind=decl["bind"], source=decl["source"])
    if not decl["declared"]:
        out.update(state=STATE_NO_BRIDGE, reason=decl["reason"])
        return out
    if not decl["usable"]:
        out.update(state=STATE_FAILED, reason=decl["reason"])
        return out

    reachable, detail = admin_reachable(admin_host, decl["admin_port"])
    if not reachable:
        out.update(state=STATE_FAILED,
                   reason=f"bridge declared ({decl['source']}) but {detail} "
                          f"— nothing changed")
        return out

    if act == ACTION_DOWN:
        ok, detail = withdraw_route(out["email"], admin_host, decl["admin_port"])
        if ok:
            out.update(state=STATE_OK, reason=detail or "route withdrawn")
        else:
            out.update(state=STATE_FAILED, reason=f"could not withdraw the route: {detail}")
        return out

    ok, detail = upsert_route(out["email"], out["target"], admin_host, decl["admin_port"])
    if ok:
        out.update(state=STATE_OK, reason=detail or "route upserted")
    else:
        out.update(state=STATE_FAILED, reason=f"could not upsert the route: {detail}")
    return out


def format_line(outcome: dict) -> str:
    """One English line for the receipt (same shape as the SDK's route lines)."""
    o = outcome or {}
    state = str(o.get("state", ""))
    email = str(o.get("email") or "")
    if state == STATE_OK and str(o.get("action")) == ACTION_DOWN:
        return f"route withdrawn: {email}"
    if state == STATE_OK:
        return f"route: {email} -> {o.get('target', '')}"
    if state == STATE_SKIPPED:
        return f"route skipped for {email}: {o.get('reason') or 'no local endpoint'}"
    if state == STATE_NO_BRIDGE:
        return f"route skipped for {email}: {o.get('reason') or 'no local bridge'}"
    if state == STATE_FAILED:
        return f"route FAILED for {email}: {o.get('reason') or 'unknown'} -- run 'aimail repair'"
    return f"route: {email} state={state or 'unknown'}"

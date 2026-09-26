#!/usr/bin/env python3
"""repair.py - automatic chain repair (the standard command line, replacing ad-hoc in-session scripting).

Principles (owner ruling 2026-08-30):
- Do not reinvent detection: run check_status.py's existing checks item by item; repair only what fails, then re-check.
- No second registration implementation: every repair action reuses install / the shared chain functions
  (deploy_bridge.start_bridge, the route refresh of the `aimail bridge` command,
  the aimail_base.register_agent_email registration chain).
- agentmail.json is the single source of truth: repair writes the local authoritative values back to the cloud/bridge.
- Idempotent: repeating the repair yields the same result (webhook pairing and route registration are both idempotent).
"""
import json
import subprocess
import sys
import re  # noqa: E402
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import os  # noqa: E402
from _common import aimail_home as _aimail_home, bridge_status

# Same semantics as scripts/aimail: an empty env falls back to ~/.aimail
# Single source of truth for the home root = pysdk/aimail_base.aimail_home() (the canonical implementation);
# the fallback copy below only covers an import failure (recovery from a broken state)


AIMAIL_HOME = _aimail_home()
SYSTEMS_DIR = AIMAIL_HOME / "systems"

GREEN, YELLOW, RED, NC = "\033[92m", "\033[93m", "\033[91m", "\033[0m"
OK, WARN, CROSS = "✓", "⚠", "✗"
BRIDGE_CFG = AIMAIL_HOME / "bridge" / "aimail_bridge.toml"
BRIDGE_PID = AIMAIL_HOME / "bridge" / "bridge.pid"
BRIDGE_BIN = AIMAIL_HOME / "bridge" / "bin" / "aimail-bridge"
ROUTES_FILE = AIMAIL_HOME / "bridge" / "aimail_routes.toml"


def _ok(msg):
    print(f"  {GREEN}{OK}{NC} {msg}")


def _warn(msg):
    print(f"  {YELLOW}{WARN}{NC} {msg}")


def _fail(msg):
    print(f"  {RED}{CROSS}{NC} {msg}")


def _bridge_pids():
    """Bridge process list -- single source of truth: the lifecycle contract (P2 close-out, 2026-09-20).
    The old implementation matched pgrep patterns (POSIX-only, able to both miss and over-report); under the contract the bridge reports its own pid."""
    try:
        st = bridge_status(str(BRIDGE_BIN), str(AIMAIL_HOME / "bridge" / "bridge.pid"))
        if st.get("running") and st.get("pid"):
            return [int(st["pid"])]
    except Exception:
        pass
    return []


def _load_gateway_cfg(sid: str):
    p = SYSTEMS_DIR / sid / "aimail_gateway.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def _gateway_client(sid: str):
    """aimail_tools._GatewayClient (the full method set). runtime_core resolves the core directory centrally."""
    gw = _load_gateway_cfg(sid)
    if not gw:
        return None, None
    from runtime_core import load_core
    load_core()
    from aimail_tools import _GatewayClient
    return _GatewayClient(gw["gateway_url"], gw["admin_key"]), gw


def _run_check(sid: str):
    """Run the existing check (subprocess, zero logic duplication); returns (all_pass, checks[])."""
    cmd = [sys.executable, str(SCRIPTS_DIR / "check_status.py"), "--json", "--system-id", sid]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        # A hanging check must not stall repair -- that item is recorded as failed and the ladder continues
        return False, [{"name": "check_status", "pass": False,
                        "detail": "check_status.py timed out after 120s"}]
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return False, []
    checks = data.get("checks", data if isinstance(data, list) else [])
    return all(c.get("pass") for c in checks), checks


def _ensure_bridge_running() -> bool:
    pids = _bridge_pids()
    if pids:
        _ok(f"bridge ok (already running pid={pids[0]})")
        return True
    _warn("bridge not running -> starting it (deploy_bridge.start_bridge)")
    if not BRIDGE_CFG.exists() or not BRIDGE_BIN.exists():
        _fail("bridge not deployed (config/binary missing) -- run install first")
        return False
    from deploy_bridge import start_bridge
    if start_bridge(str(BRIDGE_BIN), str(BRIDGE_CFG), str(BRIDGE_PID)):
        _ok(f"bridge started (pid={BRIDGE_PID.read_text().strip() if BRIDGE_PID.exists() else '?'})")
        return True
    _fail("bridge failed to start -- check ~/.aimail/bridge/aimail-bridge.log")
    return False


def _refresh_routes(sid: str) -> bool:
    """Refresh this system's routes = reuse the `aimail bridge --system-id` logic (subprocess)."""
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "aimail"), "bridge", "--system-id", sid],
        capture_output=True, text=True, timeout=60)
    sys.stdout.write(r.stdout or "")
    if r.returncode != 0:
        sys.stdout.write(r.stderr or "")
        return False
    return True


def _repair_webhook_pairing(sid: str, deep: bool = False) -> bool:
    """Repair the gateway webhook pairing.

    Evidence-driven (default): in pull mode webhook_url must be an empty string (setting it means the cloud pushes directly to
    loopback, which always fails; confirmed 2026-08-30); the secret is never echoed by GET, so only when this machine
    can read empty-signature evidence in that system's pending queue do we re-pair
    url+secret from agentmail.json (the register chain's already-exists branch, idempotent).
    --deep: skip the evidence and rewrite the pairing of every agent of this system from agentmail.json.
    """
    c, gw = _gateway_client(sid)
    if not c:
        _fail(f"gateway config missing, skipping the webhook pairing repair (system {sid})")
        return False
    sys.path.insert(0, str(SCRIPTS_DIR.parent / "pysdk"))
    try:
        pend = c._request("POST", "/api/v1/admin/pending",
                          body={"filter": [], "emails": []})
    except Exception as e:
        _warn(f"pending query failed ({e}) -- cannot read the empty-signature evidence; use --deep to rewrite")
        return False
    batches = pend.get("batches", pend if isinstance(pend, list) else [])
    empties = []
    for b in batches if isinstance(batches, list) else []:
        for d in b.get("deliveries", []):
            try:
                hs = json.loads(d.get("headers") or "{}")
            except json.JSONDecodeError:
                hs = {}
            if not (hs.get("X-Webhook-Signature") or hs.get("X-Webhook-Signature-V2")):
                empties.append((d.get("id"), d.get("email")))
    if not empties and not deep:
        _ok("webhook pairing ok (no evidence of a missing pairing)")
        return False
    if deep and not empties:
        # --deep: unconditionally rewrite the pairing of every agent of this system from agentmail.json
        targets = []
        for ajx in sorted((SYSTEMS_DIR / sid).glob("*/agentmail.json")):
            try:
                d = json.loads(ajx.read_text())
            except Exception:
                continue
            if d.get("email"):
                targets.append(d["email"])
        empties = [(None, e) for e in targets]
        _warn(f"--deep: rewriting the webhook pairing of {len(empties)} agent(s) from agentmail.json")
    _warn(f"{len(empties)} pairing target(s) need repair (evidence={bool([x for x in empties if x[0]]) or deep})")
    fixed = 0
    for _id, email in empties:
        # directory-key formula matches the global convention (non [\w.-] -> '_'; a dotted address agent.x@dom -> agent.x_dom)
        _key = re.sub(r"[^\w.\-]", "_", str(email))
        aj = SYSTEMS_DIR / sid / _key / "agentmail.json"
        if not aj.is_file():
            # loose match: look it up by directory-name prefix
            cands = [d for d in (SYSTEMS_DIR / sid).glob("*/agentmail.json")
                     if email and json.loads(d.read_text()).get("email") == email]
            aj = cands[0] if cands else None
        if not aj:
            _fail(f"{email}: local agentmail.json missing -- the single source of truth is gone; reinstall that agent")
            continue
        local = json.loads(aj.read_text())
        from runtime_core import load_core
        load_core()
        import aimail_base as _ab
        res = _ab.register_agent_email(
            c, sid, email,
            webhook_url=local.get("webhook_url", ""),
            webhook_secret=local.get("webhook_secret", ""),
            manager_address=local.get("manager_address", ""),
        )
        _ok(f"{email}: registration chain re-run (url+secret re-paired){' (key returned)' if res.get('api_key') else ''}")
        fixed += 1
    # drain the broken pending (an empty-signature item never self-heals -- decided when the headers jumped the queue)
    for pid_, _e in empties:
        if pid_ is None:
            continue
        try:
            c._request("POST", "/api/v1/admin/pending/ack", body={"ids": [pid_]})
            _ok(f"acked the broken pending id={pid_}")
        except Exception as e:
            _warn(f"ack {pid_} failed: {e}")
    return fixed > 0


def _drain_stuck(sid: str) -> bool:
    """--deep: ack every stuck pending (>10min) as a fallback cleanup."""
    c, _ = _gateway_client(sid)
    if not c:
        return False
    import time as _t
    try:
        pend = c._request("POST", "/api/v1/admin/pending", body={"filter": [], "emails": []})
    except Exception as e:
        _warn(f"pending query failed: {e}")
        return False
    batches = pend.get("batches", pend if isinstance(pend, list) else [])
    stuck = []
    for b in batches if isinstance(batches, list) else []:
        for d in b.get("deliveries", []):
            created = d.get("created_at") or ""
            try:
                from datetime import datetime
                age = _t.time() - datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                if age > 600:
                    stuck.append(d.get("id"))
            except ValueError:
                continue
    for pid_ in stuck:
        try:
            c._request("POST", "/api/v1/admin/pending/ack", body={"ids": [pid_]})
            _ok(f"ack stuck pending id={pid_}")
        except Exception as e:
            _warn(f"ack {pid_} failed: {e}")
    return bool(stuck)


# ═══════════════════════════════════════════════════════════════
# 2026-09-04 maintenance suite: config / resource / consistency repairs (all idempotent)
# ═══════════════════════════════════════════════════════════════




def _registry_order_roots() -> list:
    """Platform pointer-root candidates (platforms.json order x home_dir -- the single platform knowledge source)."""
    import json as _j
    try:
        reg = _j.load(open(str(Path(__file__).resolve().parent / "platforms.json"), encoding="utf-8"))
        return [(n, reg["platforms"][n].get("home_dir", "." + n))
                for n in reg.get("order", []) if n in reg.get("platforms", {})]
    except Exception:
        return []


def _detect_platform_from_home(system_home):
    """Marker detection with the same rules as cli/aimail.detect_platform_from_home."""
    p = Path(system_home)
    if p.name == ".pi" and (p / "agent").is_dir():
        return "pi"
    if p.name == ".dsh" and (p / "profiles").is_dir() and (p / "storages").is_dir():
        return "dsh"
    if (p / "hermes-agent").exists() or (p / "profiles").is_dir():
        return "hermes"
    if (p / "openclaw.json").is_file():
        return "openclaw"
    if (p / "backend" / "app" / "gateway").is_dir():
        return "deerflow"
    return "unknown"


def _auto_platform_home(sid: str) -> str:
    """On a single-platform machine resolve the platform root automatically: exactly one platform directory
    exists and its markers match -> use it; several or none -> '' (never guess, --home is then required)."""
    hits = []
    home = Path.home()
    for plat, root in _registry_order_roots():
        d = home / root
        if d.exists() and _detect_platform_from_home(d) == plat:
            hits.append((plat, str(d)))
    if len(hits) == 1:
        return hits[0][1]
    if len(hits) > 1:
        # multi-platform machine: try the system_home already stored in gateway.json
        gw = _load_gateway_cfg(sid) or {}
        sh = gw.get("system_home", "")
        if sh and Path(sh).is_dir():
            return sh
    return ""


def _pointer_paths_for(platform: str):
    """Platform pointer candidate paths (driven by the platforms.json pointer table, the same source as the aimail CLI)."""
    import json as _j
    try:
        reg = _j.load(open(str(Path(__file__).resolve().parent / "platforms.json"), encoding="utf-8"))
        pdef = (reg.get("platforms", {}).get(platform) or {})
    except Exception:
        pdef = {}
    home = Path.home() / pdef.get("home_dir", f".{platform}")
    file = (pdef.get("pointer") or {}).get("file", ".agentmail")
    kind = (pdef.get("pointer") or {}).get("kind", "root")
    root_ptr = home / file
    if kind != "root_or_profiles":
        return [root_ptr]
    out = [root_ptr]
    profiles = home / "profiles"
    if profiles.is_dir():
        out += sorted(profiles.glob("*/.agentmail"))
    return out


def _sid_has_pointer(sid: str) -> bool:
    for plat in _pointer_registry_order():
        for ptr in _pointer_paths_for(plat):
            if ptr.is_file():
                try:
                    if json.loads(ptr.read_text()).get("system_id") == sid:
                        return True
                except Exception:
                    pass
    return False


def _pointer_registry_order() -> list:
    """Registry platform order (platforms.json order -- the CLI's single platform knowledge source)."""
    try:
        import json as _j
        reg = _j.load(open(str(Path(__file__).resolve().parent / "platforms.json"), encoding="utf-8"))
        return list(reg.get("order", []))
    except Exception:
        return []


def _repair_gateway_config(sid: str, args_home: str = "") -> bool:
    """Fill gaps in system_home/webhook_host only; never overwrite an existing value."""
    gw_path = SYSTEMS_DIR / sid / "aimail_gateway.json"
    if not gw_path.is_file():
        _fail(f"gateway config does not exist: {gw_path}")
        return False
    cfg = json.loads(gw_path.read_text())
    changed = False
    root = args_home or _auto_platform_home(sid)
    if not cfg.get("system_home"):
        if root and _detect_platform_from_home(Path(root)) != "unknown":
            cfg["system_home"] = root
            _ok(f"system_home backfilled: {root}")
            changed = True
        else:
            _warn("system_home missing and the platform root cannot be determined (on a multi-platform machine pass --home)")
    if not cfg.get("webhook_host"):
        try:
            sys.path.insert(0, str(SCRIPTS_DIR))
            from setup_system import _detect_webhook_host
            wh = _detect_webhook_host(cfg.get("gateway_url", ""))
            if wh:
                cfg["webhook_host"] = wh
                _ok(f"webhook_host backfilled: {wh}")
                changed = True
        except Exception as e:
            _warn(f"webhook_host probe failed (skipped): {e}")
    if changed:
        gw_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
        import os as _os
        _os.chmod(gw_path, 0o600)
    return changed


def _repair_pointer(sid: str, platform_home: str) -> bool:
    """Recreate the pointer: a certain platform root + no pointer for this sid + the target pointer file absent -> write it."""
    if _sid_has_pointer(sid):
        return False
    if not platform_home:
        return False
    plat = _detect_platform_from_home(Path(platform_home))
    if plat == "unknown":
        return False
    ajx = sorted((SYSTEMS_DIR / sid).glob("*/agentmail.json"))
    email = ""
    for a in ajx:
        try:
            d = json.loads(a.read_text())
            if d.get("email"):
                email = d["email"]
                break
        except Exception:
            continue
    if not email:
        return False
    home = Path.home()
    roots = dict(_registry_order_roots())
    ptr = home / roots.get(plat, "." + plat) / ".agentmail"
    if ptr.exists():
        return False  # a pointer already exists (for another system) -- do not overwrite
    ptr.write_text(json.dumps({"system_id": sid, "email": email}, indent=2))
    _ok(f"pointer created: {ptr} → {sid}")
    return True


# ═══════════════════════════════════════════════════════════════
# Runtime resource redeploy (registry-driven: health_checks probes + install_steps entry)
# ═══════════════════════════════════════════════════════════════

_REG_FILE_KINDS = ("file_contains", "file_contains_alt", "file_exists",
                   "file_exists_any", "glob_dir_any")


def _platform_def(plat: str) -> dict:
    """Registry platform definition (platforms.json = the single platform knowledge source)."""
    import json as _j
    try:
        reg = _j.load(open(str(Path(__file__).resolve().parent / "platforms.json"),
                           encoding="utf-8"))
        return (reg.get("platforms", {}) or {}).get(plat, {}) or {}
    except Exception:
        return {}


def _failing_file_checks(plat: str, sh: str) -> list:
    """Resource-intact probe: which file-class health_checks entries of the registry have no hit.

    All three locator keys must be honoured (path/alt/glob) -- the openclaw plugin-directory check uses glob,
    and missing that key misreads "resources present" as "missing".
    """
    import glob as _g
    fails = []
    for ch in _platform_def(plat).get("health_checks", []) or []:
        if ch.get("kind") not in _REG_FILE_KINDS:
            continue
        pat = ch.get("path", "") or ch.get("alt", "") or ch.get("glob", "")
        for cand in _g.glob(pat.replace("{home}", sh).replace("{user_home}", str(Path.home()))):
            if ch.get("kind") == "glob_dir_any":
                break  # directory class: a pattern hit counts as present (no content matching)
            try:
                if ch.get("kind") in ("file_exists", "file_exists_any"):
                    if Path(cand).is_file():
                        break
                elif ch.get("marker", "") in Path(cand).read_text(errors="replace"):
                    break
            except Exception:
                continue
        else:
            fails.append(ch)
    return fails


def _sdk_install_target(plat: str) -> str:
    """The type of this platform's self-sufficient SDK install entry (registry install_steps kind=sdk_install -> target).

    Platforms without that step (openclaw/pi/dsh) manage their resources themselves (plugin/host commands),
    so the CLI does not install for them -> an empty string is returned and the caller prints the registry's own fix hint.
    """
    for st in _platform_def(plat).get("install_steps", []) or []:
        if st.get("kind") == "sdk_install" and st.get("target"):
            return str(st["target"])
    return ""


def _repair_runtime_resources(sid: str, platform_home: str) -> bool:
    """L2 runtime resources missing -> idempotent reinstall through that platform's self-sufficient SDK install entry.

    The target type comes from the registry install_steps (sdk_install -> target); a platform without such an entry
    only prints its health_checks fix hint instead of spawning an install command that is bound to fail.
    """
    gw = _load_gateway_cfg(sid) or {}
    sh = platform_home or gw.get("system_home", "")
    if not sh or not Path(sh).is_dir():
        _warn("platform root not resolvable, skipping the runtime-resource redeploy (for remote platforms such as deerflow run it on the host)")
        return False
    plat = _detect_platform_from_home(Path(sh))
    if plat == "unknown":
        _warn("platform type not recognised (the root is neither a known platform nor a self-built one) -> skipping the runtime-resource redeploy")
        return False
    fails = _failing_file_checks(plat, sh)
    if not fails:
        _ok(f"{plat} runtime resources ok")
        return False
    tgt = _sdk_install_target(plat)
    if not tgt:
        _warn(f"{plat} runtime resources missing; this platform has no SDK install entry (its own installer manages them) -> follow the hint:")
        for ch in fails:
            _warn(f"  [{ch.get('id', '?')}] {ch.get('fix') or ch.get('fail_text', '')}")
        return False
    _warn(f"{plat} runtime resources missing -> idempotent reinstall (install.py install --type {tgt} --home {sh})")
    # Call it by file path (like every other step here) instead of relying on an in-interpreter `import aimail`;
    # install.py bootstraps sys.path, so the repo (pysdk/) and pip (site-packages/aimail/) layouts both work.
    from runtime_core import resolve_core_dir
    install_py = os.path.join(resolve_core_dir(), "install.py")
    r = subprocess.run(
        [sys.executable, install_py, "install", "--type", tgt, "--home", sh,
         "--system-id", sid],
        capture_output=True, text=True, timeout=300)
    sys.stdout.write((r.stdout or "")[-600:])
    if r.returncode == 0:
        _ok("runtime resources reinstalled")
        return True
    _fail(f"reinstall failed (exit {r.returncode}): {(r.stderr or '')[-200:]}")
    return False


def _repair_agentmail_json(sid: str) -> bool:
    """Fill gaps in agentmail.json (rebuildable fields) + align webhook_url with the live route."""
    import urllib.request, urllib.error
    gw = _load_gateway_cfg(sid) or {}
    changed = False
    sysdir = SYSTEMS_DIR / sid
    if not sysdir.is_dir():
        return False
    # routes table (target URL)
    routes = {}
    if ROUTES_FILE.exists():
        for line in ROUTES_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                routes[k.strip().strip('"')] = v.strip().strip('"').strip(",")

    def _alive(url):
        if not url or not url.startswith("http"):
            return False
        try:
            urllib.request.urlopen(urllib.request.Request(url, data=b"", method="POST"), timeout=3)
            return True
        except urllib.error.HTTPError as e:
            return e.code != 404
        except Exception:
            return False

    for ajx in sorted(sysdir.glob("*/agentmail.json")):
        try:
            d = json.loads(ajx.read_text())
        except Exception:
            continue
        orig = dict(d)
        # 1) fill gaps in rebuildable fields (gateway.json is authoritative)
        for k in ("gateway_url", "domain", "system_id", "system_name", "manager_address"):
            if not d.get(k) and gw.get(k):
                d[k] = gw[k]
        # 2) align webhook_url with the live route
        email = d.get("email", "")
        target = routes.get(email, "")
        declared = d.get("webhook_url", "")
        from urllib.parse import urlparse as _up
        _h = _up(target).hostname if target else ""
        _local = _h in ("127.0.0.1", "localhost", "::1")
        if target and _alive(target) and _local:
            if declared and not _alive(declared):
                d["webhook_url"] = target
                _ok(f"{ajx.parent.name}: webhook_url {declared} -> {target} (declared dead, route alive)")
            elif declared and declared.rstrip("/") != target.rstrip("/"):
                d["webhook_url"] = target
                _ok(f"{ajx.parent.name}: webhook_url aligned to route target {target}")
        if d != orig:
            # write through the shared atomic helper (0600/tmp+rename; AUDIT-1 P1-2); a single failure skips only that file
            try:
                from aimail_base import save_agent_config as _sac
                _sac(d.get("agent_id", ""), d, sid)
                changed = True
            except Exception as e:  # noqa: BLE001
                _warn(f"{ajx.parent.name}: agentmail.json write failed ({type(e).__name__}: {e}) -> skipping that file")
    if not changed:
        _ok("agentmail.json ok (fields complete and webhook_url aligned)")
    return changed


def _repair_routes_entries(sid: str) -> bool:
    """Missing route entries -> refresh via `bridge --system-id` (generated on the bridge side)."""
    sysdir = SYSTEMS_DIR / sid
    if not sysdir.is_dir() or not ROUTES_FILE.exists():
        return False
    routes = {}
    for line in ROUTES_FILE.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            routes[k.strip().strip('"')] = v.strip().strip('"').strip(",")
    missing = []
    skipped = []
    try:
        subs = sorted(sysdir.iterdir())
    except OSError as e:
        _warn(f"system directory unreadable ({e}) -> skipping the routes-entry repair")
        return False
    for sub in subs:
        aj = sub / "agentmail.json"
        # per-file tolerance: an unreadable file (permissions/broken) only skips that file, never aborts the step (measured 2026-09-20)
        try:
            if not aj.is_file():
                continue
            d = json.loads(aj.read_text())
        except Exception as e:  # noqa: BLE001
            skipped.append(f"{sub.name}({type(e).__name__})")
            continue
        email = d.get("email", "")
        if email and email not in routes:
            missing.append(email)
    if skipped:
        _warn(f"routes-entry repair: skipped {len(skipped)} unreadable file(s): {', '.join(skipped)}")
    if not missing:
        _ok("routes entries ok")
        return False
    _warn(f"routes missing {missing} entr(ies) -> refreshed via bridge")
    return _refresh_routes(sid)



def _repair_pull_entry_key(sid: str) -> bool:
    """Align the bridge pull.systems admin_key with gateway.json (the authoritative source)."""
    gw_path = SYSTEMS_DIR / sid / "aimail_gateway.json"
    if not gw_path.is_file() or not BRIDGE_CFG.exists():
        return False
    gw = json.loads(gw_path.read_text())
    gk = gw.get("admin_key", "")
    if not gk:
        return False
    try:
        import tomllib
        with open(BRIDGE_CFG, "rb") as f:
            td = tomllib.load(f)
    except Exception:
        return False
    systems = (td.get("pull", {}) or {}).get("systems") or []
    entry = next((x for x in systems if x.get("system_id") == sid), None)
    if entry is None:
        # missing -> create it locally when the local authoritative values are complete (no server needed), reusing install's own writer
        url = gw.get("gateway_url", "") or gw.get("aimail_url", "")
        if not url:
            _warn("pull entry missing and gateway.json has no gateway_url -> cannot create it locally (fix the config first)")
            return False
        try:
            from deploy_bridge import write_bridge_config
            write_bridge_config(
                str(BRIDGE_CFG),
                str(td.get("mode") or "pull"),
                str(td.get("bind") or "127.0.0.1:38081"),
                url, gk, sid, api_key=str(gw.get("api_key", "") or ""))
        except Exception as e:  # noqa: BLE001
            _fail(f"pull entry creation failed: {type(e).__name__}: {e}")
            return False
        _ok("bridge pull entry missing -> created locally (aimail_url/admin_key/system_id from gateway.json)")
        return True
    if entry is None or entry.get("admin_key") == gk:
        return False
    raw = BRIDGE_CFG.read_text()
    # replace that entry's admin_key value exactly (within the entry line)
    import re as _re
    new_raw, n = _re.subn(
        r'(\{[^}]*system_id\s*=\s*"' + _re.escape(sid) + r'"[^}]*admin_key\s*=\s*")[^"]*(")',
        r'\g<1>' + gk + r'\g<2>',
        raw, count=1)
    if n == 0:
        # the field order may be reversed (admin_key before system_id)
        new_raw, n = _re.subn(
            r'(\{[^}]*admin_key\s*=\s*")[^"]*("[^}]*system_id\s*=\s*"' + _re.escape(sid) + r'")',
            r'\g<1>' + gk + r'\g<2>',
            raw, count=1)
    if n == 0:
        _warn("pull entry admin_key alignment failed (no format match) -- check aimail_bridge.toml by hand")
        return False
    BRIDGE_CFG.write_text(new_raw)
    try:
        os.chmod(BRIDGE_CFG, 0o600)  # credential file (AUDIT-1 F6)
    except OSError:
        pass
    _ok(f"pull entry admin_key aligned to gateway.json ({sid})")
    return True


# -- Repairability closure list (owner ruling 2026-09-20) ----------------------
# Every check dimension must be registered; no dimension is left unaccounted for:
#   kind="auto" reliably self-fixable on this machine (no server/host state needed) -> the ladder must cover it;
#               still FAILing after the re-check with its prerequisites met -> a **defect** (printed as D, exit code 1)
#   kind="hint" not reliably self-fixable (needs the server/host process/an administrator) -> repair only hints, never forces it;
#               a remaining hit after the re-check is normal (printed as H)
#   needs       the external prerequisite for self-repair; when unmet the step is "skipped with a reason" (not counted as a defect)
#   step        the ladder index covering that dimension (required for auto only)
REPAIRABILITY: dict[tuple[str, str], dict] = {
    ("config", "complete"):          {"kind": "auto", "step": 4},
    ("config", "gateway_json"):      {"kind": "auto", "step": 4},
    ("config", "system_home"):       {"kind": "auto", "step": 4},
    ("config", "pointer"):           {"kind": "auto", "step": 5},
    ("gateway", "config"):           {"kind": "auto", "step": 4},
    ("gateway", "smtp_port"):        {"kind": "auto", "step": 4},
    ("gateway", "health"):           {"kind": "hint",
                                      "why": "the target gateway is down/unreachable -> start or fix it on the host side"},
    ("gateway", "api_key"):          {"kind": "hint",
                                      "why": "admin key missing or invalid -> re-install (aimail install) or have an administrator issue one"},
    ("bridge", "process"):           {"kind": "auto", "step": 1},
    ("bridge", "config"):            {"kind": "auto", "step": 1},
    ("bridge", "config-complete"):   {"kind": "auto", "step": 1},
    ("bridge", "config_consistency"): {"kind": "auto", "step": 1},
    ("bridge", "config-mode"):       {"kind": "auto", "step": 1},
    ("bridge", "activity"):          {"kind": "auto", "step": 1},
    ("bridge", "self_health"):       {"kind": "auto", "step": 1},
    ("bridge", "pull_path"):         {"kind": "hint",
                                      "why": "bridge cannot poll the gateway pending API -> the gateway must be reachable (host side); a missing config entry is fixed by pull-entry"},
    ("bridge", "pull-entry"):        {"kind": "auto", "step": 9},
    ("bridge", "routes-entry"):      {"kind": "auto", "step": 8},
    ("bridge", "routes-target"):     {"kind": "hint",
                                      "why": "the target agent endpoint is down / the address is unreachable -> start that agent on the host side"},
    ("agent", "config"):             {"kind": "auto", "step": 7},
    ("agent", "config-json"):        {"kind": "auto", "step": 7},
    ("agent", "config-consistency"): {"kind": "auto", "step": 7},
    ("agent", "name_apikey"):        {"kind": "auto", "step": 7},
    ("agent", "config-complete"):    {"kind": "hint",
                                      "why": "fields such as system_name are authoritative server-side (no local source) -> re-register with aimail install, or let an administrator handle it"},
    ("agent", "pointer"):            {"kind": "auto", "step": 5},
    ("agent", "hook"):               {"kind": "auto", "step": 6, "needs": ["platform_sdk"]},
    ("agent", "skill"):              {"kind": "auto", "step": 6, "needs": ["platform_sdk"]},
    ("agent", "toolset"):            {"kind": "auto", "step": 6, "needs": ["platform_sdk"]},
    ("agent", "webhook"):            {"kind": "auto", "step": 3},
    ("agent", "discovery"):          {"kind": "hint",
                                      "why": "the platform has no record of this agent yet -> create/register it with the platform's own command"},
    ("agent", "register"):           {"kind": "hint",
                                      "why": "the address is not registered in the cloud -> aimail install / the registration chain, or an administrator"},
    ("agent", "session"):            {"kind": "hint",
                                      "why": "the agent session/endpoint is down -> start that agent on the host side"},
    ("runtime", "mcp-payload"):      {"kind": "auto", "step": 6},
    ("runtime", "board-resources"):  {"kind": "auto", "step": 6},
    ("runtime", "host-payload-refs"): {"kind": "auto", "step": 6},
    ("runtime", "platform-locatable"): {"kind": "auto", "step": 5},
    ("system", "id"):                {"kind": "auto", "step": 4},
}

_UNREGISTERED = {"kind": "hint",
                 "why": "unregistered check dimension -> treated as needing admin action (please report it to the maintainer)"}


def repairability(level: str, name: str) -> dict:
    """Look up a check dimension's repairability class (unregistered -> HINT, with an explicit note)."""
    return REPAIRABILITY.get((str(level or ""), str(name or "")), dict(_UNREGISTERED))


def classify_residual(residual: list) -> tuple:
    """Re-check leftovers -> (defects, hints).

    defects: still present although self-fixable here (a defect: read the log / change the code)
    hints:   need the server/host/an administrator (hints only, normal)
    """
    defects, hints = [], []
    for c in residual:
        info = repairability(c.get("level", ""), c.get("check", "") or c.get("name", ""))
        (defects if info.get("kind") == "auto" else hints).append((c, info))
    return defects, hints


def _repair_mcp_payload() -> bool:
    """Runtime mcp payload missing/stale -> idempotent local bundle install (no network needed).

    The verdict comes from the **payload state**, never from an exit code (avoids false alarms)."""
    import runtime_bundle as rb
    try:
        st = rb.payload_state("mcp")
    except Exception as e:  # noqa: BLE001
        _warn(f"mcp payload state unreadable, skipped: {e}")
        return False
    if not st.get("present"):
        _warn("mcp payload not installed -> idempotent install")
    elif not (st.get("missing") or st.get("stale")):
        _ok("mcp payload ok (complete and in step with the current version)")
        return False
    else:
        bad = list(st.get("missing") or []) + list(st.get("stale") or [])
        _warn(f"mcp payload missing/stale ({', '.join(bad)}) -> idempotent reinstall")
    try:
        rb.install("mcp", force=True)
    except Exception as e:  # noqa: BLE001
        _fail(f"mcp payload reinstall raised: {e}")
        return False
    try:
        st2 = rb.payload_state("mcp")
    except Exception:  # noqa: BLE001
        st2 = {}
    if st2.get("present") and not (st2.get("missing") or st2.get("stale")):
        _ok(f"mcp payload reinstalled (v{st2.get('version')})")
        return True
    _fail("mcp payload still incomplete after the reinstall (see the output above)")
    return False


def repair(sid: str, deep: bool = False, dry_run: bool = False, home: str = "") -> int:
    print(f"  repair system={sid}{' [dry-run]' if dry_run else ''}{' [deep]' if deep else ''}")
    deep_home = str(Path(home).expanduser()) if home else _auto_platform_home(sid)
    passed, checks = _run_check(sid)
    if not checks:
        _fail("check produced no output -- is this system_id valid?")
        return 1
    fails = [c for c in checks if not c.get("pass")]
    if passed:
        _ok("check is all green, nothing to repair")
        return 0
    for c in fails:
        _warn(f"check ✗ {c['level']}/{c['check']}: {c['detail'][:90]}")

    # Repair ladder: every step runs unconditionally (each is idempotent, repeating the repair gives the same result).
    # No conditional triggering on the check outcome -- check has blind spots around this machine's bridge
    # liveness (measured 2026-08-30: a dead bridge was reported as "remote ✓"), so the whole ladder runs
    # to cover the various failure modes.
    plan = [
        ("bridge alive (start it idempotently when dead)", lambda: _ensure_bridge_running()),
        ("refresh bridge routes (file + admin API hot reload)", lambda: _refresh_routes(sid)),
        ("repair the gateway webhook pairing (evidence-driven; --deep rewrites directly)",
         lambda: _repair_webhook_pairing(sid, deep=deep)),
        ("backfill the gateway config (system_home/webhook_host: fill gaps only)",
         lambda: _repair_gateway_config(sid, args_home=deep_home)),
        ("recreate the platform pointer (only when the platform root is certain and the pointer is missing)",
         lambda: _repair_pointer(sid, deep_home)),
        ("redeploy runtime resources (patch marker/resources missing -> idempotent reinstall)",
         lambda: _repair_runtime_resources(sid, deep_home)),
        ("refresh the runtime payload (mcp missing/stale -> idempotent local bundle install)",
         lambda: _repair_mcp_payload()),
        ("fill gaps in agentmail.json + align webhook_url with the live route",
         lambda: _repair_agentmail_json(sid)),
        ("complete the bridge routes entries (missing -> refresh)",
         lambda: _repair_routes_entries(sid)),
        ("align the bridge pull entry admin_key with gateway.json",
         lambda: _repair_pull_entry_key(sid)),
    ]
    if deep:
        plan.append(("drain stuck pending (--deep)", lambda: _drain_stuck(sid)))

    if dry_run:
        print("  [dry-run] plan:")
        for desc, _fn in plan:
            print(f"    - {desc}")
        return 0

    for desc, fn in plan:
        print(f"\n  ── {desc} ──")
        try:
            fn()
        except Exception as e:
            _fail(f"repair action raised: {e}")

    # re-check
    print("\n  -- re-check --")
    passed2, checks2 = _run_check(sid)
    still = [c for c in checks2 if not c.get("pass")]
    if passed2:
        _ok("re-check is all green")
        return 0
    defects, hints = classify_residual(still)
    for c, info in defects:
        _warn(f"[D locally fixable, still failing] {c['level']}/{c['check']}: {c['detail'][:90]}")
    for c, info in hints:
        _warn(f"[H needs admin/host action] {c['level']}/{c['check']}: {c['detail'][:80]}")
        if info.get("why"):
            _warn(f"    → {info['why']}")
    _warn(f"re-check not all green: {len(defects)} locally-fixable defect(s) / {len(hints)} needing admin action")
    if defects:
        _warn("-> locally-fixable items are still failing (defects): report them to the maintainer with the log above")
    else:
        _warn("-> the remaining items are not reliably self-fixable (repair only hints; a system administrator acts)")
    return 1


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Chain self-repair (check ✗ -> repair via the shared function chain -> re-check)")
    ap.add_argument("--system-id", required=False, default="")
    ap.add_argument("--home", help="platform home (auto-resolved on a single-pointer machine)")
    ap.add_argument("--deep", action="store_true",
                    help="deep repair: rewrite the webhook pairing from agentmail.json without evidence + drain stuck pending")
    ap.add_argument("--dry-run", action="store_true", help="list the repairs without writing any state")
    args = ap.parse_args()

    sid = args.system_id
    if not sid:
        # Authoritative resolution: reuse the aimail CLI's resolve_system_id directly (no extension ->
        # an explicit SourceFileLoader; only the function is taken, the CLI main is guarded by __main__)
        from importlib.machinery import SourceFileLoader
        loader = SourceFileLoader("aimail_cli", str(SCRIPTS_DIR / "aimail"))
        import importlib.util
        spec = importlib.util.spec_from_loader("aimail_cli", loader)
        if spec is None:
            print("  cannot load the aimail CLI module (system_id resolution failed)")
            return 1
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        sid, _platform = mod.resolve_system_id(
            Path(args.home).expanduser() if args.home else Path(), "")
    if not sid:
        print("  cannot determine system_id (--system-id)")
        return 1
    return repair(sid, deep=args.deep, dry_run=args.dry_run, home=args.home or "")


if __name__ == "__main__":
    sys.exit(main() or 0)

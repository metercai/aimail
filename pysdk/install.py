#!/usr/bin/env python3
"""aimail.install — SDK self-contained install/uninstall entry point (process command contract).

Architecture (2026-09-02 finalized): CLI is an ops tool, does not carry resources/patches; each SDK
(pysdk / tssdk platform package) carries its own resources and platform patches, and provides a spawnable install
Entry —— CLI (or user) only needs to execute:

    python -m aimail.install install            --type hermes   [--home ~/.hermes] [--system-id SID]
    python -m aimail.install install            --type deerflow [--home <backend>] [--system-id SID]
    python -m aimail.install uninstall          --type hermes   [--home ~/.hermes] [--system-id SID]
    python -m aimail.install check-env          --type hermes   [--home ...]
    python -m aimail.install register-profiles  --home <hermes-root> [--system-id SID]  # internal

All actions are idempotent; if environment self-check fails, clearly prompt "run aimail CLI first".
"""
from __future__ import annotations

import sys


def _say(*a, **k):
    """SDK progress/diagnostic output always goes to stderr: caller (CLI) uses stdout as single-line JSON protocol."""
    print(*a, file=sys.stderr, **k)

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── Dual-form bootstrap: this file is located in the core directory (pysdk/ or site-packages/aimail/) ──
# flat core (aimail_base.py…) and hermes/ deer-flow/ submodules are all in this directory.
_CORE = os.path.dirname(os.path.abspath(__file__))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

# ── Host registration form single source of truth (dual form: repo pysdk/ on sys.path / pip aimail package)────────
def _host_registry():
    try:
        import hosts.registry as _hr  # repo form: pysdk/ already in sys.path
        return _hr
    except ImportError:
        from aimail.hosts import registry as _hr  # pip form
        return _hr


_HOSTS = _host_registry()
PYTHON_HOST_IDS = tuple(_HOSTS.host_ids())  # hermes / deerflow
# node host (dsh/pi/openclaw) assembly is in the node entry of the tssdk package agent-aimail,
# not in this entry; among them **dsh** is the original --type value domain (kept accepted and loudly errors, CLI side parameters consistent),
# pi/openclaw were never in the pysdk value domain, no new additions.
NODE_HOST_IDS = ("dsh", "pi", "openclaw")
INSTALL_NODE_CHOICES = ("dsh",)

# owner 2026-10-07: adapter subdirectories (hermes/ deer-flow/) **have no __init__.py** ✗ ⇒ neither in-package modules ✓,
# nor top-level names ✗ ⇒ must attach each subdirectory itself to sys.path (following the flat module convention ✓), otherwise `import aimail_hermes` will definitely fail ✓.
# 2026-10-21: Directory listing is derived from the hosts registry (single source of truth; the dead dsh entry in the old hardcoded list has been removed — dsh is a node host).
for _sub in ((_HOSTS.get_host(h) or {})["adapter_dir"] for h in _HOSTS.host_ids()):
    _p = os.path.join(_CORE, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from _resources_release import agentmail_home, release_all_systems, resources_board_dir, resources_skills_dir  # noqa: E402


def _import_host(host_id: str, name: str):
    """import host adaptation submodule —— same-origin priority: first take the one from the same tree as this install.py
    `<adapter_dir>/<name>.py` (`_CORE` already inserted at sys.path head), then fallback to package name
    `aimail.<adapter_dir>.<name>`。

    2026-09-30 P2 forensics (hermes original text): original "package name priority" in CLI bootstrap snapshotting source code to
    `~/.aimail/bin/aimail-src/pysdk/` + mirror with pip installing old package form retrieves
    **Another tree**'s old implementation ⇒ mixed loading blows up directly. The caller and callee must be in the same tree;
    In pip form, both paths point to the same file, so reordering has no side effects.
    """
    spec = _HOSTS.get_host(host_id)
    if spec is None:
        raise ValueError(f"unknown host {host_id!r}")
    adapter = spec["adapter_dir"]
    try:
        return __import__(f"{adapter}.{name}", fromlist=["*"])
    except ImportError:
        return __import__(f"aimail.{adapter}.{name}", fromlist=["*"])


def _import_hermes(name: str):
    return _import_host("hermes", name)


def _import_deerflow(name: str):
    return _import_host("deerflow", name)


# ═══════════════════════════════════════════════════════════════
# Environment self-check (pre-load/install: if configuration is missing → clear guidance to run CLI first)
# ═══════════════════════════════════════════════════════════════

def _machine_state() -> tuple:
    """Machine environment three states:
    ('empty', msg)   — no system configuration in ~/.aimail/systems → init + install
    ('systems', msg) — system config exists (install can reuse/activate)
    """
    systems_root = os.path.join(agentmail_home(), "systems")
    if not os.path.isdir(systems_root) or not os.listdir(systems_root):
        return ("empty",
                "No system configuration found (~/.aimail/systems is empty).\n\n"
                "  Run:   aimail install --home <host root> (activate system and bind)")
    return ("systems", "")


def env_check_hermes(hermes_dir: str) -> int:
    """Hermes self-check: webhook.py/profiles.py exist, pip aimail reachable."""
    state, hint = _machine_state()
    problems = []
    ha = os.path.join(hermes_dir, "hermes-agent")
    if not os.path.isdir(ha):
        _say("[env-check] ✗ hermes-agent not installed (hermes host missing or --home points to wrong location)")
        _say(f"  Install hermes host first, then: aimail install --home {hermes_dir}")
        return 1
    webhook_py = os.path.join(ha, "gateway", "platforms", "webhook.py")
    profiles_py = os.path.join(ha, "hermes_cli", "profiles.py")
    if not os.path.isfile(webhook_py):
        problems.append(f"webhook.py missing: {webhook_py}")
    if not os.path.isfile(profiles_py):
        if not os.path.isfile(os.path.join(ha, "cli", "profiles.py")):
            problems.append("profiles.py missing")
    if state == "empty":
        for p in problems:
            _say(f"[env-check] ✗ {p}")
        _say(f"[env-check] ✗ {hint}")
        return 1
    # Configuration binding: whether system directory/pointer has been established by CLI (--home parameterized, AUDIT-1 P2-1)
    ptr = os.path.join(hermes_dir, ".agentmail")
    if not os.path.isfile(ptr):
        problems.append(
            f"Binding configuration not found {hermes_dir}/.agentmail\n"
            f"  Run first: aimail install --home {hermes_dir} (activate and bind)")
    for p in problems:
        _say(f"[env-check] ✗ {p}")
    if problems:
        _say("[env-check] Missing items require aimail CLI to complete environment configuration first")
        return 1
    _say("[env-check] hermes: OK")
    return 0


def env_check_deerflow(backend_dir: str) -> int:
    state, hint = _machine_state()
    app_py = os.path.join(backend_dir, "app", "gateway", "app.py")
    if not os.path.isfile(app_py):
        _say(
            f"[env-check] ✗ deer-flow entry not found {app_py} (--home should be backend directory)")
        if state == "empty":
            _say(f"[env-check] ✗ {hint}")
        else:
            _say("  Run first: aimail install (configure environment)")
        return 1
    if state == "empty":
        _say(f"[env-check] ✗ {hint}")
        return 1
    _say("[env-check] deerflow: OK")
    return 0


# ═══════════════════════════════════════════════════════════════
# install
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# P2 critical anchor hard gate (owner 2026-09-30)
# ═══════════════════════════════════════════════════════════════
def hermes_webhook_anchor_gate(ph, webhook_py: str) -> int:
    """Post-patch critical anchor health check: if any block is missing ⇒ rc=1 and name the missing block; all anchors present ⇒ rc=0.

    the anchor is **written in** by patch_webhook ⇒ it must be measured after patch_webhook; looking only at its
    return value is not enough —— return value only says "whether file was written" (idempotent rerun always False), whether anchors are present is the hard gate.
    """
    gaps = list(ph.webhook_patch_gaps(webhook_py))
    if gaps:
        _say(f"  ✗ webhook critical anchor missing: {', '.join(gaps)} — {webhook_py}")
        _say("      (if any block is missing ⇒ preprocessor hook/adapter cannot attach, this patch is not considered applied;"
              "host webhook.py version does not match anchor points, fix anchors and rerun install)")
        return 1
    return 0


def install_hermes(hermes_dir: str, system_id: str = "") -> int:
    """Hermes platform self-sufficient install: pip runtime already installed (this command comes from pip agent-aimail);
    webhook/profiles/toolsets patches + profile registration + board resource expansion."""
    ha = os.path.join(hermes_dir, "hermes-agent")
    webhook_py = os.path.join(ha, "gateway", "platforms", "webhook.py")
    profiles_py = os.path.join(ha, "hermes_cli", "profiles.py")
    if not os.path.isfile(profiles_py):
        alt = os.path.join(ha, "cli", "profiles.py")
        if os.path.isfile(alt):
            profiles_py = alt
    rc = 0

    ph = _import_hermes("patch_webhook")
    if os.path.isfile(webhook_py):
        changed = ph.patch_webhook(webhook_py)
        # P2 (owner 2026-09-30): if any critical anchor block is missing ⇒ rc≠0 and name the missing block; all anchors present (including idempotent
        # "already applied") ⇒ rc=0 —— hard gate see hermes_webhook_anchor_gate().
        if hermes_webhook_anchor_gate(ph, webhook_py):
            rc = 1
        else:
            _say(f"  hermes webhook patch: {'applied' if changed else 'already clean'}")
    else:
        _say(f"  ✗ webhook.py missing: {webhook_py} (--home should be hermes root)")
        rc = 1

    pp = _import_hermes("patch_profiles")
    if os.path.isfile(profiles_py):
        changed = pp.patch_profiles(profiles_py)
        _say(f"  hermes profiles patch: {'applied' if changed else 'already clean'}")
    else:
        _say(f"  ✗ profiles.py missing: {profiles_py}")
        rc = 1

    try:
        pt = _import_hermes("toolsets")
        changed = pt.patch_toolsets(ha)
        _say(f"  hermes toolsets: {'registered' if changed else 'already registered'}")
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py failure path raises SystemExit
        _say(f"  ✗ toolsets patch failed: {e}")
        rc = 1

    # profile registration (reads env: HERMES_HOME/SYSTEM_ID/HERMES_PROFILES_DIR) —— direct
    # function call (Rust CLI final state goes through spawn python -m aimail.install register-profiles,
    # same env contract; repo form has no aimail package name, cannot spawn)
    try:
        rp = _import_hermes("register_profiles")
        saved = {k: os.environ.get(k) for k in ("HERMES_HOME", "HERMES_PROFILES_DIR", "SYSTEM_ID")}
        os.environ["HERMES_HOME"] = hermes_dir
        os.environ["HERMES_PROFILES_DIR"] = os.path.join(hermes_dir, "profiles")
        if system_id:
            os.environ["SYSTEM_ID"] = system_id
        try:
            rc = rp.register_emails() or rc
        finally:
            for k, v in saved.items():  # restore only the keys we touched
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py failure path raises SystemExit
        _say(f"  ✗ register profiles failed: {e}")
        rc = 1

    # board resource expansion (idempotent; covers all existing system directories)
    rel = release_all_systems(resources_board_dir())
    for r in rel:
        _say(f"  resources: {r['board_dir']} (copied {r['copied']}, kept {r['skipped']})")

    # skills expansion (SKILL.md/DESCRIPTION.md → skills/agentmail for each hermes profile)
    _release_hermes_skills(hermes_dir)

    _say("  hermes install done. Restart hermes gateway to make patches take effect (aimail bridge restart or host restart)")
    return rc


def _release_hermes_skills(hermes_dir: str) -> int:
    """SKILL.md + DESCRIPTION.md → {home}/profiles/*/skills/agentmail/ (idempotent)."""
    skills_src = resources_skills_dir()
    if not os.path.isdir(skills_src):
        # No longer silently return 0: missing package-bundled resources = packaging defect, must be visible on the spot
        raise RuntimeError(
            f"skills resources missing: {skills_src} (repo state: resources are in repo root resources/, check checkout integrity;"
            f" pip state: reinstall agent-aimail)")
    missing = [f for f in ("SKILL.md", "DESCRIPTION.md")
               if not os.path.isfile(os.path.join(skills_src, f))]
    if missing:
        raise RuntimeError(f"skills resources incomplete: {skills_src} missing {missing}")
    profiles_root = os.path.join(hermes_dir, "profiles")
    targets = [hermes_dir]  # default profile root
    if os.path.isdir(profiles_root):
        targets += [os.path.join(profiles_root, d) for d in sorted(os.listdir(profiles_root))
                    if os.path.isdir(os.path.join(profiles_root, d))]
    n = 0
    for prof in targets:
        dst_dir = os.path.join(prof, "skills", "agentmail")
        for fname in ("SKILL.md", "DESCRIPTION.md"):
            src = os.path.join(skills_src, fname)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(dst_dir, fname)
            if os.path.exists(dst):
                try:
                    same = open(dst, "rb").read() == open(src, "rb").read()
                except Exception:  # noqa: BLE001
                    same = False
                if same:
                    continue
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(src, dst)
            n += 1
    if n:
        _say(f"  hermes skills: {n} file(s) → profiles/*/skills/agentmail")
    return n


def _deerflow_backend(root: str) -> str:
    """backend directory normalization: repo root contains backend/ (standard layout) → backend; otherwise treated as already backend."""
    r = os.path.expanduser(root)
    return os.path.join(r, "backend") if os.path.isdir(os.path.join(r, "backend")) else r


def _assembly_env(system_id: str = "", extra_env: dict | None = None) -> dict:
    """Spawn environment for the assembly steps (install-skill.sh / install-mcp.sh).

    Both scripts follow the contract via the CLI public command surface (missing `aimail` ⇒ exit 1, no fallback), while in repo/container
    forms the CLI is often not in PATH (`command -v aimail` inside the image is always empty) ⇒ here we prepend the `cli/` of the **same tree**
    `cli/` appended to the head of PATH; pip layout does not have this directory ⇒ keep the environment PATH (when the CLI is truly missing,
    The script fails loudly on its own — no fallback, no silence).
    """
    env = dict(os.environ)
    if shutil.which("aimail", path=env.get("PATH")) is None:
        cli_dir = os.path.abspath(os.path.join(_CORE, os.pardir, "cli"))
        if os.path.isfile(os.path.join(cli_dir, "aimail")):
            env["PATH"] = cli_dir + os.pathsep + env.get("PATH", "")
    if system_id:
        env.setdefault("AIMAIL_SYSTEM_ID", system_id)
    for k, v in (extra_env or {}).items():
        env.setdefault(k, v)
    return env


def _assemble_deerflow(home_root: str, system_id: str = "") -> int:
    """deerflow agent-side assembly: skills landing point + MCP toolset landing point (idempotent).

    **Reuses the product's existing scripts** `pysdk/deer-flow/install-skill.sh` / `install-mcp.sh`
    parsing and writing logic —— SDK does not maintain a second set of writing; the two scripts themselves are idempotent (skip/overwrite same content),
    repeated install does not produce duplicate writes.
    `DEER_FLOW_PROJECT_ROOT` is only filled with the platform home when not declared in the environment (following install-mcp.sh
    landing point order ③ "ops has asserted project root ⇒ create new within it"), making the landing point a location that deer-flow
    `resolve_config_path()` **and** `get_skills_path()` really read from ——
    otherwise writing to a state directory/session that is not read is equivalent to not writing (2026-09-30: skills landing point error was this).
    Any script rc!=0 ⇒ return 1 (assembly failure must be visible, no silent degradation).
    """
    sdk = os.path.join(_CORE, "deer-flow")
    # Both steps get the **same** env: skills and MCP landing points must both be the deer-flow project_root
    # project_root(get_skills_path ③ / resolve_config_path ③)—— one less step passed =
    # that step writes to the state directory = session cannot read it (2026-09-30 measured skills root cause).
    asm_env = _assembly_env(system_id, {"DEER_FLOW_PROJECT_ROOT": home_root})
    steps = (
        ("install-skill.sh", asm_env),
        ("install-mcp.sh", asm_env),
    )
    rc = 0
    for name, env in steps:
        path = os.path.join(sdk, name)
        if not os.path.isfile(path):
            _say(f"  ✗ deerflow assembly script missing: {path} (packaging/materialization defect, no silent skip)")
            rc = 1
            continue
        r = subprocess.call(["bash", path], env=env, stdout=sys.stderr)
        if r != 0:
            _say(f"  ✗ deerflow {name} assembly failed (exit {r})")
            rc = 1
        else:
            _say(f"  deerflow {name}: assembled")
    return rc


def install_deerflow(backend_dir: str, system_id: str = "", manager: str = "") -> int:
    """DeerFlow platform self-sufficient install: app.py patch + runtime bundle + agent-side assembly + registration/reconciliation.
    Parameter is the repo root or backend (backend normalization is handled inside the SDK — platform layout is adaptation knowledge)."""
    home_root = os.path.expanduser(backend_dir)  # platform home (used for assembly landing point order ③)
    backend_dir = _deerflow_backend(backend_dir)
    md = _import_deerflow("manage")
    rc = 0
    try:
        changed = md.patch_backend_app(backend_dir)
        _say(f"  deerflow app.py patch: {'applied' if changed else 'already clean'}")
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py failure path raises SystemExit
        _say(f"  ✗ app.py patch failed: {e}")
        rc = 1
    try:
        n = md.install_bundle(backend_dir)
        _say(f"  deerflow bundle: {n} file(s) installed")
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py failure path raises SystemExit
        _say(f"  ✗ bundle install failed: {e}")
        rc = 1
    # agent-side assembly (card ①(i), owner batch 2026-09-30): skills + MCP toolset landing points.
    # Pure SDK entry originally only did patch/bundle/register ⇒ when harness only calls SDK entry
    # toolset is always missing; here it is merged into the install chain (reusing existing scripts, see _assemble_deerflow).
    if _assemble_deerflow(home_root, system_id=system_id):
        rc = 1
    # registration (address + routing); idempotent
    try:
        if manager:
            md.register_agents(manager=manager, system_id=system_id, agent="all")
        else:
            md.reconcile(system_id=system_id)
        _say("  deerflow agents registered/reconciled")
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py failure path raises SystemExit
        _say(f"  ✗ register/reconcile failed: {e}")
        rc = 1
    rel = release_all_systems(resources_board_dir())
    for r in rel:
        _say(f"  resources: {r['board_dir']} (copied {r['copied']})")
    return rc


# ═══════════════════════════════════════════════════════════════
# uninstall (symmetric with install: undo patches applied + cleanup)
# ═══════════════════════════════════════════════════════════════


def uninstall_hermes(hermes_dir: str, system_id: str = "") -> int:
    ha = os.path.join(hermes_dir, "hermes-agent")
    rc = 0
    # git form: precise restoration (gateway is a git checkout, and when there are no other local changes)
    if os.path.isdir(os.path.join(ha, ".git")):
        try:
            out = subprocess.check_output(
                ["git", "-C", str(ha), "status", "--short"], text=True, timeout=10)
            modified = [l[3:].strip() for l in out.splitlines() if l.startswith(" M")]
            allowed = {"gateway/platforms/webhook.py", "toolsets.py",
                       "hermes_cli/profiles.py", "cli/profiles.py"}
            if all(m in allowed for m in modified):
                for f in modified:
                    subprocess.call(["git", "-C", str(ha), "checkout", "--", f], stdout=sys.stderr)
                    _say(f"  ✓ reverted {f} (git)")
            else:
                _say("  ⚠ hermes-agent has additional uncommitted changes——skipping git restore, please check aimail traces")
        except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py failure path raises SystemExit
            _say(f"  git revert failed: {e}")
    else:
        # non-git → exact-text undo (matches patch insertion verbatim)
        pw = _import_hermes("patch_webhook")
        pp = _import_hermes("patch_profiles")
        try:
            pt = _import_hermes("toolsets")
        except Exception:  # noqa: BLE001
            pt = None
        # Same path detection logic as install: profiles.py may be in hermes_cli/ or cli/ (AUDIT-1 P2-3)
        profiles_rel = "hermes_cli/profiles.py"
        alt = os.path.join(ha, "cli", "profiles.py")
        if not os.path.exists(os.path.join(ha, profiles_rel)) and os.path.exists(alt):
            profiles_rel = "cli/profiles.py"
        for rel, fn in (
            ("gateway/platforms/webhook.py", pw.unpatch_webhook),
            (profiles_rel, pp.unpatch_profiles),
        ):
            fp = os.path.join(ha, rel)
            if os.path.exists(fp):
                fn(Path(fp))
        if pt is not None:
            for rel in ("toolsets.py",):
                fp = os.path.join(ha, rel)
                if os.path.exists(fp):
                    pt.unpatch_toolsets(Path(fp))
        # clean pyc
        for cache in ("gateway/platforms/__pycache__", "hermes_cli/__pycache__",
                      "cli/__pycache__", "tools/__pycache__"):
            cd = os.path.join(ha, cache)
            if os.path.isdir(cd):
                shutil.rmtree(cd, ignore_errors=True)
    # remove old tools/ copy (if any)
    for rel in ("tools/aimail_tools.py", "tools/aimail_base.py",
                "tools/aimail_board.py", "tools/hermes"):
        tgt = os.path.join(ha, rel)
        if os.path.exists(tgt):
            shutil.rmtree(tgt) if os.path.isdir(tgt) else os.unlink(tgt)
            _say(f"  ✓ removed {rel}")
    # This SDK process is pip aimail — it does not uninstall itself (host venv management is decided by the CLI)
    # profile-level state revocation (symmetric with install's registration chain/skills publishing — previously by CLI
    # _uninstall_hermes, platform boundary closure migrated to SDK):
    #   pointers (sid match)/skills/agentmail directory/config.yaml toolsets entries/
    #   webhook_subscriptions routing (including root profile=hermes_dir, install's
    #   release targets also include the root — symmetric)
    _uninstall_hermes_profiles(hermes_dir, system_id)
    _say("  hermes uninstall done (local config/gateway-side cleanup is handled by aimail CLI)")
    return rc


def _uninstall_hermes_profiles(hermes_dir: str, system_id: str) -> None:
    """Revoke aimail state for each profile (including root): pointer/skills/toolsets entries/routes."""
    targets = [hermes_dir]
    profiles_root = os.path.join(hermes_dir, "profiles")
    if os.path.isdir(profiles_root):
        targets += [os.path.join(profiles_root, d) for d in sorted(os.listdir(profiles_root))
                    if os.path.isdir(os.path.join(profiles_root, d))]
    for prof in targets:
        ptr = os.path.join(prof, ".agentmail")
        if os.path.isfile(ptr):
            try:
                with open(ptr) as f:
                    data = json.load(f)
                if data.get("system_id") == system_id:
                    os.unlink(ptr)
                    _say(f"  ✓ removed pointer {ptr}")
            except Exception:  # noqa: BLE001
                pass
        sk = os.path.join(prof, "skills", "agentmail")
        if os.path.isdir(sk):
            shutil.rmtree(sk, ignore_errors=True)
            _say(f"  ✓ removed skill {sk}")
        # config.yaml:platform_toolsets remove agentmail entry (final state single name)
        cfg = os.path.join(prof, "config.yaml")
        if os.path.isfile(cfg):
            try:
                with open(cfg) as f:
                    content = f.read()
                new = re.sub(r"^[ \t]*-[ \t]*agentmail[ \t]*\r?$\n?", "", content, flags=re.M)
                if new != content:
                    with open(cfg, "w") as f:
                        f.write(new)
                    _say(f"  ✓ config toolset cleaned {cfg}")
            except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py failure path raises SystemExit
                _say(f"  ⚠ config.yaml clean failed: {e}")
        # webhook subscription routing (aimail-inbound, final state single name)
        subs = os.path.join(prof, "webhook_subscriptions.json")
        if os.path.isfile(subs):
            try:
                with open(subs) as f:
                    data = json.load(f)
                removed = [rn for rn in ("aimail-inbound",)
                           if rn in (data if isinstance(data, dict) else {})]
                if removed:
                    for rn in removed:
                        del data[rn]
                    with open(subs, "w") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    _say(f"  ✓ webhook route removed {subs}")
            except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py failure path raises SystemExit
                _say(f"  ⚠ webhook_subscriptions clean failed: {e}")


def uninstall_deerflow(backend_dir: str, system_id: str = "") -> int:
    """DeerFlow SDK uninstall: restore app.py patch + delete runtime bundle.
    Parameter is repo root or backend (normalized within SDK).

    system_id: passed in by registry-driven call. registry's sdk_uninstall step calls by
    `fn(home, sid)` position (see cli/aimail `_sdk_uninstall`), aligned with
    `uninstall_hermes(hermes_dir, system_id="")`; this function does not consume it
    (deer-flow runtime artifacts all land in backend directory, not partitioned by sid). F9 2026-09-25:
    The original signature took only 1 positional argument ⇒ `aimail uninstall` raised TypeError on deerflow,
    gateway-side deregistration entire segment was skipped, fixture zero-residue gate judged red."""
    backend_dir = _deerflow_backend(backend_dir)
    md = _import_deerflow("manage")
    rc = 0
    # First restore app.py (otherwise after deleting bundle, host restart import fails — AUDIT-1 P1-4)
    try:
        md.unpatch_backend_app(backend_dir)
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py failure path raises SystemExit
        _say(f"  ✗ app.py unpatch failed: {e}")
        rc = 1
    bundle_dir = os.path.join(backend_dir, "routers", "aimail")
    if os.path.isdir(bundle_dir):
        shutil.rmtree(bundle_dir, ignore_errors=True)
        _say(f"  ✓ removed bundle {bundle_dir}")
    if rc == 0:
        _say("  deerflow uninstall done")
    return rc


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def _cmd_register_profiles(env) -> int:
    rp = _import_hermes("register_profiles")
    old = dict(os.environ)
    os.environ.update({k: v for k, v in env.items() if v is not None})
    rc = 0
    try:
        rc = rp.register_emails() or 0
    finally:
        os.environ.clear()
        os.environ.update(old)
    return rc


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aimail.install", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    # --type value domain derived from hosts registry (single source of truth): python host + dsh (accept then loud error,
    # keep CLI surface parameter consistent; dsh assembly belongs to tssdk package).
    p_ins = sub.add_parser("install", help="Install (idempotent)")
    p_ins.add_argument("--type", choices=list(PYTHON_HOST_IDS) + list(INSTALL_NODE_CHOICES), required=True)
    p_ins.add_argument("--home", default="", help="Host root: hermes=~/.hermes; deerflow=backend directory; dsh=DSH home")
    p_ins.add_argument("--system-id", default="")
    p_ins.add_argument("--manager", default="")
    p_ins.set_defaults(fn=_run_install)

    p_uni = sub.add_parser("uninstall", help="Uninstall (undo own patch)")
    p_uni.add_argument("--type", choices=list(PYTHON_HOST_IDS), required=True)
    p_uni.add_argument("--home", default="")
    p_uni.add_argument("--system-id", default="")
    p_uni.set_defaults(fn=_run_uninstall)

    p_chk = sub.add_parser("check-env", help="Environment self-check")
    p_chk.add_argument("--type", choices=list(PYTHON_HOST_IDS), required=True)
    p_chk.add_argument("--home", default="")
    p_chk.set_defaults(fn=_run_check)

    p_rp = sub.add_parser("register-profiles", help="(internal) Hermes profile registration")
    p_rp.add_argument("--home", default="")
    p_rp.add_argument("--system-id", default="")
    p_rp.set_defaults(fn=_run_regprof)

    args = ap.parse_args(argv)
    return args.fn(args)


def _run_install(args) -> int:
    home = args.home or os.environ.get("AIMAIL_SYSTEM_HOME", "")
    if not home:
        _say("✗ install requires --home (the host root dir)")
        return 1
    # Supported set = registry keys (single source of truth); not in set ⇒ loud error (node host assembly belongs to tssdk package).
    if _HOSTS.get_host(args.type) is None:
        _say(
            f"✗ {args.type}: python side only assembles {', '.join(PYTHON_HOST_IDS)}"
            "(node host assembled by tssdk package agent-aimail)"
        )
        return 1
    if args.type == "hermes":
        return install_hermes(home, args.system_id)
    return install_deerflow(home, args.system_id, args.manager)


def _run_uninstall(args) -> int:
    home = args.home or os.environ.get("AIMAIL_SYSTEM_HOME", "")
    if not home:
        _say("✗ uninstall requires --home")
        return 1
    if args.type == "hermes":
        return uninstall_hermes(home, args.system_id)
    return uninstall_deerflow(home)


def _run_check(args) -> int:
    home = args.home or os.environ.get("AIMAIL_SYSTEM_HOME", "")
    if not home:
        _say("✗ check-env requires --home")
        return 1
    if args.type == "hermes":
        return env_check_hermes(home)
    return env_check_deerflow(home)


def _run_regprof(args) -> int:
    env = dict(os.environ)
    env.setdefault("HERMES_HOME", args.home or "")
    if args.system_id:
        env["SYSTEM_ID"] = args.system_id
    return _cmd_register_profiles(env)


if __name__ == "__main__":
    sys.exit(main())

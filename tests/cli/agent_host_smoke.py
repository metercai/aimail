#!/usr/bin/env python3
"""Manual smoke: what would the address-level bootstrap do on the agent hosts that
are installed on THIS machine?

    python3 tests/cli/agent_host_smoke.py                  # read-only: detect + native plan
    python3 tests/cli/agent_host_smoke.py --json out.json  # same, machine-readable

    # directory-based hosts (an unknown host that loads plugins from a dir):
    python3 tests/cli/agent_host_smoke.py --answers-dir ./answers --apply [--with-reload] [--keep]

Why not in L0: it needs real platforms and a real $HOME. L0 runs on a clean runner
where none of them exist.

Two install mechanisms exist, and the script keeps them apart on purpose:

  package-based  hermes / dsh / openclaw / pi / deerflow install an adapter PACKAGE
                 through their own package manager (the platform table in
                 `aimail platform spec` holds the exact command). Nothing is written
                 into a plugin directory here — writing files would be the wrong
                 mechanism for these hosts.

  directory-based  a host that loads plugins from a directory: `aimail agent doctor`
                 profiles it, and `setup --apply` renders our node/python adapter
                 into <plugins>/aimail-<host>/ plus the skill.

The script never restarts a live host unless you pass --with-reload, and never
changes a package-based host at all (it prints the command instead).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

RUNTIME = {"hermes": "python", "deerflow": "python"}
# package each adapted host installs (native mechanism, not our generated adapter)
ADAPTER_PKG = {
    "hermes": "agent-aimail",
    "deerflow": "agent-aimail",
    "dsh": "agent-aimail",
    "openclaw": "agent-aimail",
    "pi": "agent-aimail",
}


def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def platform_spec(cli: str) -> dict:
    p = run([cli, "platform", "spec", "--json"])
    if p.returncode != 0:
        raise SystemExit("cannot read platform spec: " + (p.stderr or p.stdout))
    return json.loads(p.stdout)


def native_install(platform: dict) -> str:
    """First spawn/sdk_install step = the host's own install command."""
    for step in platform.get("install_steps", []):
        argv = step.get("argv") or ([step["cmd"]] if step.get("cmd") else [])
        if argv:
            return " ".join(argv)
        if step.get("kind") == "sdk_install":
            return step.get("text", "sdk_install")
    return ""


def restart_hint(platform: dict) -> str:
    blob = json.dumps(platform, ensure_ascii=False)
    m = re.search(r'"text":\s*"([^"]*(?:restart|reload)[^"]*)"', blob, re.I)
    return m.group(1) if m else ""


def find_pkg(pkg: str, roots: list[Path]) -> str:
    for root in roots:
        if not root.is_dir():
            continue
        cand = root / pkg
        if cand.is_dir():
            return str(cand)
        for hit in root.glob(f"**/{pkg}/package.json"):
            return str(hit.parent)
    return ""


def npm_global_root() -> Path | None:
    p = run(["npm", "root", "-g"])
    return Path(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None


def probe_dir_host(host: str, home: Path) -> dict:
    pdir = next((home / c for c in ("plugins", "extensions", "addons") if (home / c).is_dir()), None)
    sdir = next((home / c for c in ("skills", "skill", "agent/skills") if (home / c).is_dir()), None)
    if not pdir or not sdir:
        return {}
    sample = None
    for f in sorted(pdir.rglob("*")):
        if f.is_file() and f.suffix in (".js", ".mjs", ".cjs", ".py", ".ts"):
            try:
                if "register" in f.read_text(errors="ignore")[:8000]:
                    sample = f
                    break
            except OSError:
                continue
    if sample is None:
        return {}
    return {
        "host": {"name": host, "version": "unknown", "runtime": RUNTIME.get(host, "node")},
        "plugin": {"dir": str(pdir)},
        "reload": {"mode": "command", "cmd": ""},
        "skills": {"dir": str(sdir)},
        "process": {"long_running": True},
        "sample_file": str(sample),
        "inbound": {"mode": "call", "call": "api.sendMessage(sessionId, mail.body)"},
        "_guessed": True,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", default=shutil.which("aimail") or "aimail")
    ap.add_argument("--answers-dir", default="", help="directory host answers, one <host>.json each")
    ap.add_argument("--apply", action="store_true", help="directory hosts only: write adapter + skill")
    ap.add_argument("--with-reload", action="store_true", help="let setup call the host reload hook")
    ap.add_argument("--keep", action="store_true", help="keep what setup wrote")
    ap.add_argument("--json", dest="json_out", default="")
    args = ap.parse_args()

    home = Path(os.environ.get("HOME", "~")).expanduser()
    try:
        s = platform_spec(args.cli)
    except SystemExit as e:
        print(f"FAIL {e}", file=sys.stderr)
        return 2
    groot = npm_global_root()

    rows = []
    for host in s.get("order", []):
        plat = s["platforms"][host]
        hdir = home / plat.get("home_dir", "")
        row = {
            "host": host,
            "detected": hdir.is_dir(),
            "mechanism": "package",
            "package": ADAPTER_PKG.get(host, ""),
            "installed": "",
            "native_cmd": native_install(plat),
            "profile": "-",
            "restart": restart_hint(plat),
            "note": "",
        }
        if not row["detected"]:
            row["note"] = f"not installed (no {hdir.name}/ under $HOME)"
            rows.append(row)
            continue

        roots = [hdir] + ([groot] if groot else [])
        row["installed"] = find_pkg(row["package"], roots) or "missing"

        # a directory-based host under the same name? profile it instead of guessing.
        answers = None
        if args.answers_dir:
            f = Path(args.answers_dir) / f"{host}.json"
            if f.is_file():
                answers = json.loads(f.read_text())
        if answers is None and row["installed"] == "missing":
            answers = probe_dir_host(host, hdir) or None
            if answers:
                row["mechanism"] = "directory"
                row["note"] = "profiled from a plugin/skills dir (guessed answers — the agent's own table is better)"

        if answers:
            p = run([args.cli, "agent", "doctor", "--json", "--answers", json.dumps(answers)])
            try:
                prof = json.loads(p.stdout[p.stdout.index("{"):])
                row["profile"] = prof.get("verdict", "?")
            except (ValueError, json.JSONDecodeError):
                row["note"] = (row["note"] + " | doctor failed").strip(" |")
            if args.apply and row["profile"] == "ready" and row["mechanism"] == "directory":
                argv = [args.cli, "agent", "setup", "--answers", json.dumps(answers), "--apply"]
                if not args.with_reload:
                    argv.append("--no-reload")
                sp = run(argv)
                row["profile"] += " / adapter " + ("written" if sp.returncode == 0 else f"rc={sp.returncode}")
                if not args.keep:
                    run([args.cli, "agent", "uninstall", "--host", host])
        elif row["mechanism"] == "package":
            row["note"] = ("adapter package already installed; nothing to write — this host uses its own "
                           "package manager") if row["installed"] != "missing" else \
                          "adapter package missing — run the native command"

        rows.append(row)

    cols = ["host", "detected", "mechanism", "package", "installed", "profile", "native_cmd", "restart", "note"]

    def cell(value: object, limit: int = 46) -> str:
        text = str(value)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    widths = {c: max(len(c), *(len(cell(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(cell(r[c]).ljust(widths[c]) for c in cols))
    print("\n(full commands/paths are in --json output)")

    print("\nTool visibility must be checked inside each host after its reload/restart — "
          "the script cannot read the host's tool list.")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        print("json: " + args.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

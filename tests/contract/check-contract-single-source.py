#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-contract-single-source.py — AIMail agent-side contract "no drift" gate (L0).

Single source of truth = repo root ``contract/aimail-contract.json``. This script implements three rules:

  (a) Constants consistent with manifest: TS ``tssdk/packages/agent-aimail/src/core/contract.ts`` and
      Each contract constant in Python ``pysdk/aimail_contract.py`` must be item-by-item == manifest.
      **Rust side not covered in this batch** (gateway/bridge two repos left for next batch) ⇒ registered as GAP in output.
  (b) Literal whitelist ratchet: literals in manifest are only allowed in whitelist (manifest itself, two
      constant modules, this gate directory, two bootstrap docs `DOCS_WHITELIST` (reason in their comments:
      they must quote contract values verbatim, enforced by check-docs-consistency.py), and lines with
      ``contract-allowed: <reason>`` comment).
      Other locations counted by "file × literal", compared with baseline
      ``tests/contract/contract-literal-baseline.json`` comparison -- **only reductions allowed, no additions**;
      New location ⇒ FAIL and provide ``file:line`` + current/baseline count.
  (c) SKILL.md frontmatter contract name: single source of truth ``resources/skills/SKILL.md`` and
       the copy staged in the agent-aimail package prepack must have ``name:`` / ``toolset:``
      must == manifest's agent_skill_name / agent_toolset_name.
      (This is exactly the check that catches breaking changes like the rename commit d701dae(2026-09-03).)

Exit codes:
  0 = all green;
  1 = drift or violation (takes precedence over 2 when definite violations exist —— both are non-zero, gates are red);
  2 = undecidable (manifest unreadable/unparseable/missing keys, materialized artifacts not yet materialized, git unavailable, baseline missing).

Discipline: diagnostic output must be printed as-is (never ``2>/dev/null`` or ``cut`` to swallow it); each conclusion must include
file:line or the original command. Baseline is generated with ``--update-baseline`` (re-run only after **intentional** reduction/addition
of the whitelist).

Usage:
  python3 tests/contract/check-contract-single-source.py [--repo DIR]
        [--update-baseline] [--show-baseline-diff]
Environment:
  AIMAIL_CONTRACT_MANIFEST manifest path (used for tri-state self-verification "manifest unreadable ⇒ rc=2").
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_KEYS = (
    "inbound_path", "hermes_inbound_path", "hermes_route_name",
    "agent_skill_name", "agent_toolset_name", "binding_file", "pointer_file",
    "inbound_port",
)

# ── Whitelist (files where literals are allowed; paths relative to repo root) ──────────────────────────────
TS_CONTRACT = "tssdk/packages/agent-aimail/src/core/contract.ts"
PY_CONTRACT = "pysdk/aimail_contract.py"
MANIFEST_REL = "contract/aimail-contract.json"
BASELINE_REL = "tests/contract/contract-literal-baseline.json"
GATE_DIR = "tests/contract/"          # The gate's own directory (including baseline) is whitelisted as a whole
# Documentation surface whitelist (2026-09-27, user ruling "documentation capabilities must be backed by implementation"): docs/agent-self-setup
# These two bootstrap documents **must quote contract values verbatim** (inbound paths, registration names, binding/pointer file names, ports,
# agent-level prefixes) to clearly explain the closed loop, so the "literal location count" ratchet does not apply to them; they are instead
# strictly validated by check-docs-consistency.py in the same directory via "value == manifest" verbatim matching (stronger than location count:
# Stronger: red if one character is wrong), both checks run in L0, coverage not downgraded.
# 2026-10-03 added (owner approved fix A): cli/cli-sdk-bridge-boundaries.md —— boundaries finalized
# To clearly explain the contract surface (skill/toolset registration names, binding/pointer file names, inbound paths), contract values must be quoted verbatim,
# Same reason as the two above ⇒ similarly excluded from the "location count" ratchet. ⚠ Difference from the two above: it **does not have**
# check-docs-consistency.py equivalent fallback ⇒ contract values in documentation drifting from source of truth are not constrained by this gate,
# already registered in GAP (see DOCS_WHITELIST_GAPS).
DOCS_WHITELIST = ("docs/agent-self-setup.md",
                  # docs/agent-self-setup_zh.md removed 2026-10-25 (owner ruling: EN-only bootstrap
                  # doc, single activation entry point) — whitelist entry retired with the file.
                  # 2026-10-06 added (owner signed contract v1.0): CLI↔SDK boundary specification document,
                  # must quote contract values verbatim (same as original cli/cli-sdk-bridge-boundaries.md fix A).
                  # 2026-10-08 moved from docs/contract-boundary_zh.md to cli/contract-boundary.md;
                  # old cli/cli-sdk-bridge-boundaries.md (2026-10-03 boundary draft) has been replaced and deleted by v1.0 contract.
                  "cli/contract-boundary.md")
#: 2026-10-23 addition: SDK adaptation layer sole maintenance document (original platform documentation retired
#: deleted, its host-side contract facts migrated accordingly). Same reason as agent-self-setup:
#: To clearly explain the adaptation contract surface (inbound paths/ports/bindings/pointer file names/registration names/agent-level prefixes), contract values must be quoted verbatim
#: Reference contract value; equivalent fallback = (c) check in check-docs-consistency.py in the same directory
#: (PLATFORM_EN/ZH have been re-pointed to these two, values match the single source of truth verbatim, a single character error turns it red)
#: ⇒ Do not register in DOCS_WHITELIST_GAPS (has fallback, coverage not downgraded).
_DOCS_PLAYBOOK = ("docs/agent-adapter-playbook.md",
                  "docs/agent-adapter-playbook_zh.md")
#: Documents in the whitelist with "no equivalent verbatim fallback" —— must be explicitly registered, no silent downgrade.
#: Original cli/cli-sdk-bridge-boundaries.md (boundary draft, no fallback) has been deleted with v1.0 contract; its successor
#: cli/contract-boundary.md is listed with agent-self-setup, not registered in this GAP (maintaining original criteria).
#: 2026-10-23 Added _DOCS_PLAYBOOK has check-docs-consistency (c) fallback, same as not registered.
DOCS_WHITELIST_GAPS = ()
WHITELIST_FILES = ((MANIFEST_REL, TS_CONTRACT, PY_CONTRACT, BASELINE_REL)
                   + DOCS_WHITELIST + _DOCS_PLAYBOOK)
ALLOW_MARKER = "contract-allowed:"    # Inline escape hatch (must include reason)

# ── Rule (c): SKILL.md frontmatter (1 source of truth + the copy staged by each package's prepack) ────────
SKILL_SOURCE = "resources/skills/SKILL.md"
SKILL_MATERIALIZED = (
    # 2026-10-22 Single package merge: originally 4 packages each staged a copy (generic core + 3 host adapters)
     # Converged to one copy within the agent-aimail single package.
    "tssdk/packages/agent-aimail/resources/skills/SKILL.md",
)

# Manifest key → (TS constant name, Python constant name)
CONST_MAP = {
    "inbound_path": ("INBOUND_PATH", "INBOUND_PATH"),
    "hermes_inbound_path": ("HERMES_INBOUND_PATH", "HERMES_INBOUND_PATH"),
    "hermes_route_name": ("HERMES_ROUTE_NAME", "HERMES_ROUTE_NAME"),
    "agent_skill_name": ("AGENT_SKILL_NAME", "AGENT_SKILL_NAME"),
    "agent_toolset_name": ("AGENT_TOOLSET_NAME", "AGENT_TOOLSET_NAME"),
    "binding_file": ("BINDING_FILE", "BINDING_FILE"),
    "pointer_file": ("POINTER_FILE", "POINTER_FILE"),
    "inbound_port": ("INBOUND_PORT", "INBOUND_PORT"),
}

# Literal scanning strategy: path/filename type = substring count; single token skill/toolset name =
# word boundary count (otherwise agentmail.json / agentmail_home / .agentmail / AGENTMAIL_* would all be miscounted).
TOKEN_KEYS = ("agent_skill_name", "agent_toolset_name")


def _token_re(lit: str):
    """Word boundary regex (dynamically built based on manifest values, not hardcoded strings ⇒ contract rename gate follows automatically)."""
    return re.compile(r"(?<![A-Za-z0-9_.\-])%s(?![A-Za-z0-9_.\-])" % re.escape(lit))


class CannotJudge(Exception):
    """Cannot judge ⇒ rc=2 (fail-closed, never pretend to pass)."""


# ── Output ───────────────────────────────────────────────────────────────────
_VIOLATIONS: list = []
#: (Rule, Description) —— Uncovered/undecidable items, gate must register them honestly
_GAPS: list = []


def out(msg: str = "") -> None:
    print(msg)


def viol(rule: str, msg: str) -> None:
    _VIOLATIONS.append((rule, msg))


def gap(rule: str, msg: str) -> None:
    _GAPS.append((rule, msg))


# ── Manifest ───────────────────────────────────────────────────────────────────
def load_manifest() -> tuple[dict, str]:
    p = os.environ.get("AIMAIL_CONTRACT_MANIFEST", "").strip()
    path = Path(p).expanduser() if p else None
    if path is None:
        path = REPO / MANIFEST_REL
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CannotJudge(f"Manifest unreadable: {path} ({e.__class__.__name__}: {e})") from e
    if not raw.strip():
        raise CannotJudge(f"Manifest is empty: {path}")
    try:
        man = json.loads(raw)
    except ValueError as e:
        raise CannotJudge(f"Manifest is not valid JSON: {path} ({e})") from e
    if not isinstance(man, dict):
        raise CannotJudge(f"Manifest top-level is not an object: {path} ({type(man).__name__})")
    missing = [k for k in REQUIRED_KEYS if k not in man]
    if missing:
        raise CannotJudge(f"Manifest missing contract keys: {path} missing {missing}")
    port = man["inbound_port"]
    if not isinstance(port, int) or isinstance(port, bool) or not (0 < port < 65536):
        raise CannotJudge(f"Manifest inbound_port is not a valid port number: {path} ({port!r})")
    return man, str(path)


# ── Rule (a): Constant consistency ───────────────────────────────────────────────────
def _ts_consts(path: Path) -> dict:
    """Grab export const values from the TS constants module (strings + numbers)."""
    found = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    str_re = re.compile(r"^export const ([A-Z][A-Z0-9_]*) = '([^']*)'")
    num_re = re.compile(r"^export const ([A-Z][A-Z0-9_]*)[^=]*= (\d+)")
    for i, line in enumerate(lines, 1):
        m = str_re.match(line)
        if m:
            found[m.group(1)] = (m.group(2), i, line.strip())
            continue
        m = num_re.match(line)
        if m:
            found[m.group(1)] = (int(m.group(2)), i, line.strip())
    return found


def _py_consts(path: Path) -> dict:
    """Extract contract constants from Python constant module (execution module = truth, line numbers located separately via regex)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_aimail_contract_probe", str(path))
    if spec is None or spec.loader is None:
        raise CannotJudge(f"Cannot load Python constant module: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    found = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    py_names = {py for _, py in CONST_MAP.values()}
    for i, line in enumerate(lines, 1):
        m = re.match(r"^([A-Z][A-Z0-9_]*) = (.*)$", line)
        if m and m.group(1) in py_names:
            found.setdefault(m.group(1), (None, i, line.strip()))
    for _, py_name in CONST_MAP.values():
        if hasattr(mod, py_name):
            _, line_no, src = found.get(py_name, (None, 1, py_name))
            found[py_name] = (getattr(mod, py_name), line_no, src)
    return found


def check_constants(man: dict) -> None:
    out("[contract] (a) constants == manifest")
    for label, rel, loader in (("python", PY_CONTRACT, _py_consts),
                               ("ts", TS_CONTRACT, _ts_consts)):
        path = REPO / rel
        if not path.is_file():
            viol("a", f"{rel}: constant module missing (contract requires it to exist; deleting = breach, not undecidable)")
            out(f"  ✗ {rel}: constant module missing")
            continue
        try:
            consts = loader(path)
        except CannotJudge:
            raise
        except Exception as e:  # noqa: BLE001 — constants module itself is broken = undecidable
            raise CannotJudge(f"{rel}: constant module cannot be parsed/executed ({e.__class__.__name__}: {e})") from e
        bad = 0
        for key, (ts_name, py_name) in CONST_MAP.items():
            name = ts_name if label == "ts" else py_name
            want = man[key]
            if name not in consts:
                viol("a", f"{rel}: contract constant {name} not found (manifest key {key})")
                out(f"  ✗ {rel}: no constant {name} ← manifest key {key}")
                bad += 1
                continue
            got, line_no, src = consts[name]
            if got != want:
                viol("a", f"{rel}:{line_no} {name} = {got!r} != manifest {want!r}")
                out(f"  ✗ {rel}:{line_no} {name} = {got!r} != manifest {want!r} (source: {src})")
                bad += 1
        if not bad:
            out(f"  ✓ {rel} == manifest ({len(CONST_MAP)} items)")
    # Rust side not done in this batch ⇒ register GAP (no silent omission)
    gap("a", "rust: gateway/bridge Rust constants in both repos not covered in this batch (next batch follows ⇒ see record "
             "aimail-contract-hardening-pending.md); contract source of truth **contains no bridge keys** (owner ruling "
             "2026-09-28 SDK de-bridging): bridge = CLI's environmental responsibility, not part of agent internal contract surface")
    out("  · GAP rust: not covered (gateway/bridge left for next batch)")


 # ── Rule (b): Literal whitelist ratchet ────────────────────────────────────────────
def literal_specs(man: dict) -> dict:
    """Manifest key → literal value (deduplicate same values; merge same values into the same logical literal).

    Non-string contract values (``inbound_port`` is a number) do not participate in
    literal scanning —— only the string surface is scanned.
    """
    specs: dict = {}
    for key, val in man.items():
        if key not in CONST_MAP:
            continue
        if not isinstance(val, str):
            continue
        group = "token" if key in TOKEN_KEYS else "substr"
        specs.setdefault((val, group), []).append(key)
    return specs


def _tracked_files() -> list:
    try:
        r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"],
                           capture_output=True, check=False)
    except OSError as e:
        raise CannotJudge(f"git unavailable, cannot get tracked file manifest ({e.__class__.__name__}: {e})") from e
    if r.returncode != 0:
        raise CannotJudge(f"git ls-files failed (rc={r.returncode}): "
                          f"{r.stderr.decode('utf-8', 'replace').strip()}")
    return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]


def scan_literals(man: dict) -> tuple[dict, dict]:
    """Returns (counts[path][literal] = n, hits[path] = [(lineno, literal, text)])."""
    specs = literal_specs(man)
    matchers = [(lit, group, keys[0], _token_re(lit) if group == "token" else None)
                for (lit, group), keys in specs.items()]
    counts: dict = {}
    hits: dict = {}
    for rel in _tracked_files():
        if rel.startswith(GATE_DIR) or rel in WHITELIST_FILES:
            continue
        path = REPO / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary/unreadable = not participating (not undecidable)
        for lineno, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line:
                continue
            for lit, group, key, rx in matchers:
                n = len(rx.findall(line)) if rx is not None else line.count(lit)
                if not n:
                    continue
                counts.setdefault(rel, {})[key] = counts.get(rel, {}).get(key, 0) + n
                hits.setdefault(rel, []).append((lineno, key, line.strip()[:160]))
    return counts, hits


def load_baseline() -> dict:
    p = REPO / BASELINE_REL
    if not p.is_file():
        raise CannotJudge(f"Baseline missing: {BASELINE_REL} (use --update-baseline to generate and commit)")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise CannotJudge(f"Baseline is not valid JSON: {BASELINE_REL} ({e})") from e
    files = data.get("files")
    if not isinstance(files, dict):
        raise CannotJudge(f"Baseline missing files mapping: {BASELINE_REL}")
    return data


def check_literals(man: dict, update: bool, show_diff: bool) -> None:
    out("[contract] (b) Literal whitelist ratchet")
    # Documents in the whitelist with "no equivalent verbatim fallback" must be explicitly registered —— no silent downgrade coverage
    # (self-setup has two with check-docs-consistency.py fallback; boundary finalization does not).
    for rel in DOCS_WHITELIST_GAPS:
        gap("b", f"{rel} is already in the document whitelist (owner 2026-10-03 approved fix A: boundary documents must quote contract values verbatim)"
                 f", but **no check-docs-consistency equivalent fallback** ⇒ contract values in that document drift from the source of truth"
                 f"are not constrained by this gate (registered, not a pass item)")
    counts, hits = scan_literals(man)
    if update:
        payload = {
            "_comment": ("Literal ratchet baseline (counted by file × manifest key). Rule = no new additions:"
                         "counts may only decrease, not increase; new locations must use the constant module, or add a "
                         "`contract-allowed: <reason>` comment on that line (test-only escape hatch)."
                         "Regenerate: python3 tests/contract/check-contract-single-source.py "
                         "--update-baseline"),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tracked_files": len(_tracked_files()),
            "hits": sum(sum(v.values()) for v in counts.values()),
            "files": {k: dict(sorted(v.items())) for k, v in sorted(counts.items())},
        }
        (REPO / BASELINE_REL).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        out(f"  · Baseline rewritten: {BASELINE_REL} "
            f"(files {len(counts)}, hits {payload['hits']}, tracked files {payload['tracked_files']})")
        return

    base = load_baseline()
    base_files = base["files"]
    added = 0
    removed = 0
    for rel, cur in sorted(counts.items()):
        was = base_files.get(rel, {})
        for key, n in sorted(cur.items()):
            b = int(was.get(key, 0))
            if n > b:
                added += 1
                where = ", ".join(f"{rel}:{ln}" for ln, k, _ in hits.get(rel, []) if k == key)
                viol("b", f"{rel} {key}={man[key]!r} count {n} > baseline {b} "
                          f"(new locations: {where or '(unlocated)'}))")
                out(f"  ✗ {rel} {key}={man[key]!r}: current {n} > baseline {b} (+{n - b})")
                for ln, k, text in hits.get(rel, []):
                    if k == key:
                        out(f"      {rel}:{ln}: {text}")
    for rel, was in sorted(base_files.items()):
        for key, b in sorted(was.items()):
            n = int(counts.get(rel, {}).get(key, 0))
            if n < b:
                removed += 1
                if show_diff:
                    out(f"  · reduced {rel} {key}={man[key]!r}: {b} → {n}")
    total_now = sum(sum(v.values()) for v in counts.values())
    total_base = sum(sum(v.values()) for v in base_files.values())
    if added == 0:
        out(f"  ✓ No new locations (files {len(counts)} hits {total_now}; baseline {len(base_files)} files "
            f"/ {total_base} hits; reduction of {removed} locations/literals is an allowed direction)")
    else:
        out(f"  ✗ Added {added} locations (should use constant module, or add `{ALLOW_MARKER} <reason>` comment)")


 # ── Rule (c): SKILL.md frontmatter contract name ─────────────────────────────────
def _frontmatter_fields(path: Path, rel: str) -> tuple:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise CannotJudge(f"{rel}: first line is not frontmatter delimiter `---`")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise CannotJudge(f"{rel}: frontmatter not closed")
    name = tool = None
    name_line = tool_line = 0
    for i in range(1, end):
        m = re.match(r"^name:\s*(.+?)\s*$", lines[i])
        if m:
            name, name_line = m.group(1).strip().strip('"\''), i + 1
            continue
        m = re.match(r"^\s+toolset:\s*(.+?)\s*$", lines[i])
        if m:
            tool, tool_line = m.group(1).strip().strip('"\''), i + 1
    return name, name_line, tool, tool_line


def check_skill_frontmatter(man: dict) -> None:
    out(f"[contract] (c) SKILL.md frontmatter contract name (1 single source of truth + {len(SKILL_MATERIALIZED)} in-package staged copies)")
    want_name = man["agent_skill_name"]
    want_tool = man["agent_toolset_name"]
    ok = 0
    src = REPO / SKILL_SOURCE
    if not src.is_file():
        viol("c", f"{SKILL_SOURCE}: single source of truth SKILL.md missing")
        out(f"  ✗ {SKILL_SOURCE}: missing")
    else:
        targets = [(SKILL_SOURCE, False)] + [(p, True) for p in SKILL_MATERIALIZED]
        for rel, materialized in targets:
            path = REPO / rel
            if not path.is_file():
                if materialized:
                    # In-package generated artifact missing = that package has not staged its own copy (run its own prepack)
                    raise CannotJudge(
                        f"{rel}: in-package generated artifact does not exist, frontmatter cannot be judged (run that package's prepack first: "
                        f"node tssdk/packages/agent-aimail/scripts/stage-resources.mjs --dest <pkg>; gate calls this check after staging)")
                raise CannotJudge(f"{rel}: does not exist")
            name, nline, tool, tline = _frontmatter_fields(path, rel)
            bad = []
            if name != want_name:
                bad.append(f"{rel}:{nline} name: {name!r} != {want_name!r}")
            if tool != want_tool:
                bad.append(f"{rel}:{tline} toolset: {tool!r} != {want_tool!r}")
            if bad:
                for b in bad:
                    viol("c", b)
                    out(f"  ✗ {b}")
            else:
                ok += 1
                out(f"  ✓ {rel}: name={name!r} toolset={tool!r}")
    if ok == len(SKILL_MATERIALIZED) + 1:
        out(f"  ✓ {ok}/{ok} frontmatter == manifest (host looks up by `/{want_name}` ⇒ this is the broken link defense line)")


# ── main ───────────────────────────────────────────────────────────────────
def main(argv: list) -> int:
    global REPO
    ap = argparse.ArgumentParser(description="AIMail agent side contract single source of truth gate")
    ap.add_argument("--repo", default="", help="Repo root (default derived from this file's location)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="Rewrite literal ratchet baseline (only run after intentional changes)")
    ap.add_argument("--show-baseline-diff", action="store_true",
                    help="Print count reduction details")
    args = ap.parse_args(argv)

    REPO = Path(args.repo).expanduser().resolve() if args.repo \
        else Path(__file__).resolve().parents[2]

    try:
        man, man_path = load_manifest()
    except CannotJudge as e:
        out(f"[contract] CANNOT JUDGE (rc=2): {e}")
        return 2
    out(f"[contract] manifest: {man_path}")
    out(f"[contract] Contract keys {len(REQUIRED_KEYS)}: " +
        ", ".join(f"{k}={man[k]!r}" for k in REQUIRED_KEYS))

    # Run all three rules (one rule being unable to judge does not affect the verdict output of the remaining rules)
    cannot: list = []
    update = bool(args.update_baseline)
    show_diff = bool(args.show_baseline_diff)
    rules = (
        lambda: check_constants(man),
        lambda: check_literals(man, update, show_diff),
        lambda: check_skill_frontmatter(man),
    )
    for fn in rules:
        try:
            fn()
        except CannotJudge as e:
            cannot.append(str(e))
            out(f"[contract] CANNOT JUDGE: {e}")

    if _GAPS:
        out("[contract] GAP (uncovered, registered, not a pass item):")
        for rule, msg in _GAPS:
            out(f"  · ({rule}) {msg}")
    if _VIOLATIONS:
        out(f"[contract] FAIL — {len(_VIOLATIONS)} violations"
            + (f"; plus {len(cannot)} items unable to be judged" if cannot else "") + ":")
        for rule, msg in _VIOLATIONS:
            out(f"  ✗ ({rule}) {msg}")
        for c in cannot:
            out(f"  ? Unable to judge: {c}")
        return 1
    if cannot:
        out(f"[contract] CANNOT JUDGE (rc=2) — {len(cannot)} items cannot be judged:")
        for c in cannot:
            out(f"  ? {c}")
        return 2
    if args.update_baseline:
        out("[contract] PASS (baseline rewritten)")
        return 0
    out("[contract] PASS — contract single source of truth consistent, no new literal locations, frontmatter contract name correct")
    return 0


REPO = Path(__file__).resolve().parents[2]

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except CannotJudge as _e:  # Fallback: any "unable to judge" must not be disguised as success
        print(f"[contract] CANNOT JUDGE (rc=2): {_e}")
        sys.exit(2)
    except Exception as _e:  # noqa: BLE001 — unexpected exceptions are also "cannot judge", never return 0
        import traceback
        traceback.print_exc()
        print(f"[contract] CANNOT JUDGE (rc=2): unexpected exception {_e.__class__.__name__}: {_e}")
        sys.exit(2)

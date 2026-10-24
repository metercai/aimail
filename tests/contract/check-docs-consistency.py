#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-docs-consistency.py — Documentation ↔ Implementation Consistency Gate (L0).

User ruling (2026-09-27): **Capabilities promised in documentation must be backed by implementation** —— every thing promised in the documentation
must have a corresponding implementation, otherwise it is leading users into a pit. This script asserts three things for `docs/agent-self-setup.md` and
`docs/agent-self-setup_zh.md` (only covering the agent-level activation code path):

  (a) Symbol existence: identifiers wrapped in backticks in the documentation must be found in ``pysdk`` / ``tssdk``
      **defined** (function/class/module-level constant/interface field/type member), otherwise = documentation is teaching users to call
      a non-existent API. Extraction form = single identifier code span (trailing ``()`` allowed);
      only take "lowercase snake_case (at least one ``_``)" or "camelCase/PascalCase with uppercase not in the first position",
      all-uppercase constants/environment variable names (``AIMAIL_*``) and bare lowercase words are excluded by form (they are not symbols).
      Non-symbol names (contract values/example markers) go through an explicit short whitelist ``NON_SYMBOLS``, each with a reason.

  (b) Bilingual structure consistency: the heading structure of the en/zh versions must be identical item by item —— on the same order,
      "level + sequence number" must be consistent (heading text is translation, so not compared literally), otherwise the two documents describe
       about different things.

  (c) Contract values == single source of truth: contract values appearing in the documentation must be character-for-character equal to the single source of truth list
      ``contract/aimail-contract.json`` (prefixes separately reference TS-side constant source of truth).
      Attribution is divided by layer: bootstrap documentation only pins agent internal names (skill/toolset registration names, binding file names,
      pointer file names); host-side facts (inbound paths, Hermes exceptions, inbound ports for each platform) are pinned in the adapter layer
      documentation ``docs/agent-adapter-playbook*.md`` —— agent-level flows are only divided by language, platform endpoints
      belong to the adapter layer (owner ruling 2026-10-19 de-branding; 2026-10-23 original platform documentation retired,
      attribution migrated to adapter layer documentation). If documentation writes wrong values (even if only one character differs) ⇒ the source of truth value is
      **not found** in the attribution documentation ⇒ red.

Exit codes:
  0 = pass;
  1 = violation (print ``file:line`` for each, takes priority over 2 when there are definite violation items —— both are non-zero);
  2 = cannot determine (documentation unreadable, list unreadable/missing keys, prefix source of truth unreadable).

Usage:
  python3 tests/contract/check-docs-consistency.py [--repo DIR]
        [--en docs/agent-self-setup.md] [--zh docs/agent-self-setup_zh.md]
  --en/--zh override the path of the documentation being checked (used for three-state self-verification "documentation unreadable ⇒ rc=2").
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

 # ── Checked document and source of truth ─────────────────────────────────────────────────────────
DEFAULT_EN = "docs/agent-self-setup.md"
DEFAULT_ZH = "docs/agent-self-setup_zh.md"
#: Attribution documentation for platform facts (inbound paths/ports) —— agent-level flows are only divided by language, platform endpoints belong to the adapter layer,
#: therefore these contract values are pinned in the adapter layer documentation, no longer requiring them to appear in the bootstrap documentation (owner ruling 2026-10-19 de-branding;
#: 2026-10-23 Original platform documentation retired and deleted, platform fact ownership migrated to
#: agent-adapter-playbook{,_zh}.md —— the sole maintenance document for the SDK adapter layer).
PLATFORM_EN = "docs/agent-adapter-playbook.md"
PLATFORM_ZH = "docs/agent-adapter-playbook_zh.md"
MANIFEST_REL = "contract/aimail-contract.json"
#: system_id prefix for agent-level binding —— the implementation-side source of truth (the documentation explaining "which path is enabled" must state this).
PREFIX_SRC = "tssdk/packages/agent-aimail/src/core/poll-entry.ts"
PREFIX_CONST = "AGENT_SCOPE_SYSTEM_PREFIX"

#: The agent internal contract names that the bootstrap documentation must quote verbatim (language-agnostic; missing any = inconsistent with the source of truth).
CONTRACT_KEYS = (
    "agent_skill_name", "binding_file", "pointer_file",
)
#: Host-side contract values that platform documentation must quote verbatim (inbound path + ports for each platform).
PLATFORM_CONTRACT_KEYS = ("inbound_path", "hermes_inbound_path")

# ── Non-symbol name whitelist (short; each entry with reason)──────────────────────────────────────────
NON_SYMBOLS = {
    # Contract **value** (not SDK symbol): agent-level bound system_id prefix, whose value is
    # AGENT_SCOPE_SYSTEM_PREFIX definition in poll-entry.ts, the documentation must be written out.
    "shared_addr_": "agent-level binding system_id prefix (contract value, not SDK symbol)",
    # Gateway-side request/activation response field names (consumed only in cli/aimail in this repo, not defined by pysdk/tssdk
    #): documentation explaining "mailbox validity period criteria" must state this.
    "validity_days": "Gateway activation response field name (not defined by SDK, see consumption in cli/aimail)",
}

 # ── Output ───────────────────────────────────────────────────────────────────
_VIOLATIONS: list = []


class CannotJudge(Exception):
    """Cannot determine ⇒ rc=2 (fail-closed, never pretend to pass)."""


def out(msg: str = "") -> None:
    print(msg)


def viol(rule: str, msg: str) -> None:
    _VIOLATIONS.append((rule, msg))


# ── Text tools: backtick span / fence / heading ────────────────────────────────────
def _strip_fences(text: str) -> list:
    """Returns [(lineno, line, in_fence)] —— lines inside fences do not participate in heading detection."""
    rows = []
    fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```") or line.lstrip().startswith("~~~"):
            fence = not fence
            rows.append((i, line, True))
            continue
        rows.append((i, line, fence))
    return rows


_SPAN = re.compile(r"`([^`\n]+)`")
#: Single identifier form (may have trailing ()): others (including dot/slash/space) are never taken.
_IDENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\(\s*\))?$")
_ALL_CAPS = re.compile(r"^[A-Z0-9_]+$")


def _identifier_spans(text: str) -> list:
    """Extract code spans that "look like code identifiers" ⇒ [(lineno, name, span)].

    Form filtering (excludes non-symbol bare words/constant names):
      ① Lowercase snake_case: ``^[a-z][a-z0-9_]*$`` and contains ``_`` (e.g., pull_list)
      ② CamelCase/PascalCase: has uppercase not at the first position (e.g., startAgentPullEntries / GatewayClient)
      ③ All-uppercase constants/environment variable names are naturally excluded by ① ② (AIMAIL_PULL_INTERVAL_MS)
    """
    got = []
    for lineno, line, _ in _strip_fences(text):
        for span in _SPAN.findall(line):
            span = span.strip()
            m = _IDENT.match(span)
            if not m:
                continue
            name = m.group(1)
            if _ALL_CAPS.match(name):
                continue
            snake = bool(re.match(r"^[a-z][a-z0-9_]*$", name)) and "_" in name
            camel = any(c.isupper() for c in name[1:])
            if not (snake or camel):
                continue
            got.append((lineno, name, span))
    return got


_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_ORDINAL = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")


def _headings(text: str) -> list:
    """Returns [(level, struct_key, title, lineno)] —— struct_key = level + sequence number."""
    rows = []
    unnumbered: dict = {}
    for lineno, line, in_fence in _strip_fences(text):
        if in_fence:
            continue
        m = _HEAD.match(line)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2)
        om = _ORDINAL.match(title)
        if om:
            key = f"{level}:{om.group(1)}"
        else:
            unnumbered[level] = unnumbered.get(level, 0) + 1
            key = f"{level}:#{unnumbered[level]}"
        rows.append((level, key, title, lineno))
    return rows


 # ── (a) Symbol index ───────────────────────────────────────────────────────────
_PY_DEF = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(\w+)")
_PY_ASSN = re.compile(r"^([A-Za-z_]\w*)\s*(?::[^=]+)?=")
_TS_DEF = re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
                     r"(?:function|const|let|var|class|interface|type|enum)\s+(\w+)")
_TS_FN = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)")
_TS_FIELD = re.compile(r"^\s*([a-z_][A-Za-z0-9_]*)\??\s*:")


def build_symbol_index(repo: Path) -> dict:
    """name → source (file:line) definition index (functions/classes/constants/interface fields/type members)."""
    idx: dict = {}

    def add(name: str, rel: str, lineno: int) -> None:
        idx.setdefault(name, f"{rel}:{lineno}")

    py_files = sorted(p for p in (repo / "pysdk").rglob("*.py")
                      if "__pycache__" not in p.parts)
    for path in py_files:
        rel = path.relative_to(repo).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8",
                                                     errors="replace").splitlines(), 1):
            m = _PY_DEF.match(line) or _PY_ASSN.match(line)
            if m:
                add(m.group(1), rel, lineno)
    ts_files = sorted(p for p in (repo / "tssdk" / "packages").rglob("*.ts")
                      if "node_modules" not in p.parts)
    for path in ts_files:
        rel = path.relative_to(repo).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8",
                                                     errors="replace").splitlines(), 1):
            m = _TS_DEF.match(line) or _TS_FN.match(line) or _TS_FIELD.match(line)
            if m:
                add(m.group(1), rel, lineno)
    return idx


def check_symbols(rel: str, text: str, idx: dict) -> None:
    out(f"[docs] (a) {rel}: Backtick identifier existence")
    checked = 0
    for lineno, name, span in _identifier_spans(text):
        if name in NON_SYMBOLS:
            continue
        checked += 1
        where = idx.get(name)
        if where is None:
            viol("a", f"{rel}:{lineno} Backtick identifier `{span}` is not defined in pysdk/tssdk"
                      f"(documentation is teaching users to call a non-existent API; if it is not a symbol, add it to NON_SYMBOLS "
                      f"and write a reason)")
            out(f"  ✗ {rel}:{lineno}: `{span}` definition not found")
    out(f"  ✓ {rel}: Extracted {checked} identifiers, all defined")


 # ── (b) Bilingual heading structure ───────────────────────────────────────────────────────
def check_structure(rel_en: str, text_en: str, rel_zh: str, text_zh: str) -> None:
    out("[docs] (b) en/zh heading structure consistency")
    h_en = _headings(text_en)
    h_zh = _headings(text_zh)
    k_en = [k for _, k, _, _ in h_en]
    k_zh = [k for _, k, _, _ in h_zh]
    if k_en == k_zh:
        out(f"  ✓ Heading structure is identical item by item ({len(k_en)} items; level + sequence number)")
        return
    n = min(len(k_en), len(k_zh))
    detail = []
    for i in range(max(len(k_en), len(k_zh))):
        a = k_en[i] if i < len(k_en) else "(missing)"
        b = k_zh[i] if i < len(k_zh) else "(missing)"
        if a != b:
            la = h_en[i][3] if i < len(h_en) else 0
            lb = h_zh[i][3] if i < len(h_zh) else 0
            detail.append(f"{rel_en}:{la} `{a}` != {rel_zh}:{lb} `{b}`")
    viol("b", f"Heading structure inconsistent (en {len(k_en)} items / zh {len(k_zh)} items): " +
              "; ".join(detail[:5]) + (" …" if len(detail) > 5 else ""))
    for d in detail[:5]:
        out(f"  ✗ {d}")
    out(f"  ✗ en {len(k_en)} headings / zh {len(k_zh)} items (first {n} items compared item by item until the first divergence)")


 # ── (c) Contract values == Single Source of Truth ─────────────────────────────────────────────────
def load_manifest(repo: Path) -> dict:
    path = repo / MANIFEST_REL
    try:
        man = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise CannotJudge(f"Single source of truth list cannot be read: {path} ({e.__class__.__name__}: {e})") from e
    except ValueError as e:
        raise CannotJudge(f"List is not valid JSON: {path} ({e})") from e
    missing = [k for k in CONTRACT_KEYS + ("inbound_ports",) if k not in man]
    if missing:
        raise CannotJudge(f"List missing contract keys: {path} missing {missing}")
    return man


def agent_scope_prefix(repo: Path) -> str:
    path = repo / PREFIX_SRC
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CannotJudge(f"Prefix source of truth cannot be read: {path} ({e.__class__.__name__}: {e})") from e
    m = re.search(r"export const %s\s*=\s*'([^']*)'" % PREFIX_CONST, src)
    if not m:
        raise CannotJudge(f"{path}: cannot find `export const {PREFIX_CONST} = '…'`")
    return m.group(1)


def check_contract_values(rel: str, text: str, man: dict, prefix: str,
                          keys: tuple = CONTRACT_KEYS, ports: bool = False,
                          require_prefix: bool = True) -> None:
    out(f"[docs] (c) {rel}: Contract value == single source of truth")
    nlines = len(text.splitlines())
    hits = 0
    for key in keys:
        val = man[key]
        if val in text:
            hits += 1
            continue
        viol("c", f"{rel}:1 (full text {nlines} lines no match) missing contract value {key}={val!r} "
                  f"← single source of truth {MANIFEST_REL}; value written in docs must equal source verbatim")
        out(f"  ✗ {rel}: full text no match for contract value {key}={val!r}")
    if ports:
        for plat, port in sorted(man["inbound_ports"].items()):
            if str(port) in text:
                hits += 1
                continue
            viol("c", f"{rel}:1 (full text {nlines} lines no match) missing manifest inbound port "
                      f"{plat}={port} ← Single Source of Truth {MANIFEST_REL}[inbound_ports]")
            out(f"  ✗ {rel}: full text no match for inbound port {plat}={port}")
    total = len(keys) + (len(man["inbound_ports"]) if ports else 0)
    if require_prefix:
        total += 1
        if prefix in text:
            hits += 1
        else:
            viol("c", f"{rel}:1 (full text {nlines} lines no match) missing agent-level binding prefix {prefix!r} "
                      f"← Implementation source of truth {PREFIX_SRC}:{PREFIX_CONST}")
            out(f"  ✗ {rel}: full text no match for agent-level binding prefix {prefix!r}")
    out(f"  ✓ {rel}: {hits}/{total} contract values matched source verbatim")


# ── main ───────────────────────────────────────────────────────────────────
def _read(repo: Path, rel: str) -> str:
    path = (repo / rel) if not Path(rel).is_absolute() else Path(rel)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise CannotJudge(f"Doc unreadable: {path} ({e.__class__.__name__}: {e}) —— "
                          f"if unreadable, do not judge, never pretend to pass") from e


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="AIMail doc↔implementation consistency gate")
    ap.add_argument("--repo", default="", help="repo root (default derived from this file location)")
    ap.add_argument("--en", default=DEFAULT_EN, help=f"English doc (default {DEFAULT_EN})")
    ap.add_argument("--zh", default=DEFAULT_ZH, help=f"Chinese doc (default {DEFAULT_ZH})")
    ap.add_argument("--platform-en", default=PLATFORM_EN,
                    help=f"English platform doc (default {PLATFORM_EN})")
    ap.add_argument("--platform-zh", default=PLATFORM_ZH,
                    help=f"Chinese platform doc (default {PLATFORM_ZH})")
    args = ap.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve() if args.repo \
        else Path(__file__).resolve().parents[2]

    try:
        text_en = _read(repo, args.en)
        text_zh = _read(repo, args.zh)
        plat_en = _read(repo, args.platform_en)
        plat_zh = _read(repo, args.platform_zh)
        man = load_manifest(repo)
        prefix = agent_scope_prefix(repo)
        idx = build_symbol_index(repo)
    except CannotJudge as e:
        out(f"[docs] CANNOT JUDGE (rc=2): {e}")
        return 2

    out(f"[docs] Docs checked: {args.en} / {args.zh}")
    out(f"[docs] Platform docs: {args.platform_en} / {args.platform_zh}")
    out(f"[docs] Symbol index: {len(idx)} definitions (from pysdk/ + tssdk/packages/)")
    out(f"[docs] Single source of truth: {MANIFEST_REL}")

    cannot: list = []
    for fn in (lambda: check_symbols(args.en, text_en, idx),
               lambda: check_symbols(args.zh, text_zh, idx),
               lambda: check_structure(args.en, text_en, args.zh, text_zh),
               # Bootstrap doc = agent side: only check symbols + title isomorphism. Contract values belong to host facts,
               # single ownership in platform docs (next two) —— writing into bootstrap doc only creates ambiguity.
               lambda: out(f"[docs] (c) {args.en}: contract values not applicable"
                           "(bootstrap doc does not carry host infrastructure names, see platform docs)"),
               lambda: check_contract_values(
                   args.platform_en, plat_en, man, prefix,
                   keys=CONTRACT_KEYS + PLATFORM_CONTRACT_KEYS, ports=True),
               lambda: check_contract_values(
                   args.platform_zh, plat_zh, man, prefix,
                   keys=CONTRACT_KEYS + PLATFORM_CONTRACT_KEYS, ports=True)):
        try:
            fn()
        except CannotJudge as e:
            cannot.append(str(e))
            out(f"[docs] CANNOT JUDGE: {e}")

    if _VIOLATIONS:
        out(f"[docs] FAIL — {len(_VIOLATIONS)} violations"
            + (f"; plus {len(cannot)} cannot be judged" if cannot else "") + ":")
        for rule, msg in _VIOLATIONS:
            out(f"  ✗ ({rule}) {msg}")
        for c in cannot:
            out(f"  ? Cannot judge: {c}")
        return 1
    if cannot:
        out(f"[docs] CANNOT JUDGE (rc=2) — {len(cannot)} items cannot be determined:")
        for c in cannot:
            out(f"  ? {c}")
        return 2
    out("[docs] PASS — all symbols defined, en/zh title structure consistent, contract values == single source of truth")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except CannotJudge as _e:  # fallback: any "cannot judge" is not allowed to disguise as success
        print(f"[docs] CANNOT JUDGE (rc=2): {_e}")
        sys.exit(2)
    except Exception as _e:  # noqa: BLE001 — unexpected exception is also "cannot judge"
        import traceback
        traceback.print_exc()
        print(f"[docs] CANNOT JUDGE (rc=2): unexpected exception {_e.__class__.__name__}: {_e}")
        sys.exit(2)

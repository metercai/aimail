#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-zero-bridge.py — zero-bridge symbol ratchet (L0, static negative assertion).

owner adjudication 2026-09-28 (**SDK de-bridging**): environment (bridge/routing) is self-held by CLI
 (``cli/bridge_wire.py``; entry = host best-effort notification ``aimail address -a <addr>
--inbound-live|--inbound-down``). SDK **does not talk about bridges**: it must not define/call/import any bridge symbols.

Three rules (all are negative assertions where "hit = 0"):

  (a) ``pysdk/**/*.py``: AST scan identifiers (variables/properties/functions/classes/parameters/import aliases)——
      **comments and docstrings do not count as references** (retirement notes are documentation, not references).
  (b) ``tssdk/packages/*/src/**/*.ts`` (release surface): strip block comments/line comments then scan;
      also scan ``tssdk/test/**/*.ts``, **excluding** ``startup-hook.test.ts``
      (it is the symbol table of the ratchet itself —— those literals are assertion data, not references).
  (c) the contract source of truth must not contain bridge keys: the **keys** of ``contract/aimail-contract.json``, 
      ``pysdk/aimail_contract.py`` and
      constant names in ``tssdk/packages/agent-aimail/src/core/contract.ts``.

  (d) **Reference side** (added 2026-09-28, the missed surface of the definition side): in ``pysdk/**`` ``tssdk/**``
      in ``cli/**`` ``tests/**``, retired symbols must not be **referenced** ——
      import / alias / member access / definition name / parameter name / dynamic call name (``getattr(x, "sym")`` form).
      Scan surface = whitelist extensions **.py/.ts/.sh/.md** **∪ extensionless executable/shebang scripts**
      (2026-09-28 Second supplement: The extension whitelist previously skipped the suffix-less main entry ``cli/aimail`` entirely ——
      its dead reference ``from aimail_base import ensure_bridge_routes_for_system`` dodged another round,
      gate L1 101 PASS/12 FAIL all failed due to that ImportError).
      Root cause: (a)(b) only guard the SDK's own files; elsewhere ``from aimail_base import is_bridge_host_port``
      this kind of "importing a deleted symbol" completely slipped through, and **pyflakes does not report "importing a non-existent
      name from a module"** ⇒ L0 false green (measured: cli/setup_system.py + cli/repair.py two dead references, gate L1
      45 red all failed due to the same ImportError).
      Criteria details (same method as (a)(b)): **comments and docstrings do not count as references**; string literals are treated as **data**
      (Configuration keys / bridge API paths / symbol tables are all data) —— only strings retrieved as **call names** count as references.
      Inline escape hatch: if the line contains an explicit ``retired:`` description marker ⇒ printed in a separate section, not counted as a reference; but **executable
      code** identifier hits are **not allowed** to be exempted by the marker (marker only exempts comment/data lines).
      Generated artifacts and third-party directories (``node_modules`` ``__pycache__`` ``lib`` ``dist``) are not scanned.

Exit codes: 0 = all green; 1 = hits found (file:line given for each); 2 = cannot judge (path unreadable).
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

RETIRED_PY = (
    "register_bridge_route", "bridge_admin_port", "bridge_listening",
    "ensure_bridge_route", "ensure_bridge_routes_for_system",
    "bridge_register_url_path", "store_bridge_register_url",
    "_align_registrations_to_bridge", "format_bridge_route_line",
    "route_outcome_is_warning", "is_deliverable_webhook_url",
    "is_bridge_host_port", "bridge_default_path",
)
RETIRED_TS = (
    "ensureBridgeRoutesForSystem", "ensureBridgeRoute", "registerBridgeRoute",
    "formatBridgeRouteLine", "isBridgeRouteWarning", "bridgeListening",
    "resolveBridgeAdminPort", "BridgeRouteOutcome", "BRIDGE_DEFAULT_PATH",
    "bridgeDefaultPath", "/api/v1/routes",
)
#: Bridge keys not allowed to appear in the contract source of truth (key names / constant names).
FORBIDDEN_CONTRACT_KEYS = ("bridge_default_path", "BRIDGE_DEFAULT_PATH", "bridgeDefaultPath")
TS_RATCHET_FILE = "tssdk/test/startup-hook.test.ts"
CONTRACT_JSON = "contract/aimail-contract.json"
CONTRACT_PY = "pysdk/aimail_contract.py"
CONTRACT_TS = "tssdk/packages/agent-aimail/src/core/contract.ts"

 #: ── (d) Reference side ────────────────────────────────────────────────────────────────
#: Scan scope: **where** retired symbols are **referenced** (definition side only guards the SDK itself, here guards "who is importing/calling it").
REF_SCAN_DIRS = ("pysdk", "tssdk", "cli", "tests")
REF_SCAN_EXT = (".py", ".ts", ".sh", ".md")
#: **Extensionless scripts** (second miss 2026-09-28, added surface): extension whitelist will skip main entries without suffix
#: Skip directly —— CLI main entry ``cli/aimail`` (no extension, shebang ``#!/usr/bin/env python3``)
#: dead reference dodged another round: ``cli/aimail:2091``
#: ``from aimail_base import (ensure_bridge_routes_for_system, ...)`` ⇒ ImportError ⇒
 #: Gate L1 101 PASS / 12 FAIL all failed due to the same place. Criteria = whitelist extensions **∪**
#: (no suffix and (executable bit or first line ``#!``) and text and <= REF_SCAN_MAX_BYTES);
#: choose parser by shebang (see ``_ref_kind``: python → AST, sh → strip line comments, others → text token).
REF_SCAN_EXTENSIONLESS = True
REF_SCAN_MAX_BYTES = 1_000_000
#: Do not scan generated artifacts/third-party (source of truth is in src/; see docstring).
REF_SKIP_DIRS = {"node_modules", "__pycache__", ".git", "lib", "dist", "build"}
#: Escape hatch marker: only if the line contains this explicit comment is "mentioning retired symbols" allowed (description lines / symbol tables), printed in a separate section.
ESCAPE_MARKER = "retired:"
#: "String call name" form: symbol name is taken as **call/attribute name** in string form (dynamic call), not as data.
DYNAMIC_NAME_HINTS = ("getattr(", "setattr(", "import_module(", "__import__(",
                      "globals()[", "require(", "import(")
#: All retired symbols (py definition side + ts definition side), reference side checks both.
RETIRED_ALL = tuple(RETIRED_PY) + tuple(RETIRED_TS)
_RETIRED_SET = frozenset(RETIRED_ALL)


def _strip_ts_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"//[^\n]*", "", code)


def _py_identifiers(tree: ast.AST) -> set:
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[-1])
    return names


def rule_a(repo: Path) -> list:
    hits: list = []
    for f in sorted((repo / "pysdk").rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError as e:
            hits.append(f"{f.relative_to(repo)}: unparsable ({e})")
            continue
        names = _py_identifiers(tree)
        for sym in RETIRED_PY:
            if sym in names:
                hits.append(f"{f.relative_to(repo)}: py identifier {sym}")
    return hits


def rule_b(repo: Path) -> list:
    hits: list = []
    targets = sorted((repo / "tssdk" / "packages").glob("*/src/**/*.ts"))
    targets += [p for p in sorted((repo / "tssdk" / "test").glob("**/*.ts"))
                if p.relative_to(repo).as_posix() != TS_RATCHET_FILE]
    for f in targets:
        rel = f.relative_to(repo).as_posix()
        code = _strip_ts_comments(f.read_text(encoding="utf-8"))
        for sym in RETIRED_TS:
            if sym in code:
                hits.append(f"{rel}: ts symbol {sym}")
    return hits


def rule_c(repo: Path) -> list:
    hits: list = []
    man = json.loads((repo / CONTRACT_JSON).read_text(encoding="utf-8"))
    for key in man:
        if key.lower() in FORBIDDEN_CONTRACT_KEYS or key in FORBIDDEN_CONTRACT_KEYS:
            hits.append(f"{CONTRACT_JSON}: key {key} is a bridge key")
    py_txt = (repo / CONTRACT_PY).read_text(encoding="utf-8")
    ts_txt = _strip_ts_comments((repo / CONTRACT_TS).read_text(encoding="utf-8"))
    for sym in FORBIDDEN_CONTRACT_KEYS:
        # Only constant **definitions** (assignment/declaration) count, comments/docstrings do not
        if re.search(r"^\s*%s\s*[:=]" % re.escape(sym), py_txt, re.M):
            hits.append(f"{CONTRACT_PY}: constant {sym} defined")
        if re.search(r"^\s*(export\s+)?const\s+%s\b" % re.escape(sym), ts_txt, re.M):
            hits.append(f"{CONTRACT_TS}: constant {sym} declared")
    return hits


def _has_shebang(f: Path) -> bool:
    try:
        with f.open("rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return False


def _is_ref_file(f: Path) -> bool:
    """Whitelist extension **∪** extensionless executable/shebang scripts (see REF_SCAN_EXTENSIONLESS for missed records)."""
    if f.suffix in REF_SCAN_EXT:
        return True
    if not REF_SCAN_EXTENSIONLESS or f.suffix:
        return False
    try:
        st = f.stat()
    except OSError:
        return False
    if st.st_size > REF_SCAN_MAX_BYTES:
        return False
    return bool(st.st_mode & 0o111) or _has_shebang(f)


def _iter_ref_files(repo: Path):
    """Reference side scan surface: REF_SCAN_DIRS × (REF_SCAN_EXT ∪ extensionless scripts), skipping generated/third-party directories."""
    for d in REF_SCAN_DIRS:
        base = repo / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or not _is_ref_file(f):
                continue
            if REF_SKIP_DIRS & set(f.parts):
                continue
            yield f


def _ref_kind(f: Path, text: str) -> str:
    """Scan mode: ``py``(AST) / ``ts``(strip comments+strings) / ``sh``(strip line comments) / ``generic``(whole text token).

    Extension-less scripts select method by **first-line shebang** —— criteria consistent with same-extension files:
    python ⇒ AST(comments and docstrings naturally excluded, same method as (a)), sh/bash/zsh/dash ⇒ strip line comments,
    others ⇒ text token(same method as .md: only ``retired:`` marker exempted).
    """
    if f.suffix in (".py", ".ts", ".sh", ".md"):
        return {".py": "py", ".ts": "ts", ".sh": "sh", ".md": "generic"}[f.suffix]
    first = text.splitlines()[0].lower() if text.splitlines() else ""
    if "python" in first:
        return "py"
    if re.search(r"/[a-z]*(?:ba|z|k|da)?sh\b", first):
        return "sh"
    return "generic"


def _token_hits(text: str, syms=RETIRED_ALL) -> list:
    """[(lineno, symbol, line)] —— whole word identifier hits (neither side can be identifier character)."""
    hits: list = []
    pat = {s: re.compile(r"(?<![A-Za-z0-9_$])%s(?![A-Za-z0-9_$])" % re.escape(s))
           for s in syms}
    for i, line in enumerate(text.splitlines(), 1):
        for s, p in pat.items():
            if p.search(line):
                hits.append((i, s, line.strip()))
    return hits


def _py_code_hits(rel: str, text: str) -> tuple:
    """(violations, benign) —— Python reference side.

    Violation = **code identifier** hit: import name/alias, member access(``mod.sym``), bare name, definition name,
    parameter name —— i.e. "this name is really used as a symbol". Comments and docstrings naturally excluded (no comments in AST;
    string constants judged separately).
    benign = same name **string literal**(config key / bridge API path / symbol table / description text)= data;
    only exception: string used as **call name**(``getattr(x, "sym")`` form)⇒ violation.
    """
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as e:
        return [(1, "", f"{rel}: unparsable ({e})")], []
    lines = text.splitlines()
    viol: list = []
    for node in ast.walk(tree):
        # A node may carry **two** names(import original name + as alias), both are references.
        pairs: list = []
        if isinstance(node, ast.Name):
            pairs = [(node.id, "py name")]
        elif isinstance(node, ast.Attribute):
            pairs = [(node.attr, "py member access")]
        elif isinstance(node, ast.alias):
            pairs = [(node.name.split(".")[-1], "py import")]
            if node.asname:
                pairs.append((node.asname.split(".")[-1], "py import alias"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            pairs = [(node.name, "py definition")]
        elif isinstance(node, ast.arg):
            pairs = [(node.arg, "py parameter")]
        elif isinstance(node, ast.keyword):
            pairs = [(node.arg or "", "py keyword")]
        for name, kind in pairs:
            if name and name in _RETIRED_SET:
                lineno = getattr(node, "lineno", 1)
                viol.append((lineno, name, f"{rel}:{lineno}: {kind} {name}"))
    benign: list = []
    for i, line in enumerate(lines, 1):
        for s in RETIRED_ALL:
            if not re.search(r"""["']%s["']""" % re.escape(s), line):
                continue
            dynamic = (any(h in line for h in DYNAMIC_NAME_HINTS)
                       or re.search(r"""["']%s["']\s*\(""" % re.escape(s), line))
            if dynamic:
                viol.append((i, s, f"{rel}:{i}: py string call-name {s!r} (dynamic access)"))
            else:
                benign.append((i, s, f"{rel}:{i}: string literal {s} (data, not a reference)"))
    return viol, benign


def _ts_code(text: str) -> str:
    """TS: strip comments + strip string literals ⇒ remaining are **code identifiers**(strings are data).

    String stripping uses equal length placeholder, line numbers stay aligned. Names in symbol tables(startup-hook.test.ts type)
    are therefore naturally excluded —— they are assertion data, not references.
    """
    code = _strip_ts_comments(text)
    code = re.sub(r"`(?:\\.|[^`\\])*`", "''", code, flags=re.S)
    code = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", code)
    code = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', code)
    return code


def _sh_code(text: str) -> str:
    """shell: only strip line comments; **preserve** strings(``python3 -c "from aimail_base import x"`` is a real reference)."""
    return "\n".join(re.sub(r"(^|\s)#.*$", r"\1", ln) for ln in text.splitlines())


def _scan_ref_file(f: Path, root: Path, prefix: str = "") -> tuple:
    """Judge **one** reference side file —— (violations, escapes, benign, scanned)."""
    rel = prefix + f.relative_to(root).as_posix()
    violations: list = []
    escapes: list = []
    benign: list = []
    try:
        text = f.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        return ([f"{rel}: unreadable ({e})"], [], [], 0)
    kind = _ref_kind(f, text)
    if kind == "py":
        hits, ben = _py_code_hits(rel, text)
        benign += ben
    elif kind == "ts":
        hits = [(i, s, f"{rel}:{i}: ts identifier {s}")
                for i, s, _ in _token_hits(_ts_code(text))]
        # TS side strings: only judge "dynamic call name" form(require('sym') / import('sym'))
        for i, line in enumerate(text.splitlines(), 1):
            for s in RETIRED_ALL:
                if re.search(r"""["'`]%s["'`]""" % re.escape(s), line):
                    if any(h in line for h in DYNAMIC_NAME_HINTS):
                        hits.append((i, s, f"{rel}:{i}: ts string call-name {s!r} (dynamic access)"))
                    else:
                        benign.append((i, s, f"{rel}:{i}: string literal {s} (data, not a reference)"))
    elif kind == "sh":
        hits = [(i, s, f"{rel}:{i}: sh identifier {s}")
                for i, s, _ in _token_hits(_sh_code(text))]
    else:      # .md / other extensionless scripts: text token(same method as .md)
        label = "doc token" if f.suffix == ".md" else "text token"
        hits = [(i, s, f"{rel}:{i}: {label} {s}")
                for i, s, _ in _token_hits(text)]
    src = text.splitlines()
    escaped_lines: set = set()
    for lineno, _sym, msg in hits:
        line = src[lineno - 1] if 0 < lineno <= len(src) else ""
        is_code_identifier = re.search(r"\b(identifier|import|member access|definition|"
                                       r"parameter|keyword)\b", msg) and "string call-name" not in msg
        if ESCAPE_MARKER in line and not is_code_identifier:
            escaped_lines.add(lineno)
            escapes.append(f"{msg}  [escape hatch: {ESCAPE_MARKER}]")
        elif ESCAPE_MARKER in line:
            violations.append(f"{msg}  (escape marker ignored: executable code, not a comment)")
        else:
            violations.append(msg)
    # Explicit description lines in pure comments(do not produce any code/string hits) must also be printed as separate section ——
    # "allowed mention" and "silent ignore" are two different things: escape hatch must be visible.
    for i, line in enumerate(src, 1):
        if i in escaped_lines or ESCAPE_MARKER not in line:
            continue
        if any(re.search(r"(?<![A-Za-z0-9_$])%s(?![A-Za-z0-9_$])" % re.escape(s), line)
               for s in RETIRED_ALL):
            escapes.append(f"{rel}:{i}: documented mention carrying `{ESCAPE_MARKER}` "
                           f"[escape hatch: {line.strip()[:90]}]")
    return violations, escapes, benign, 1


def rule_d(repo: Path) -> tuple:
    """(violations, escapes, benign, scanned) —— reference side ratchet(retired symbols must not be referenced)."""
    violations: list = []
    escapes: list = []
    benign: list = []
    scanned = 0
    for f in _iter_ref_files(repo):
        v, e, b, n = _scan_ref_file(f, repo)
        violations += v
        escapes += e
        benign += b
        scanned += n
    return violations, escapes, benign, scanned


#: ── (e) Cross-repo reference side(2026-09-28 third miss) ────────────────────────────────────────
#: Gate script itself also references contract/product symbols ⇒ retired symbol misses not only happen in aimail repo:
#: tests/cli/docker/journey-in-host.sh:359 references retired BRIDGE_DEFAULT_PATH, while (d) only scans this repo,
#: so it went green all the way to L2 before exposing itself in the form of "bridge form branch unreachable(bridge branch judged as direct push)" —— a
#: cross-repo **false green**. Ownership note: here we scan **read-only sibling repo**(gate repo), no write no modify; criteria
#: (retired symbol references = 0) and (d) are **same source and same strength**, just expanding the scan surface from "this repo" to "this repo ∪ sibling repos".
#: Sibling repo does not exist(e.g. SDK standalone checkout / CI)⇒ record a visible CANNOT JUDGE(gap, not pass).
SIBLING_REPOS = ("../aimail-advanced",)
SIBLING_SCAN_DIRS = ("tests",)

#: ── Reference side **baseline**(only decrease allowed, no increase, 2026-09-28 closure) ────────────────────────────────
#: Cross-repo surface expansion immediately reported 5 existing hits, destination after individual judgment:
#:   · `tests/cli/README.md:45` 3 items (register_bridge_route / ensureBridgeRoute /
#:     `/api/v1/routes`)= **documentary enumeration**, goes through existing visible escape hatch(inline `retired:` marker,
#:     printed as separate section), not entering baseline;
#:   · `tests/cli/docker/route-helpers.sh` 2 `/api/v1/routes` = **data** (fixtures read-only
 #:     reading the bridge's own admin API: `GET /api/v1/routes` / `DELETE /api/v1/routes/:email`,
#:     see contract comments in cli/bridge_wire.py), not reference to retired SDK symbols —— but .sh branch by
#:     design **preserves strings**(strings like `python3 -c "from aimail_base import x"` contain real references),
#:     Therefore, the criterion can only be met by **explicitly grandfathering the baseline**; it must not be silent, nor can the .sh rules be relaxed just to make it green.
#: Baseline semantics: record counts by `path:symbol`, current count > baseline ⇒ rc=1 (print file:line + current/baseline for each);
#: current < baseline ⇒ print one stale line (only decreases allowed, no increases; remind to lower it next time). Baseline file missing ⇒
#: treat as all 0 (any hit is a violation, fail-closed), and print a visible explanation.
REF_BASELINE_JSON = "tests/contract/zero-bridge-ref-baseline.json"


def _iter_sibling_files(root: Path):
    """Sibling repo scan surface: SIBLING_SCAN_DIRS × same set of extension/no-extension rules (criteria not separately created)."""
    for d in SIBLING_SCAN_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or not _is_ref_file(f):
                continue
            if REF_SKIP_DIRS & set(f.parts):
                continue
            yield f


def rule_e(repo: Path) -> tuple:
    """(violations, escapes, benign, scanned, notes) —— sibling repo (gatekeeper repo) reference side."""
    violations: list = []
    escapes: list = []
    benign: list = []
    notes: list = []
    scanned = 0
    for rel_repo in SIBLING_REPOS:
        root = (repo / rel_repo).resolve()
        if not root.is_dir():
            notes.append(f"CANNOT JUDGE: {root} not present — cross-repo reference-side scan "
                         f"NOT exercised here (a gap, not a pass)")
            continue
        for f in _iter_sibling_files(root):
            v, e, b, n = _scan_ref_file(f, root, prefix=f"{rel_repo}/")
            violations += v
            escapes += e
            benign += b
            scanned += n
    return violations, escapes, benign, scanned, notes


def _hit_key(msg: str) -> str:
    """`<path>:<symbol>` —— baseline key (independent of line number: only the occurrence count of the same symbol in the same file is comparable)."""
    m = re.match(r"^(?P<path>.+?):\d+: ", msg)
    path = m.group("path") if m else msg.split(":", 1)[0]
    for sym in RETIRED_ALL:
        if re.search(r"(?<![A-Za-z0-9_$])%s(?![A-Za-z0-9_$])" % re.escape(sym), msg):
            return f"{path}:{sym}"
    return f"{path}:<unknown>"


def _load_baseline(repo: Path) -> tuple:
    """(entries, note) —— reference side baseline (file missing ⇒ empty baseline = any hit is a violation)."""
    p = repo / REF_BASELINE_JSON
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, f"{REF_BASELINE_JSON} absent — baseline taken as empty (any hit fails)"
    except Exception as e:  # noqa: BLE001
        return {}, f"{REF_BASELINE_JSON} unreadable ({e.__class__.__name__}) — baseline taken as empty"
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}, f"{REF_BASELINE_JSON} has no `entries` object — baseline taken as empty"
    return {str(k): int(v) for k, v in entries.items()}, ""


def main(argv: list) -> int:
    update_baseline = "--update-baseline" in argv
    argv = [a for a in argv if a != "--update-baseline"]
    repo = Path(argv[1] if len(argv) > 1 else ".").resolve()
    if not (repo / "pysdk").is_dir():
        print(f"CANNOT JUDGE: {repo}/pysdk not found", file=sys.stderr)
        return 2
    missing = [d for d in REF_SCAN_DIRS if not (repo / d).is_dir()]
    if missing:
        print(f"CANNOT JUDGE: reference-side scan needs {missing} under {repo}",
              file=sys.stderr)
        return 2
    hits = rule_a(repo) + rule_b(repo) + rule_c(repo)
    ref_hits, escapes, benign, scanned = rule_d(repo)
    # (e) Sibling repo (gatekeeper repo) reference side —— same source and strength as (d), just expanding the scan surface to this repo ∪ sibling repos.
    # Sibling repo absence (SDK standalone checkout / CI) is not a pass: print a visible GAP line, but do not change the exit code
    # (on CI, sibling repos do not exist anyway; making it red would mix "not scanned" and "scanned with hits" into the same signal).
    sib_hits, sib_escapes, sib_benign, sib_scanned, sib_notes = rule_e(repo)
    ref_hits = ref_hits + sib_hits
    escapes = escapes + sib_escapes
    benign = benign + sib_benign
    for note in sib_notes:
        print(f"[zero-bridge/references] GAP: {note}")
    # ── Reference side baseline (only decreases allowed, no increases) ───────────────────────────────────────────────
    base, base_note = _load_baseline(repo)
    counts: dict = {}
    for h in ref_hits:
        counts[_hit_key(h)] = counts.get(_hit_key(h), 0) + 1
    if update_baseline:
        out = {"_comment": "reference-side baseline (only-decrease). Regenerated by "
                           "`check-zero-bridge.py --update-baseline`; every entry needs a "
                           "reason in the checker's REF_BASELINE_JSON docstring.",
               "entries": dict(sorted(counts.items()))}
        (repo / REF_BASELINE_JSON).write_text(
            json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[zero-bridge/references] baseline REWRITTEN: {REF_BASELINE_JSON} "
              f"({len(counts)} entrie(s)) — this is a grandfathering action, justify every entry")
        base = dict(counts)
    over: list = []
    for key, n in sorted(counts.items()):
        allowed = base.get(key, 0)
        if n > allowed:
            over.append(f"{key}: current {n} > baseline {allowed}")
    stale = [f"{k}: baseline {v} > current {counts.get(k, 0)}"
             for k, v in sorted(base.items()) if counts.get(k, 0) < v]
    if base_note:
        print(f"[zero-bridge/references] {base_note}")
    if base and not over:
        print(f"[zero-bridge/references] baseline: {len(base)} entrie(s) carried "
              f"(only-decrease; see the REF_BASELINE_JSON docstring for each reason) — "
              f"{sum(counts.values())} hit(s) inside the baseline")
    for s in stale:
        print(f"[zero-bridge/references] baseline stale (only-decrease, lower it): {s}")
    if escapes:
        print(f"[zero-bridge/references] escape hatch — {len(escapes)} documented "
              f"mention(s) carrying an explicit `{ESCAPE_MARKER}` marker "
              f"(prose/symbol tables, NOT references; counted, never silent):")
        for e in escapes:
            print(f"  · {e}")
    if benign:
        print(f"[zero-bridge/references] {len(benign)} string-literal mention(s) treated as "
              f"DATA (config keys / bridge API paths / symbol tables), not references:")
        for b in benign[:8]:
            print(f"  · {b}")
        if len(benign) > 8:
            print(f"  · … +{len(benign) - 8} more")
    if hits or over:
        print(f"[zero-bridge] VIOLATION — the SDK must speak no bridge "
              f"({len(hits)} definition-side + {len(over)} reference-side hit(s) above "
              f"baseline):", file=sys.stderr)
        for h in hits:
            print(f"  ✘ {h}", file=sys.stderr)
        for h in over:
            print(f"  ✘ {h}", file=sys.stderr)
        for h in ref_hits:
            if _hit_key(h) in {o.split(": ", 1)[0] for o in over}:
                print(f"  ✘ {h}", file=sys.stderr)
        return 1
    print(f"[zero-bridge] clean (definitions=0): pysdk/*.py identifiers ∩ "
          f"{len(RETIRED_PY)} retired = 0; tssdk packages/*/src + test/*.ts ∩ "
          f"{len(RETIRED_TS)} retired = 0; contract truth source carries no bridge key")
    print(f"[zero-bridge/references] clean: {scanned} file(s) scanned in "
          f"{'/'.join(REF_SCAN_DIRS)} ("
          f"{'/'.join(e.lstrip('.') for e in REF_SCAN_EXT)}"
          f"{' + extension-less executable/shebang scripts' if REF_SCAN_EXTENSIONLESS else ''}"
          f"; comments+docstrings excluded) "
          f"— {len(RETIRED_ALL)} retired symbol(s) referenced "
          f"{sum(counts.values())} time(s), all inside the baseline")
    print(f"[zero-bridge/references/siblings] clean: {sib_scanned} file(s) "
          f"scanned in {' + '.join(SIBLING_REPOS)}/{'/'.join(SIBLING_SCAN_DIRS)} "
          f"(same extensions/extension-less rules as the in-repo reference scan; read-only) "
          f"— {len(RETIRED_ALL)} retired symbol(s) referenced "
          f"{len(sib_hits)} time(s), all inside the baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-file-ownership.py — Static assertion for file ownership (L0).

owner layered adjudication (2026-09-28, inheriting Phase 1/2a's "SDK de-bridging"):

     System-level environment file  ``aimail_gateway.json``  ← written only by **CLI**
     per-agent binding file ``agentmail.json``     ← written only by **SDK**
    Read-only is always allowed (reading is allowed in both directions).

This script turns that ruling into falsifiable static assertions —— each entry carries "filename literal + write call" **dual evidence**,
Read-only (``open`` without write mode / ``read_text`` / ``json.load`` / ``.is_file()`` / ``glob``) is never judged:

  (a) SDK surface (``pysdk/**`` + ``tssdk/**``) **must not write** to ``aimail_gateway.json``.
      SDK only reads it (gets gateway_url/admin_key/…) —— writing to it = SDK overstepping to manage environment for CLI.
  (b) CLI surface (``cli/**``) **must not write** to ``agentmail.json``.
      the CLI only reads it (the per-agent source of truth for SDK permissions) — writing it = the CLI overstepping to manage identity on behalf of the SDK.
  (c) Helper function surface: Writing binding files via the SDK's "atomic binding file write" API (``save_agent_config``; including
      ``import … as alias`` alias) write binding file, in CLI side is also (b) out-of-bounds write —— evidence is
      **call name** rather than filename literal (measured ``cli/repair.py`` goes through this path, looking only at literals will miss judgments).
  (d) Local atomic disk-write helper surface (added 2026-09-28): When a path is passed to the **repo's own** atomic write helper
      (like ``_atomic_json_write(path, data)``), looking only at ``open``/``write_text`` will miss the entire segment
      Missed detection —— in practice ``cli/aimail`` has five instances via ``_atomic_json_write(jf, cfg)``
      while the checker judges them green (proof of false green). Now the helper name + **first argument** (literal or
      tainted name) also counts as write evidence.

Judgment method (honestly registering approximation):
  · Python: ``ast`` + **scope-based** lightweight taint propagation. Literal ⇒ assignment target is tainted (same name across scopes
    does not cross), ``x = y`` propagates, function names returning tainted values count as taint sources; if write call arguments contain a literal or tainted name
    ⇒ hit. The write call whitelist is specific to **file semantics** (``open(mode contains w/a/x/+)``/``write_text``/
    ``write_bytes``/``unlink``/``touch``/``rename``/``mkdir*``/``rmtree``/``copy*``/
    ``move`` + ``os.remove``/``os.replace``) —— ``str.replace`` and other methods with the same name but unrelated
    are not judged (otherwise the screen would be full of false hits). **No cross-module data flow** (cross-module relies on rule (c) and manual baseline registration).
  · TypeScript (``tssdk/**/*.ts``): Strip comments then scan line by line, same "assignment is taint" + write API list.
  · Shell (``cli/**/*.sh``): Literal on the same line + redirection/``tee``/``sed -i``/``cp``/``mv``/``rm``.
  · Filenames in comments do not count as references (retired/explanatory is documentation); inline escape hatch ``file-ownership-allowed: <reason>``
    must include a reason, and allowed lines are **printed in a separate section** (``allowed by marker``) = visible, not silent.

Baseline(``tests/contract/file-ownership-baseline.json``): Same method as literal ratchet —— **only decrease, no increase**.
Currently known implementation↔adjudication mismatches (with reason) are registered in the baseline, the gate prints them as GAP **loudly** instead of pretending
all green; any **new** overstepping write ⇒ FAIL (rc=1); baseline item disappeared ⇒ print stale hint (should delete that entry).
``--strict`` makes baseline items FAIL too (used when clearing the baseline).

Exit codes: 0 = no new hits (baseline GAPs printed as-is); 1 = new hits/unparseable; 2 = undecidable (path unreadable).
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

 # ── Managed files and two directions ─────────────────────────────────────────────────────────
GATEWAY_FILE_LITERALS = ("aimail_gateway.json", "aimail-gateway.json")
BINDING_FILE_LITERALS = ("agentmail.json",)

#: (Rule id, scan root, target filename literal, description)
RULES = (
    ("sdk-writes-gateway-config", "pysdk/", GATEWAY_FILE_LITERALS),
    ("sdk-writes-gateway-config", "tssdk/", GATEWAY_FILE_LITERALS),
    ("cli-writes-agent-binding", "cli/", BINDING_FILE_LITERALS),
)
RULE_WHY = {
    "sdk-writes-gateway-config":
        "the SDK must not write the system-level environment file "
        "(aimail_gateway.json) — the CLI owns it (read-only for the SDK)",
    "cli-writes-agent-binding":
        "the CLI must not write the per-agent binding file (agentmail.json) — the SDK "
        "owns it (read-only for the CLI)",
}

#: Rule (c): Writing binding files via these APIs = overstepping write (evidence = call name; aliases also recognized)
BINDING_WRITE_APIS = ("save_agent_config", "saveAgentConfig", "writeAgentConfig",
                      "write_agent_config")

#: Write calls specific to file semantics (same-named str/list methods are not included — see module docstring)
PATH_WRITE_METHODS = {
    "write_text", "write_bytes", "unlink", "touch", "rmdir",
    "rename", "replace", "remove", "rmtree", "copy", "copyfile", "copy2", "move",
    "mkdir", "makedirs", "link", "symlink_to", "truncate",
}
#: Rule (d): The repo's own "atomic disk-write helper". Literal/tainted name appearing in the helper's **first argument**
#: (path bit) even if writing evidence —— only looking at ``open``/``write_text`` will miss the whole segment:
 #: Empirically ``cli/aimail`` once used ``_atomic_json_write(jf, cfg)`` to write directly to binding files while the checker was all green.
ATOMIC_WRITE_HELPERS = ("_atomic_json_write", "atomic_json_write", "_atomic_write",
                        "atomic_write", "atomic_write_private", "write_json",
                        "_write_json")
#: Only ``module.method(...)`` form counts (os.remove is a file, list.remove is not)
MODULE_WRITE_CALLS = {
    "os": {"remove", "unlink", "replace", "rename", "rmdir", "mkdir", "makedirs",
           "link", "symlink", "truncate"},
    "shutil": {"rmtree", "copy", "copyfile", "copy2", "move"},
    "pathlib": set(),
}
TS_WRITE_RE = re.compile(
    r"writeFileSync|writeFile\b|appendFile|createWriteStream|unlinkSync|rmSync|"
    r"rmdirSync|renameSync|copyFileSync|truncateSync|mkdirSync|"
    r"openSync\([^)]*[\"'](?:w|a|x|r\+)")
SHELL_WRITE_RE = re.compile(r">\s*\S|tee\s|sed\s+-i|cp\s|mv\s|rm\s|install\s")
ALLOW_MARKER = "file-ownership-allowed:"
BASELINE_REL = "tests/contract/file-ownership-baseline.json"
GATE_DIR = "tests/contract/"


class CannotJudge(Exception):
    """Undecidable ⇒ rc=2 (fail-closed)."""


 # ── Small utilities ────────────────────────────────────────────────────────────────────

def _literal_in(node: ast.AST, literals: tuple) -> str:
    """Target file name literals appearing in the subtree (including string constants from concatenation/format)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            for lit in literals:
                if lit in n.value:
                    return lit
    return ""


def _names_in(node: ast.AST) -> set:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def _write_targets(call: ast.Call) -> list:
    """Is this call a "file write"? If so, return the list of arguments to check (otherwise empty).

    ``open(path, 'w')`` → [path];``p.write_text(x)`` → [p];``os.remove(p)`` → [p]。
    """
    fn = call.func
    if isinstance(fn, ast.Attribute):
        recv = fn.value.id if isinstance(fn.value, ast.Name) else ""
        name = fn.attr
        if name == "open":                       # Path.open and built-in open judged the same
            modes = [a.value for a in call.args[1:] if isinstance(a, ast.Constant)
                     and isinstance(a.value, str)]
            if any(any(c in m for c in "wax+") for m in modes):
                return list(call.args[:1])
            return []
        if recv in MODULE_WRITE_CALLS and name in MODULE_WRITE_CALLS[recv]:
            return list(call.args)
        if name in PATH_WRITE_METHODS:
            return [fn.value]
        return []
    if isinstance(fn, ast.Name) and fn.id == "open":
        modes = [a.value for a in call.args[1:] if isinstance(a, ast.Constant)
                 and isinstance(a.value, str)]
        if any(any(c in m for c in "wax+") for m in modes):
            return list(call.args[:1])
    return []


# ── Python determination (scope-aware taint propagation) ────────────────────────────────────────

_SCOPE_NODES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                ast.ClassDef)


def _parents_and_scope(tree: ast.AST):
    parents: dict = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node

    def scope_of(node):
        cur = node
        while cur is not None:
            if isinstance(cur, _SCOPE_NODES):
                return cur
            cur = parents.get(cur)
        return tree
    return parents, scope_of


def _ordered(node: ast.AST):
    """Depth-first in source code order (parent statements before child statements ⇒ assignment before use)."""
    yield node
    for ch in ast.iter_child_nodes(node):
        yield from _ordered(ch)


def _py_hits(text: str, literals: tuple, api_rule: bool) -> tuple:
    """(hits, allowed) —— Hit list for the Python side; each entry is (lineno, kind, detail, source)."""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [(e.lineno or 1, "unparsable", "(syntax)", f"{e.msg}")], []
    lines = text.splitlines()
    parents, scope_of = _parents_and_scope(tree)
    taint: dict = {}          # scope node -> {names}
    tainted_funcs: set = set()  # Function names returning tainted values (taint sources)
    hits: list = []
    allowed: list = []

    def _src(node) -> str:
        ln = getattr(node, "lineno", 0) or 0
        return lines[ln - 1].strip() if 0 < ln <= len(lines) else ""

    def _record(node, kind: str, detail: str) -> None:
        src = _src(node)
        row = ((getattr(node, "lineno", 0) or 1), kind, detail, src)
        (allowed if ALLOW_MARKER in src else hits).append(row)

    def _visible(node) -> set:
        """Tainted names visible to this node (current scope → outer scope → module, union with tainted function names)."""
        out = set(tainted_funcs)
        cur = scope_of(node)
        seen = set()
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            out |= taint.get(id(cur), set())
            cur = parents.get(cur)
        return out

    # 0) Bind the import alias for the write API (`save_agent_config as _sac` from cli/repair.py)
    aliases: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name in BINDING_WRITE_APIS:
                    aliases[a.asname or a.name] = a.name
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name in BINDING_WRITE_APIS:
                    aliases[(a.asname or a.name).split(".")[-1]] = a.name

    for node in _ordered(tree):
        # 1) Assignment within scope ⇒ tainted (no name leakage across scopes)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = getattr(node, "value", None)
            if value is None:
                continue
            keys = _names_in(value)
            tgt = getattr(node, "targets", None) or [getattr(node, "target", None)]
            sc = id(scope_of(node))
            body = taint.setdefault(sc, set())
            if _literal_in(value, literals) or (keys & _visible(node)):
                for t in tgt:
                    if t is not None:
                        body |= _names_in(t)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sc = id(node)
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                    v = getattr(sub, "value", None)
                    if v is not None and (_literal_in(v, literals)
                                          or (_names_in(v) & taint.get(sc, set()))):
                        tainted_funcs.add(node.name)
                        break
                if isinstance(sub, ast.Return) and sub.value is not None:
                    if _literal_in(sub.value, literals) or (
                            _names_in(sub.value) & taint.get(sc, set())):
                        tainted_funcs.add(node.name)
                        break
        # 2) Write call + (literal | tainted name)
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else "")
            if api_rule and (name in BINDING_WRITE_APIS or name in aliases):
                _record(node, "helper-write", f"{name}() writes the binding file")
            _targets = _write_targets(node)
            if not _targets and name in ATOMIC_WRITE_HELPERS:
                _targets = list(node.args[:1])   # Rule (d): first argument of helper = path position
            for arg in _targets:
                lit = _literal_in(arg, literals)
                if lit:
                    _record(node, "write", f"literal {lit}")
                    continue
                hit_names = _names_in(arg) & _visible(node)
                if hit_names:
                    _record(node, "write(tainted path)",
                            f"tainted name(s) {sorted(hit_names)}")
    return hits, allowed


# ── TypeScript / Shell determination ───────────────────────────────────────────────────

def _strip_ts(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"//[^\n]*", "", code)


def _ts_hits(text: str, literals: tuple) -> tuple:
    hits: list = []
    allowed: list = []
    tainted: set = set()
    for i, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            allowed.append((i, "marker", "line annotated", line.strip()))
            continue
        lit = _literal_in_line(line, literals)
        if TS_WRITE_RE.search(line):
            if lit:
                hits.append((i, "write", f"literal {lit}", line.strip()))
            else:
                hit_names = {t for t in tainted
                             if re.search(r"\b%s\b" % re.escape(t), line)}
                if hit_names:
                    hits.append((i, "write(tainted path)",
                                 f"tainted name(s) {sorted(hit_names)}", line.strip()))
        if lit:
            m = re.match(r"\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*[=:]", line)
            if m:
                tainted.add(m.group(1))
    return hits, allowed


def _shell_hits(text: str, literals: tuple) -> tuple:
    hits: list = []
    allowed: list = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if ALLOW_MARKER in line:
            allowed.append((i, "marker", "line annotated", line.strip()))
            continue
        lit = _literal_in_line(line, literals)
        if lit and SHELL_WRITE_RE.search(line):
            hits.append((i, "write(shell)", f"literal {lit}", line.strip()))
    return hits, allowed


def _literal_in_line(line: str, literals: tuple) -> str:
    for lit in literals:
        if lit in line:
            return lit
    return ""


# ── Scanning ──────────────────────────────────────────────────────────────────────

def _looks_python(path: Path) -> bool:
    if path.suffix == ".py":
        return True
    if path.suffix:
        return False
    try:
        with open(path, "rb") as fh:
            first = fh.readline(200).decode("utf-8", "replace")
    except OSError:
        return False
    return first.startswith("#!") and "python" in first


def scan(root: Path) -> tuple:
    """(hits, scanned, allowed);hits = [(rel, lineno, rule, kind, detail, src)]。"""
    hits: list = []
    allowed: list = []
    scanned = 0
    for rule, sub, literals in RULES:
        base = root / sub.rstrip("/")
        if not base.is_dir():
            continue
        api_rule = rule == "cli-writes-agent-binding"
        for f in sorted(base.rglob("*")):
            if not f.is_file() or "__pycache__" in f.parts or "node_modules" in f.parts:
                continue
            rel = f.relative_to(root).as_posix()
            if rel.startswith(GATE_DIR):
                continue
            is_py = _looks_python(f)
            is_ts = f.suffix in (".ts", ".tsx", ".mts", ".cts")
            is_sh = f.suffix in (".sh", ".bash")
            if not (is_py or is_ts or is_sh):
                continue
            try:
                raw = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            if is_py:
                found = _py_hits(raw, literals, api_rule)
            elif is_ts:
                found = _ts_hits(_strip_ts(raw), literals)
            else:
                found = _shell_hits(raw, literals)
            found_hits, found_allowed = found
            for ln, kind, detail, src in found_hits:
                hits.append((rel, ln, rule, kind, detail, src))
            for ln, kind, detail, src in found_allowed:
                allowed.append((rel, ln, rule, kind, detail, src))
    return hits, scanned, allowed


def load_baseline(root: Path) -> dict:
    p = root / BASELINE_REL
    if not p.is_file():
        raise CannotJudge(f"Baseline missing: {BASELINE_REL}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise CannotJudge(f"Baseline is not valid JSON: {BASELINE_REL} ({e})") from e
    if not isinstance(data.get("entries"), dict):
        raise CannotJudge(f"Baseline missing entries mapping: {BASELINE_REL}")
    return data


def main(argv: list) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--strict", action="store_true",
                    help="Baseline items also count as FAIL (use after clearing baseline)")
    ap.add_argument("--suggest-baseline", action="store_true",
                    help="Only print the current hit baseline JSON skeleton (reason left as TODO, do not write file)")
    args = ap.parse_args(argv[1:])
    root = Path(args.repo).resolve()
    if not (root / "pysdk").is_dir() or not (root / "cli").is_dir():
        print(f"CANNOT JUDGE: {root} cannot find pysdk/ and cli/", file=sys.stderr)
        return 2
    try:
        baseline = load_baseline(root)
    except CannotJudge as e:
        print(f"CANNOT JUDGE: {e}", file=sys.stderr)
        return 2

    hits, scanned, allowed = scan(root)
    known = baseline["entries"]

    def _key(rel: str, rule: str, kind: str, src: str) -> str:
        """Hit identity = file::rule::write_call_kind::source_line (line numbers drift, snippets do not)."""
        return f"{rel}::{rule}::{kind}|{src[:80]}"

    if args.suggest_baseline:
        entries: dict = {}
        for rel, ln, rule, kind, detail, src in hits:
            k = _key(rel, rule, kind, src)
            entries.setdefault(k, {"line": ln, "detail": detail,
                                   "count": 0, "reason": "TODO: <why is it still here>"})
            entries[k]["count"] += 1
        print(json.dumps({"entries": entries}, ensure_ascii=False, indent=1))
        return 0

    by_key: dict = {}
    for rel, ln, rule, kind, detail, src in hits:
        by_key.setdefault(_key(rel, rule, kind, src), []).append((ln, kind, detail, src))

    new: dict = {}
    stale: list = []
    for key, rows in sorted(by_key.items()):
        if key not in known:
            new[key] = rows
    for key, meta in sorted(known.items()):
        if key not in by_key:
            stale.append((key, meta))

    print(f"[file-ownership] scanned {scanned} source file(s) — "
          f"pysdk/** + tssdk/** vs {'/'.join(GATEWAY_FILE_LITERALS)}, "
          f"cli/** vs {'/'.join(BINDING_FILE_LITERALS)};   reads are ALWAYS allowed")
    if by_key:
        print(f"[file-ownership] {len(by_key)} baselined location(s) — GAP "
              f"(implementation ≠ ruling; reported, never hidden):")
        for key, rows in sorted(by_key.items()):
            rel, rule = key.split("::")[:2]
            meta = known.get(key) or {}
            tag = "NEW" if key in new else "known"
            impl = meta.get("impl", "unknown")
            print(f"  [{tag}/{impl}] {rel}  ({rule})  {len(rows)} hit(s) — {RULE_WHY[rule]}")
            if meta.get("reason"):
                print(f"       reason on record: {meta['reason']}")
            for ln, kind, detail, src in rows:
                print(f"      {rel}:{ln}: {kind} [{detail}]  |  {src[:130]}")
    if allowed:
        print(f"[file-ownership] {len(allowed)} line(s) allowed by an inline marker "
              f"({ALLOW_MARKER} <reason>) — visible, not silent:")
        for rel, ln, rule, kind, detail, src in allowed:
            print(f"      {rel}:{ln}: {src[:130]}")
    if stale:
        print("[file-ownership] stale baseline entr(ies) — no longer hits, drop them:")
        for key, meta in stale:
            print(f"  · {key} (was {meta.get('count')})")

    if new:
        print(f"[file-ownership] VIOLATION — {len(new)} new location(s) write the file "
              f"the other side owns:", file=sys.stderr)
        for key, rows in sorted(new.items()):
            rel, rule = key.split("::")[:2]
            print(f"  ✘ {rel} ({rule}) — {RULE_WHY[rule]}", file=sys.stderr)
            for ln, kind, detail, src in rows:
                print(f"      {rel}:{ln}: {kind} [{detail}]  |  {src[:130]}",
                      file=sys.stderr)
        print("  fix: write it through the owner (SDK → binding file, CLI → system env "
              "file), or annotate the line with `file-ownership-allowed: <reason>`.",
              file=sys.stderr)
        return 1
    if args.strict and by_key:
        print(f"[file-ownership] VIOLATION (--strict): {len(by_key)} baselined "
              f"location(s) still write the other side's file", file=sys.stderr)
        return 1
    if not by_key:
        print("[file-ownership] clean: no write of the other side's file in any "
              "scanned tree")
    else:
        print(f"[file-ownership] no NEW hit; {len(by_key)} baselined GAP(s) above "
              f"(see {BASELINE_REL})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-file-ownership.py — 文件归属静态断言 (L0)。

owner 分层裁决(2026-09-28, 承接 Phase 1/2a 的"SDK 去桥化"):

    系统级环境文件  ``aimail_gateway.json``  ← 只由 **CLI** 写
    per-agent 绑定文件 ``agentmail.json``     ← 只由 **SDK** 写
    只读永远是允许的(两个方向都允许读)。

本脚本把该裁决变成可否证的静态断言 —— 每条目带"文件名字面量 + 写调用"**双重证据**,
只读(``open`` 无写模式 / ``read_text`` / ``json.load`` / ``.is_file()`` / ``glob``)一律不判:

  (a) SDK 面(``pysdk/**`` + ``tssdk/**``)**不得写** ``aimail_gateway.json``。
      SDK 只读它(取 gateway_url/admin_key/…)—— 写它 = SDK 越界替 CLI 管环境。
  (b) CLI 面(``cli/**``)**不得写** ``agentmail.json``。
      CLI 只读它(SDK 权限的 per-agent 事实源)—— 写它 = CLI 越界替 SDK 管身份。
  (c) 帮助函数面: 通过 SDK 的"绑定文件原子写"API(``save_agent_config``;含
      ``import … as alias`` 别名)写绑定文件, 在 CLI 面同样是 (b) 的越界写 —— 证据是
      **调用名**而不是文件名字面量(实测 ``cli/repair.py`` 走这条路, 只看字面量会漏判)。

判定手法(诚实登记近似性):
  · Python: ``ast`` + **按作用域**的轻量污点传播。字面量 ⇒ 赋值目标被汚染(同名跨作用域
    不串), ``x = y`` 传播, 返回被汚染值的函数名算汚染源;写调用的实参含字面量或汚染名
    ⇒ 命中。写调用白名单是**文件语义**专用的(``open(mode含w/a/x/+)``/``write_text``/
    ``write_bytes``/``unlink``/``touch``/``rename``/``mkdir*``/``rmtree``/``copy*``/
    ``move`` + ``os.remove``/``os.replace``) —— ``str.replace`` 这类同名但无关的方法
    不判(否则满屏假命中)。**不做跨模块数据流**(跨模块靠规则 (c) 与基线人工登记)。
  · TypeScript(``tssdk/**/*.ts``): 剥注释后按行扫, 同上的"赋值即汚染" + 写 API 名单。
  · Shell(``cli/**/*.sh``): 同行的字面量 + 重定向/``tee``/``sed -i``/``cp``/``mv``/``rm``。
  · 注释里的文件名不算引用(退役/说明是文档); 行内逃生门 ``file-ownership-allowed: <理由>``
    必须带理由, 被放过的行**单独成节打印**(``allowed by marker``)= 可见, 不静默。

基线(``tests/contract/file-ownership-baseline.json``): 与字面量棘轮同法 —— **只许减不许增**。
当下已知的实现↔裁决不符项(带 reason)登记在基线里, 门禁把它们当 GAP **大声打印**而不是假装
全绿; 任何**新增**越界写 ⇒ FAIL(rc=1); 基线项已消失 ⇒ 打印 stale 提示(应删该条)。
``--strict`` 让基线项也 FAIL(把基线清零时用)。

退出码: 0 = 无新增命中(基线内 GAP 原样打印); 1 = 有新增命中/无法解析; 2 = 判不了(路径读不到)。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# ── 受管文件与两个方向 ─────────────────────────────────────────────────────────
GATEWAY_FILE_LITERALS = ("aimail_gateway.json", "aimail-gateway.json")
BINDING_FILE_LITERALS = ("agentmail.json",)

#: (规则 id, 扫描根, 目标文件名字面量, 说明)
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

#: 规则 (c): 经这些 API 写绑定文件 = 越界写(证据 = 调用名;别名也认)
BINDING_WRITE_APIS = ("save_agent_config", "saveAgentConfig", "writeAgentConfig",
                      "write_agent_config")

#: 文件语义专用的写调用(同名的 str/list 方法不在内 —— 见模块 docstring)
PATH_WRITE_METHODS = {
    "write_text", "write_bytes", "unlink", "touch", "rmdir",
    "rename", "replace", "remove", "rmtree", "copy", "copyfile", "copy2", "move",
    "mkdir", "makedirs", "link", "symlink_to", "truncate",
}
#: 只有 ``模块.方法(...)`` 形态才算的(os.remove 是文件, list.remove 不是)
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
    """判不了 ⇒ rc=2(fail-closed)。"""


# ── 小工具 ────────────────────────────────────────────────────────────────────

def _literal_in(node: ast.AST, literals: tuple) -> str:
    """子树里出现的目标文件名字面量(拼接/format 的字符串常量都算)。"""
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
    """这个调用是不是"写文件"? 是则返回要检查的实参列表(否则空)。

    ``open(path, 'w')`` → [path];``p.write_text(x)`` → [p];``os.remove(p)`` → [p]。
    """
    fn = call.func
    if isinstance(fn, ast.Attribute):
        recv = fn.value.id if isinstance(fn.value, ast.Name) else ""
        name = fn.attr
        if name == "open":                       # Path.open 与内建 open 同判
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


# ── Python 判定(作用域感知的污点传播) ────────────────────────────────────────

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
    """按源码顺序深度优先(父语句先于子语句 ⇒ 赋值先于使用)。"""
    yield node
    for ch in ast.iter_child_nodes(node):
        yield from _ordered(ch)


def _py_hits(text: str, literals: tuple, api_rule: bool) -> tuple:
    """(hits, allowed) —— Python 面的命中清单;每条 (lineno, kind, detail, source)。"""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [(e.lineno or 1, "unparsable", "(syntax)", f"{e.msg}")], []
    lines = text.splitlines()
    parents, scope_of = _parents_and_scope(tree)
    taint: dict = {}          # scope node -> {names}
    tainted_funcs: set = set()  # 返回被汚染值的函数名(汚染源)
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
        """该节点可见的汚染名(本作用域 → 外层 → 模块, 并上汚染函数名)。"""
        out = set(tainted_funcs)
        cur = scope_of(node)
        seen = set()
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            out |= taint.get(id(cur), set())
            cur = parents.get(cur)
        return out

    # 0) 绑定写 API 的 import 别名(cli/repair.py 的 `save_agent_config as _sac`)
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
        # 1) 作用域内赋值 ⇒ 汚染(不跨作用域串名)
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
        # 2) 写调用 + (字面量 | 汚染名)
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else "")
            if api_rule and (name in BINDING_WRITE_APIS or name in aliases):
                _record(node, "helper-write", f"{name}() writes the binding file")
            for arg in _write_targets(node):
                lit = _literal_in(arg, literals)
                if lit:
                    _record(node, "write", f"literal {lit}")
                    continue
                hit_names = _names_in(arg) & _visible(node)
                if hit_names:
                    _record(node, "write(tainted path)",
                            f"tainted name(s) {sorted(hit_names)}")
    return hits, allowed


# ── TypeScript / Shell 判定 ───────────────────────────────────────────────────

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


# ── 扫描 ──────────────────────────────────────────────────────────────────────

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
        raise CannotJudge(f"基线缺失: {BASELINE_REL}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise CannotJudge(f"基线不是合法 JSON: {BASELINE_REL} ({e})") from e
    if not isinstance(data.get("entries"), dict):
        raise CannotJudge(f"基线缺 entries 映射: {BASELINE_REL}")
    return data


def main(argv: list) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--strict", action="store_true",
                    help="基线项也算 FAIL(基线清零后用)")
    ap.add_argument("--suggest-baseline", action="store_true",
                    help="只打印当前命中的基线 JSON 骨架(reason 留 TODO, 不写文件)")
    args = ap.parse_args(argv[1:])
    root = Path(args.repo).resolve()
    if not (root / "pysdk").is_dir() or not (root / "cli").is_dir():
        print(f"CANNOT JUDGE: {root} 下找不到 pysdk/ 与 cli/", file=sys.stderr)
        return 2
    try:
        baseline = load_baseline(root)
    except CannotJudge as e:
        print(f"CANNOT JUDGE: {e}", file=sys.stderr)
        return 2

    hits, scanned, allowed = scan(root)
    known = baseline["entries"]

    def _key(rel: str, rule: str, kind: str, src: str) -> str:
        """命中身份 = 文件::规则::写调用种类::源码行(行号会漂, 片段不会)。"""
        return f"{rel}::{rule}::{kind}|{src[:80]}"

    if args.suggest_baseline:
        entries: dict = {}
        for rel, ln, rule, kind, detail, src in hits:
            k = _key(rel, rule, kind, src)
            entries.setdefault(k, {"line": ln, "detail": detail,
                                   "count": 0, "reason": "TODO: <为什么现在还在>"})
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

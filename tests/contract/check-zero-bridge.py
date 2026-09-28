#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-zero-bridge.py — 零桥符号棘轮 (L0, 静态负断言)。

owner 裁决 2026-09-28(**SDK 去桥化**): 环境(桥/路由)由 CLI 自持
(``cli/bridge_wire.py``; 入口 = 宿主 best-effort 通知 ``aimail address -a <addr>
--inbound-live|--inbound-down``)。SDK **不谈桥**: 它不得定义/调用/导入任何桥符号。

三条规则(全部为"命中 = 0"的负断言):

  (a) ``pysdk/**/*.py``: AST 扫描标识符(变量/属性/函数/类/参数/导入别名)——
      **注释与文档字符串不算引用**(退役说明是文档, 不是引用)。
  (b) ``tssdk/packages/*/src/**/*.ts``(发布面): 剥掉块注释/行注释后扫描;
      另扫 ``tssdk/test/**/*.ts``,**排除** ``startup-hook.test.ts``
      (它是棘轮自身的符号表 —— 那些字面量是断言数据, 不是引用)。
  (c) 契约真源不得含桥键: ``contract/aimail-contract.json`` 的**键**、
      ``pysdk/aimail_contract.py`` 与
      ``tssdk/packages/mail-core/src/contract.ts`` 的常量名。

退出码: 0 = 全绿; 1 = 有命中(逐条给 file:line); 2 = 判不了(路径读不到)。
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
#: 契约真源里不许出现的桥键(键名 / 常量名)。
FORBIDDEN_CONTRACT_KEYS = ("bridge_default_path", "BRIDGE_DEFAULT_PATH", "bridgeDefaultPath")
TS_RATCHET_FILE = "tssdk/test/startup-hook.test.ts"
CONTRACT_JSON = "contract/aimail-contract.json"
CONTRACT_PY = "pysdk/aimail_contract.py"
CONTRACT_TS = "tssdk/packages/mail-core/src/contract.ts"


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
        # 常量**定义**(赋值/声明)才算, 注释/文档字符串不算
        if re.search(r"^\s*%s\s*[:=]" % re.escape(sym), py_txt, re.M):
            hits.append(f"{CONTRACT_PY}: constant {sym} defined")
        if re.search(r"^\s*(export\s+)?const\s+%s\b" % re.escape(sym), ts_txt, re.M):
            hits.append(f"{CONTRACT_TS}: constant {sym} declared")
    return hits


def main(argv: list) -> int:
    repo = Path(argv[1] if len(argv) > 1 else ".").resolve()
    if not (repo / "pysdk").is_dir():
        print(f"CANNOT JUDGE: {repo}/pysdk not found", file=sys.stderr)
        return 2
    hits = rule_a(repo) + rule_b(repo) + rule_c(repo)
    if hits:
        print("[zero-bridge] VIOLATION — the SDK must speak no bridge "
              f"({len(hits)} hit(s)):", file=sys.stderr)
        for h in hits:
            print(f"  ✘ {h}", file=sys.stderr)
        return 1
    print(f"[zero-bridge] clean: pysdk/*.py identifiers ∩ {len(RETIRED_PY)} retired = 0; "
          f"tssdk packages/*/src + test/*.ts ∩ {len(RETIRED_TS)} retired = 0; "
          f"contract truth source carries no bridge key")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

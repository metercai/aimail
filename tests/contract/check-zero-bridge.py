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

  (d) **引用侧**(2026-09-28 补, 定义侧的漏网面): ``pysdk/**`` ``tssdk/**``
      ``cli/**`` ``tests/**`` 里, 退役符号不得被**引用** ——
      import / 别名 / 成员访问 / 定义名 / 参数名 / 动态调用名(``getattr(x, "sym")`` 形态)。
      扫描面 = 白名单扩展 **.py/.ts/.sh/.md** **∪ 无扩展名可执行/shebang 脚本**
      (2026-09-28 二次补: 扩展名白名单曾把无后缀的主入口 ``cli/aimail`` 整个跳过 ——
      它的死引用 ``from aimail_base import ensure_bridge_routes_for_system`` 又躲过一轮,
      门禁 L1 101 PASS/12 FAIL 全败于该 ImportError)。
      根因: (a)(b) 只守 SDK 自己的文件; 别处 ``from aimail_base import is_bridge_host_port``
      这种"import 一个已删除的符号"完全漏网, 而 **pyflakes 不报"从模块 import 不存在的
      名字"** ⇒ L0 假绿(实测: cli/setup_system.py + cli/repair.py 两处死引用, 门禁 L1
      45 红全败于同一个 ImportError)。
      判据细则(与 (a)(b) 同法): **注释与文档字符串不算引用**; 字符串字面量按**数据**处理
      (配置键 / 桥 API 路径 / 符号表都是数据) —— 只有被当**调用名**取用的字符串才算引用。
      行内逃生门: 该行含显式 ``retired:`` 说明标记 ⇒ 单独成节打印, 不算引用; 但**可执行
      代码**上的标识符命中**不允许**用标记豁免(标记只豁免注释/数据行)。
      生成物与第三方目录(``node_modules`` ``__pycache__`` ``lib`` ``dist``)不扫。

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

#: ── (d) 引用侧 ────────────────────────────────────────────────────────────────
#: 扫描范围: 退役符号**被引用的地方**(定义侧只守 SDK 自己, 这里守"谁在 import/调用它")。
REF_SCAN_DIRS = ("pysdk", "tssdk", "cli", "tests")
REF_SCAN_EXT = (".py", ".ts", ".sh", ".md")
#: **无扩展名脚本**(2026-09-28 第二次漏网, 补面): 扩展名白名单会把没有后缀的主入口
#: 直接跳过 —— CLI 主入口 ``cli/aimail``(无后缀, shebang ``#!/usr/bin/env python3``)
#: 里的死引用又躲过一轮: ``cli/aimail:2091``
#: ``from aimail_base import (ensure_bridge_routes_for_system, ...)`` ⇒ ImportError ⇒
#: 门禁 L1 101 PASS / 12 FAIL 全败于同一处。判据 = 白名单扩展 **∪**
#: (无后缀 且 (可执行位 或 首行 ``#!``) 且 文本 且 <= REF_SCAN_MAX_BYTES);
#: 按 shebang 选解析法(见 ``_ref_kind``: python → AST、sh → 剥行注释、其它 → 文本 token)。
REF_SCAN_EXTENSIONLESS = True
REF_SCAN_MAX_BYTES = 1_000_000
#: 不扫生成物/第三方(源真源在 src/; 见 docstring)。
REF_SKIP_DIRS = {"node_modules", "__pycache__", ".git", "lib", "dist", "build"}
#: 逃生门标记: 行内含该显式注释才允许"提及退役符号"(说明行 / 符号表), 单独成节打印。
ESCAPE_MARKER = "retired:"
#: "字符串调用名"形态: 符号名以字符串形式被当**调用/属性名**取用(动态调用), 而不是数据。
DYNAMIC_NAME_HINTS = ("getattr(", "setattr(", "import_module(", "__import__(",
                      "globals()[", "require(", "import(")
#: 全部退役符号(py 定义面 + ts 定义面), 引用侧对两者都判。
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
        # 常量**定义**(赋值/声明)才算, 注释/文档字符串不算
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
    """白名单扩展 **∪** 无扩展名可执行/shebang 脚本(见 REF_SCAN_EXTENSIONLESS 的漏网记录)。"""
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
    """引用侧扫描面: REF_SCAN_DIRS × (REF_SCAN_EXT ∪ 无扩展名脚本), 跳过生成物/第三方目录。"""
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
    """扫描模式: ``py``(AST) / ``ts``(剥注释+字符串) / ``sh``(剥行注释) / ``generic``(整文本 token)。

    无扩展名脚本按**首行 shebang** 选法 —— 判据与同后缀文件一致:
    python ⇒ AST(注释与文档字符串天然出局, 与 (a) 同法)、sh/bash/zsh/dash ⇒ 剥行注释、
    其它 ⇒ 文本 token(与 .md 同法: 只有 ``retired:`` 标记豁免)。
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
    """[(lineno, symbol, line)] —— 整词标识符命中(两边不许是标识符字符)。"""
    hits: list = []
    pat = {s: re.compile(r"(?<![A-Za-z0-9_$])%s(?![A-Za-z0-9_$])" % re.escape(s))
           for s in syms}
    for i, line in enumerate(text.splitlines(), 1):
        for s, p in pat.items():
            if p.search(line):
                hits.append((i, s, line.strip()))
    return hits


def _py_code_hits(rel: str, text: str) -> tuple:
    """(violations, benign) —— Python 引用侧。

    违规 = **代码标识符**命中: import 名/别名、成员访问(``mod.sym``)、裸名、定义名、
    参数名 —— 即"这个名字真的被当作符号用"。注释与文档字符串天然出局(AST 里没有注释;
    字符串常量单独判)。
    benign = 同名的**字符串字面量**(配置键 / 桥 API 路径 / 符号表 / 说明文字)= 数据;
    唯一例外: 字符串被当**调用名**取用(``getattr(x, "sym")`` 形态)⇒ 违规。
    """
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as e:
        return [(1, "", f"{rel}: unparsable ({e})")], []
    lines = text.splitlines()
    viol: list = []
    for node in ast.walk(tree):
        # 一个节点可能带**两个**名字(import 的原名 + as 别名), 两个都是引用。
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
    """TS: 剥注释 + 剥字符串字面量 ⇒ 剩下的才是**代码标识符**(字符串是数据)。

    剥字符串用等长占位, 行号保持对齐。符号表(startup-hook.test.ts 那类)里的名字
    因此天然出局 —— 它们是断言数据, 不是引用。
    """
    code = _strip_ts_comments(text)
    code = re.sub(r"`(?:\\.|[^`\\])*`", "''", code, flags=re.S)
    code = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", code)
    code = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', code)
    return code


def _sh_code(text: str) -> str:
    """shell: 只剥行注释; **保留**字符串(``python3 -c "from aimail_base import x"`` 是真引用)。"""
    return "\n".join(re.sub(r"(^|\s)#.*$", r"\1", ln) for ln in text.splitlines())


def rule_d(repo: Path) -> tuple:
    """(violations, escapes, benign) —— 引用侧棘轮(退役符号不得被引用)。"""
    violations: list = []
    escapes: list = []
    benign: list = []
    scanned = 0
    for f in _iter_ref_files(repo):
        rel = f.relative_to(repo).as_posix()
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            violations.append(f"{rel}: unreadable ({e})")
            continue
        scanned += 1
        kind = _ref_kind(f, text)
        if kind == "py":
            hits, ben = _py_code_hits(rel, text)
            benign += ben
        elif kind == "ts":
            hits = [(i, s, f"{rel}:{i}: ts identifier {s}")
                    for i, s, _ in _token_hits(_ts_code(text))]
            # TS 侧字符串: 只判"动态调用名"形态(require('sym') / import('sym'))
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
        else:      # .md / 其它无扩展名脚本: 文本 token(与 .md 同法)
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
        # 纯注释里的显式说明行(不产生任何 code/string 命中)也要单独成节打印 ——
        # "允许提及"和"静默忽略"是两回事: 逃生门必须是可见的。
        for i, line in enumerate(src, 1):
            if i in escaped_lines or ESCAPE_MARKER not in line:
                continue
            if any(re.search(r"(?<![A-Za-z0-9_$])%s(?![A-Za-z0-9_$])" % re.escape(s), line)
                   for s in RETIRED_ALL):
                escapes.append(f"{rel}:{i}: documented mention carrying `{ESCAPE_MARKER}` "
                               f"[escape hatch: {line.strip()[:90]}]")
    return violations, escapes, benign, scanned


def main(argv: list) -> int:
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
    if hits or ref_hits:
        print(f"[zero-bridge] VIOLATION — the SDK must speak no bridge "
              f"({len(hits)} definition-side + {len(ref_hits)} reference-side hit(s)):",
              file=sys.stderr)
        for h in hits + ref_hits:
            print(f"  ✘ {h}", file=sys.stderr)
        return 1
    print(f"[zero-bridge] clean (definitions=0): pysdk/*.py identifiers ∩ "
          f"{len(RETIRED_PY)} retired = 0; tssdk packages/*/src + test/*.ts ∩ "
          f"{len(RETIRED_TS)} retired = 0; contract truth source carries no bridge key")
    print(f"[zero-bridge/references] clean (references=0): {scanned} file(s) scanned in "
          f"{'/'.join(REF_SCAN_DIRS)} ("
          f"{'/'.join(e.lstrip('.') for e in REF_SCAN_EXT)}"
          f"{' + extension-less executable/shebang scripts' if REF_SCAN_EXTENSIONLESS else ''}"
          f"; comments+docstrings excluded) "
          f"— {len(RETIRED_ALL)} retired symbol(s) referenced 0 time(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

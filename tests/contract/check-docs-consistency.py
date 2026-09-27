#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-docs-consistency.py — 文档 ↔ 实现一致性门禁(L0)。

用户裁决(2026-09-27): **文档承诺的能力必须被实现兜住** —— 文档里承诺的每件事
都要有对应实现, 否则就是把用户带沟里。本脚本对 `docs/agent-self-setup.md` 与
`docs/agent-self-setup_zh.md`(只覆盖 agent 级激活码那条路径)断言三件事:

  (a) 符号存在: 文档里反引号包着的标识符必须能在 ``pysdk`` / ``tssdk`` 里找到
      **定义**(函数/类/模块级常量/接口字段/类型成员), 否则 = 文档在教用户调用
      一个不存在的 API。抽取形态 = 单一标识符的 code span(允许尾部 ``()``);
      只取「小写蛇形(至少一个 ``_``)」或「非首位含大写的驼峰/帕斯卡」,
      全大写常量/环境变量名(``AIMAIL_*``)与裸小写词按形态排除(那不是符号)。
      非符号名(契约值/示例标记)走显式短白名单 ``NON_SYMBOLS``, 每条带理由。

  (b) 双语结构一致: en/zh 两份的标题结构必须逐条相同 —— 同一顺序上
      「层级 + 序号」一致(标题文本是译文, 故不按字面比), 否则两份文档讲的
      不是同一件事。

  (c) 契约值 == 单一真源: 文档里出现的契约值(入站路径、hermes 例外路径、
      agent 内部 skill/toolset 注册名、绑定文件名、指针文件名、各平台入站
      端口、agent 级绑定系统前缀)必须逐字等于单一真源清单
      ``contract/aimail-contract.json``(前缀另引 TS 侧常量事实源)。
      文档写错值(哪怕只差一个字符)⇒ 真源值在文档里**找不到** ⇒ 红。

退出码:
  0 = 通过;
  1 = 违约(每条打印 ``file:line``, 有确定违约项时优先于 2 —— 两者都非零);
  2 = 判不了(文档读不到、清单读不到/缺键、前缀事实源读不到)。

用法:
  python3 tests/contract/check-docs-consistency.py [--repo DIR]
        [--en docs/agent-self-setup.md] [--zh docs/agent-self-setup_zh.md]
  --en/--zh 覆盖被检文档路径(三态自验「文档读不到 ⇒ rc=2」用)。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ── 被检文档与真源 ─────────────────────────────────────────────────────────
DEFAULT_EN = "docs/agent-self-setup.md"
DEFAULT_ZH = "docs/agent-self-setup_zh.md"
MANIFEST_REL = "contract/aimail-contract.json"
#: agent 级绑定的 system_id 前缀 —— 实现侧事实源(文档讲"哪条路径被开启"要写出它)。
PREFIX_SRC = "tssdk/packages/mail-core/src/poll-entry.ts"
PREFIX_CONST = "AGENT_SCOPE_SYSTEM_PREFIX"

#: 文档必须逐字引用的契约键(值型字符串键; 缺任一 = 文档与真源不一致)。
CONTRACT_KEYS = (
    "inbound_path", "hermes_inbound_path", "agent_skill_name",
    "binding_file", "pointer_file",
)

# ── 非符号名白名单(短; 每条带理由)──────────────────────────────────────────
NON_SYMBOLS = {
    # 契约**值**(不是 SDK 符号): agent 级绑定的 system_id 前缀, 其值由
    # poll-entry.ts 的 AGENT_SCOPE_SYSTEM_PREFIX 定义, 文档必须写出来。
    "shared_addr_": "agent 级绑定 system_id 前缀(契约值, 非 SDK 符号)",
    # 网关侧申请/激活响应字段名(本仓只在 cli/aimail 里消费, 不由 pysdk/tssdk
    # 定义): 文档讲"邮箱有效期口径"必须写它。
    "validity_days": "网关激活响应字段名(SDK 不定义, 见 cli/aimail 消费处)",
}

# ── 输出 ───────────────────────────────────────────────────────────────────
_VIOLATIONS: list = []


class CannotJudge(Exception):
    """判不了 ⇒ rc=2(fail-closed, 绝不假装通过)。"""


def out(msg: str = "") -> None:
    print(msg)


def viol(rule: str, msg: str) -> None:
    _VIOLATIONS.append((rule, msg))


# ── 文本工具: 反引号 span / 围栏 / 标题 ────────────────────────────────────
def _strip_fences(text: str) -> list:
    """返回 [(lineno, line, in_fence)] —— 围栏里的行不参与标题判定。"""
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
#: 单一标识符形态(可带尾部 ()): 其余(含点/斜杠/空格)一律不取。
_IDENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\(\s*\))?$")
_ALL_CAPS = re.compile(r"^[A-Z0-9_]+$")


def _identifier_spans(text: str) -> list:
    """抽取「像代码标识符」的 code span ⇒ [(lineno, name, span)]。

    形态过滤(排除非符号的裸词/常量名):
      ① 小写蛇形: ``^[a-z][a-z0-9_]*$`` 且含 ``_``(如 pull_list)
      ② 驼峰/帕斯卡: 非首位有大写(如 startAgentPullEntries / GatewayClient)
      ③ 全大写常量/环境变量名由 ① ② 自然排除(AIMAIL_PULL_INTERVAL_MS)
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
    """返回 [(level, struct_key, title, lineno)] —— struct_key = 层级 + 序号。"""
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


# ── (a) 符号索引 ───────────────────────────────────────────────────────────
_PY_DEF = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(\w+)")
_PY_ASSN = re.compile(r"^([A-Za-z_]\w*)\s*(?::[^=]+)?=")
_TS_DEF = re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
                     r"(?:function|const|let|var|class|interface|type|enum)\s+(\w+)")
_TS_FN = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)")
_TS_FIELD = re.compile(r"^\s*([a-z_][A-Za-z0-9_]*)\??\s*:")


def build_symbol_index(repo: Path) -> dict:
    """name → 出处(file:line) 的定义索引(函数/类/常量/接口字段/类型成员)。"""
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
    out(f"[docs] (a) {rel}: 反引号标识符存在性")
    checked = 0
    for lineno, name, span in _identifier_spans(text):
        if name in NON_SYMBOLS:
            continue
        checked += 1
        where = idx.get(name)
        if where is None:
            viol("a", f"{rel}:{lineno} 反引号标识符 `{span}` 在 pysdk/tssdk 里没有定义"
                      f"(文档在教用户调用不存在的 API; 若它不是符号, 加进 NON_SYMBOLS "
                      f"并写理由)")
            out(f"  ✗ {rel}:{lineno}: `{span}` 找不到定义")
    out(f"  ✓ {rel}: 抽取 {checked} 个标识符, 全部有定义")


# ── (b) 双语标题结构 ───────────────────────────────────────────────────────
def check_structure(rel_en: str, text_en: str, rel_zh: str, text_zh: str) -> None:
    out("[docs] (b) en/zh 标题结构一致")
    h_en = _headings(text_en)
    h_zh = _headings(text_zh)
    k_en = [k for _, k, _, _ in h_en]
    k_zh = [k for _, k, _, _ in h_zh]
    if k_en == k_zh:
        out(f"  ✓ 标题结构逐条相同({len(k_en)} 条; 层级+序号)")
        return
    n = min(len(k_en), len(k_zh))
    detail = []
    for i in range(max(len(k_en), len(k_zh))):
        a = k_en[i] if i < len(k_en) else "(缺)"
        b = k_zh[i] if i < len(k_zh) else "(缺)"
        if a != b:
            la = h_en[i][3] if i < len(h_en) else 0
            lb = h_zh[i][3] if i < len(h_zh) else 0
            detail.append(f"{rel_en}:{la} `{a}` != {rel_zh}:{lb} `{b}`")
    viol("b", f"标题结构不一致(en {len(k_en)} 条 / zh {len(k_zh)} 条): " +
              "; ".join(detail[:5]) + (" …" if len(detail) > 5 else ""))
    for d in detail[:5]:
        out(f"  ✗ {d}")
    out(f"  ✗ en {len(k_en)} 条标题 / zh {len(k_zh)} 条(前 {n} 条逐条比到第一处分歧)")


# ── (c) 契约值 == 单一真源 ─────────────────────────────────────────────────
def load_manifest(repo: Path) -> dict:
    path = repo / MANIFEST_REL
    try:
        man = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise CannotJudge(f"单一真源清单读不到: {path} ({e.__class__.__name__}: {e})") from e
    except ValueError as e:
        raise CannotJudge(f"清单不是合法 JSON: {path} ({e})") from e
    missing = [k for k in CONTRACT_KEYS + ("inbound_ports",) if k not in man]
    if missing:
        raise CannotJudge(f"清单缺契约键: {path} 缺 {missing}")
    return man


def agent_scope_prefix(repo: Path) -> str:
    path = repo / PREFIX_SRC
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CannotJudge(f"前缀事实源读不到: {path} ({e.__class__.__name__}: {e})") from e
    m = re.search(r"export const %s\s*=\s*'([^']*)'" % PREFIX_CONST, src)
    if not m:
        raise CannotJudge(f"{path}: 找不到 `export const {PREFIX_CONST} = '…'`")
    return m.group(1)


def check_contract_values(rel: str, text: str, man: dict, prefix: str) -> None:
    out(f"[docs] (c) {rel}: 契约值 == 单一真源")
    nlines = len(text.splitlines())
    hits = 0
    for key in CONTRACT_KEYS:
        val = man[key]
        if val in text:
            hits += 1
            continue
        viol("c", f"{rel}:1 (全文 {nlines} 行未命中) 缺契约值 {key}={val!r} "
                  f"← 单一真源 {MANIFEST_REL}; 文档写的值必须逐字等于真源")
        out(f"  ✗ {rel}: 全文未命中契约值 {key}={val!r}")
    for plat, port in sorted(man["inbound_ports"].items()):
        if str(port) in text:
            hits += 1
            continue
        viol("c", f"{rel}:1 (全文 {nlines} 行未命中) 缺清单入站端口 "
                  f"{plat}={port} ← 单一真源 {MANIFEST_REL}[inbound_ports]")
        out(f"  ✗ {rel}: 全文未命中入站端口 {plat}={port}")
    if prefix in text:
        hits += 1
    else:
        viol("c", f"{rel}:1 (全文 {nlines} 行未命中) 缺 agent 级绑定前缀 {prefix!r} "
                  f"← 实现事实源 {PREFIX_SRC}:{PREFIX_CONST}")
        out(f"  ✗ {rel}: 全文未命中 agent 级绑定前缀 {prefix!r}")
    out(f"  ✓ {rel}: {hits}/{len(CONTRACT_KEYS) + len(man['inbound_ports']) + 1} "
        f"项契约值逐字命中真源")


# ── main ───────────────────────────────────────────────────────────────────
def _read(repo: Path, rel: str) -> str:
    path = (repo / rel) if not Path(rel).is_absolute() else Path(rel)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise CannotJudge(f"文档读不到: {path} ({e.__class__.__name__}: {e}) —— "
                          f"读不到就不判, 绝不假装通过") from e


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="AIMail 文档↔实现一致性门禁")
    ap.add_argument("--repo", default="", help="仓根(默认由本文件位置推导)")
    ap.add_argument("--en", default=DEFAULT_EN, help=f"英文文档(默认 {DEFAULT_EN})")
    ap.add_argument("--zh", default=DEFAULT_ZH, help=f"中文文档(默认 {DEFAULT_ZH})")
    args = ap.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve() if args.repo \
        else Path(__file__).resolve().parents[2]

    try:
        text_en = _read(repo, args.en)
        text_zh = _read(repo, args.zh)
        man = load_manifest(repo)
        prefix = agent_scope_prefix(repo)
        idx = build_symbol_index(repo)
    except CannotJudge as e:
        out(f"[docs] CANNOT JUDGE (rc=2): {e}")
        return 2

    out(f"[docs] 被检文档: {args.en} / {args.zh}")
    out(f"[docs] 符号索引: {len(idx)} 个定义(来自 pysdk/ + tssdk/packages/)")
    out(f"[docs] 单一真源: {MANIFEST_REL}")

    cannot: list = []
    for fn in (lambda: check_symbols(args.en, text_en, idx),
               lambda: check_symbols(args.zh, text_zh, idx),
               lambda: check_structure(args.en, text_en, args.zh, text_zh),
               lambda: check_contract_values(args.en, text_en, man, prefix),
               lambda: check_contract_values(args.zh, text_zh, man, prefix)):
        try:
            fn()
        except CannotJudge as e:
            cannot.append(str(e))
            out(f"[docs] CANNOT JUDGE: {e}")

    if _VIOLATIONS:
        out(f"[docs] FAIL — {len(_VIOLATIONS)} 项违约"
            + (f";另有 {len(cannot)} 项判不了" if cannot else "") + ":")
        for rule, msg in _VIOLATIONS:
            out(f"  ✗ ({rule}) {msg}")
        for c in cannot:
            out(f"  ? 判不了: {c}")
        return 1
    if cannot:
        out(f"[docs] CANNOT JUDGE (rc=2) — {len(cannot)} 项无法判定:")
        for c in cannot:
            out(f"  ? {c}")
        return 2
    out("[docs] PASS — 符号全部有定义, en/zh 标题结构一致, 契约值 == 单一真源")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except CannotJudge as _e:  # 兜底: 任何"判不了"都不许伪装成功
        print(f"[docs] CANNOT JUDGE (rc=2): {_e}")
        sys.exit(2)
    except Exception as _e:  # noqa: BLE001 — 未预期异常同样是"判不了"
        import traceback
        traceback.print_exc()
        print(f"[docs] CANNOT JUDGE (rc=2): 未预期异常 {_e.__class__.__name__}: {_e}")
        sys.exit(2)

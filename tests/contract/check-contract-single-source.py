#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-contract-single-source.py — AIMail agent 侧契约"不可漂移"门禁(L0)。

单一真源 = 仓根 ``contract/aimail-contract.json``。本脚本实现三条规则:

  (a) 常量与清单一致: TS ``tssdk/packages/mail-core/src/contract.ts`` 与
      Python ``pysdk/aimail_contract.py`` 的每个契约常量必须逐项 == 清单。
      **Rust 侧本批未覆盖**(gateway/bridge 两仓留下批)⇒ 输出里登记为 GAP。
  (b) 字面量白名单棘轮: 清单里的字面量只允许出现在白名单(清单自身、两个
      常量模块、本门禁目录、两份自举文档 `DOCS_WHITELIST`(理由见其注释:
      它们必须逐字引用契约值, 改由 check-docs-consistency.py 强校验)、以及带
      ``contract-allowed: <理由>`` 注释的行)。
      其余位置按"文件 × 字面量"计数, 与基线
      ``tests/contract/contract-literal-baseline.json`` 比对 —— **只许减不许增**;
      新增位置 ⇒ FAIL 并给 ``file:line`` + 当前/基线计数。
  (c) SKILL.md frontmatter 契约名: 单一真源 ``resources/skills/SKILL.md`` 与 4 份
      物化产物(pysdk + tssdk/{openclaw,dsh,pi}-aimail)的 ``name:`` / ``toolset:``
      必须 == 清单的 agent_skill_name / agent_toolset_name。
      (正是能抓住改名提交 d701dae(2026-09-03)那类破坏的检查。)

退出码:
  0 = 全绿;
  1 = 漂移或违约(有确定违约项时优先于 2 —— 两者都非零, 门禁都红);
  2 = 判不了(清单读不到/不可解析/缺键、物化产物尚未物化、git 不可用、基线缺失)。

纪律: 诊断输出一律原样打印(绝不 ``2>/dev/null`` 或 ``cut`` 吞掉);每条结论带
file:line 或命令原文。基线用 ``--update-baseline`` 生成(只在**有意**减少/新增
白名单后重跑)。

用法:
  python3 tests/contract/check-contract-single-source.py [--repo DIR]
        [--update-baseline] [--show-baseline-diff]
环境:
  AIMAIL_CONTRACT_MANIFEST 覆盖清单路径(三态自验"清单读不到 ⇒ rc=2"用)。
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
    "inbound_ports", "bridge_default_path",
)

# ── 白名单(字面量允许出现的文件;路径相对仓根)──────────────────────────────
TS_CONTRACT = "tssdk/packages/mail-core/src/contract.ts"
PY_CONTRACT = "pysdk/aimail_contract.py"
MANIFEST_REL = "contract/aimail-contract.json"
BASELINE_REL = "tests/contract/contract-literal-baseline.json"
GATE_DIR = "tests/contract/"          # 门禁自身目录(含基线)整体白名单
# 文档面白名单(2026-09-27, 用户裁决「文档能力必须被实现兜住」): docs/agent-self-setup
# 这两份自举文档**必须逐字引用**契约值(入站路径、注册名、绑定/指针文件名、端口、
# agent 级前缀)才讲得清闭环, 所以"字面量位置计数"这套棘轮对它们不适用; 它们改由
# 同目录的 check-docs-consistency.py 以「值 == 清单」逐字命中方式强校验(比位置计数
# 更强: 写错一个字符就红), 两套检查同在 L0 里跑, 覆盖不降级。
DOCS_WHITELIST = ("docs/agent-self-setup.md", "docs/agent-self-setup_zh.md")
WHITELIST_FILES = (MANIFEST_REL, TS_CONTRACT, PY_CONTRACT, BASELINE_REL) + DOCS_WHITELIST
ALLOW_MARKER = "contract-allowed:"    # 行内逃生门(必须带理由)

# ── 规则 (c): 5 份 SKILL.md(1 真源 + 4 物化产物)──────────────────────────
SKILL_SOURCE = "resources/skills/SKILL.md"
SKILL_MATERIALIZED = (
    "pysdk/resources/skills/SKILL.md",
    "tssdk/packages/openclaw-aimail/resources/skills/SKILL.md",
    "tssdk/packages/dsh-aimail/resources/skills/SKILL.md",
    "tssdk/packages/pi-aimail/resources/skills/SKILL.md",
)

# 清单键 → (TS 常量名, Python 常量名)
CONST_MAP = {
    "inbound_path": ("INBOUND_PATH", "INBOUND_PATH"),
    "hermes_inbound_path": ("HERMES_INBOUND_PATH", "HERMES_INBOUND_PATH"),
    "hermes_route_name": ("HERMES_ROUTE_NAME", "HERMES_ROUTE_NAME"),
    "agent_skill_name": ("AGENT_SKILL_NAME", "AGENT_SKILL_NAME"),
    "agent_toolset_name": ("AGENT_TOOLSET_NAME", "AGENT_TOOLSET_NAME"),
    "binding_file": ("BINDING_FILE", "BINDING_FILE"),
    "pointer_file": ("POINTER_FILE", "POINTER_FILE"),
    "inbound_ports": ("INBOUND_PORTS", "INBOUND_PORTS"),
    "bridge_default_path": ("BRIDGE_DEFAULT_PATH", "BRIDGE_DEFAULT_PATH"),
}

# 字面量扫描策略: 路径/文件名类 = 子串计数;单 token 的 skill/toolset 名 =
# 词边界计数(否则 agentmail.json / agentmail_home / .agentmail / AGENTMAIL_* 全被误算)。
TOKEN_KEYS = ("agent_skill_name", "agent_toolset_name")


def _token_re(lit: str):
    """词边界正则(按清单值动态构建, 不钉死字符串 ⇒ 契约改名门禁自动跟着走)。"""
    return re.compile(r"(?<![A-Za-z0-9_.\-])%s(?![A-Za-z0-9_.\-])" % re.escape(lit))


class CannotJudge(Exception):
    """判不了 ⇒ rc=2(fail-closed,绝不假装通过)。"""


# ── 输出 ───────────────────────────────────────────────────────────────────
_VIOLATIONS: list = []
#: (规则, 说明) —— 未覆盖/判不了项,门禁必须如实登记
_GAPS: list = []


def out(msg: str = "") -> None:
    print(msg)


def viol(rule: str, msg: str) -> None:
    _VIOLATIONS.append((rule, msg))


def gap(rule: str, msg: str) -> None:
    _GAPS.append((rule, msg))


# ── 清单 ───────────────────────────────────────────────────────────────────
def load_manifest() -> tuple[dict, str]:
    p = os.environ.get("AIMAIL_CONTRACT_MANIFEST", "").strip()
    path = Path(p).expanduser() if p else None
    if path is None:
        path = REPO / MANIFEST_REL
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CannotJudge(f"清单读不到: {path} ({e.__class__.__name__}: {e})") from e
    if not raw.strip():
        raise CannotJudge(f"清单为空: {path}")
    try:
        man = json.loads(raw)
    except ValueError as e:
        raise CannotJudge(f"清单不是合法 JSON: {path} ({e})") from e
    if not isinstance(man, dict):
        raise CannotJudge(f"清单顶层不是对象: {path} ({type(man).__name__})")
    missing = [k for k in REQUIRED_KEYS if k not in man]
    if missing:
        raise CannotJudge(f"清单缺契约键: {path} 缺 {missing}")
    ports = man["inbound_ports"]
    if not isinstance(ports, dict) or not ports:
        raise CannotJudge(f"清单 inbound_ports 不是非空对象: {path} ({ports!r})")
    return man, str(path)


# ── 规则 (a): 常量一致性 ───────────────────────────────────────────────────
def _ts_consts(path: Path) -> dict:
    """从 TS 常量模块抓 export const 值(字符串 + INBOUND_PORTS 对象)。"""
    found = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    str_re = re.compile(r"^export const ([A-Z][A-Z0-9_]*) = '([^']*)'")
    ports_re = re.compile(r"^export const ([A-Z][A-Z0-9_]*)[^=]*= \{([^}]*)\}")
    for i, line in enumerate(lines, 1):
        m = str_re.match(line)
        if m:
            found[m.group(1)] = (m.group(2), i, line.strip())
            continue
        m = ports_re.match(line)
        if m:
            pairs = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\d+)", m.group(2))
            found[m.group(1)] = ({k: int(v) for k, v in pairs}, i, line.strip())
    return found


def _py_consts(path: Path) -> dict:
    """从 Python 常量模块抓契约常量(执行模块 = 真值,行号另用正则定位)。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_aimail_contract_probe", str(path))
    if spec is None or spec.loader is None:
        raise CannotJudge(f"无法装载 Python 常量模块: {path}")
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
    out("[contract] (a) 常量 == 清单")
    for label, rel, loader in (("python", PY_CONTRACT, _py_consts),
                               ("ts", TS_CONTRACT, _ts_consts)):
        path = REPO / rel
        if not path.is_file():
            viol("a", f"{rel}: 常量模块缺失(契约要求它存在;删掉=违约,不是判不了)")
            out(f"  ✗ {rel}: 常量模块缺失")
            continue
        try:
            consts = loader(path)
        except CannotJudge:
            raise
        except Exception as e:  # noqa: BLE001 — 常量模块自身坏了 = 判不了
            raise CannotJudge(f"{rel}: 常量模块无法解析/执行 ({e.__class__.__name__}: {e})") from e
        bad = 0
        for key, (ts_name, py_name) in CONST_MAP.items():
            name = ts_name if label == "ts" else py_name
            want = man[key]
            if name not in consts:
                viol("a", f"{rel}: 找不到契约常量 {name}(清单键 {key})")
                out(f"  ✗ {rel}: 无常量 {name} ← 清单键 {key}")
                bad += 1
                continue
            got, line_no, src = consts[name]
            if got != want:
                viol("a", f"{rel}:{line_no} {name} = {got!r} != 清单 {want!r}")
                out(f"  ✗ {rel}:{line_no} {name} = {got!r} != 清单 {want!r}(原文: {src})")
                bad += 1
        if not bad:
            out(f"  ✓ {rel} == 清单 ({len(CONST_MAP)} 项)")
    # Rust 侧本批不做 ⇒ 登记 GAP(不许静默)
    gap("a", "rust: gateway/bridge 两仓的 Rust 常量本批未覆盖(清单已备 "
             "bridge_default_path 字段, 下批接 ⇒ 见备案 aimail-contract-hardening-pending.md)")
    out("  · GAP rust: 未覆盖(gateway/bridge 留下批)")


# ── 规则 (b): 字面量白名单棘轮 ────────────────────────────────────────────
def literal_specs(man: dict) -> dict:
    """清单键 → 字面量值(同值去重;同值合并为同一逻辑字面量)。

    结构型契约键(``inbound_ports`` 是对象)不参与字面量扫描 —— 只扫字符串面。
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
        raise CannotJudge(f"git 不可用,无法取跟踪文件清单({e.__class__.__name__}: {e})") from e
    if r.returncode != 0:
        raise CannotJudge(f"git ls-files 失败(rc={r.returncode}): "
                          f"{r.stderr.decode('utf-8', 'replace').strip()}")
    return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]


def scan_literals(man: dict) -> tuple[dict, dict]:
    """返回 (counts[path][literal] = n, hits[path] = [(lineno, literal, text)])。"""
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
            continue  # 二进制/不可读 = 不参与(不是判不了)
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
        raise CannotJudge(f"基线缺失: {BASELINE_REL}(用 --update-baseline 生成并提交)")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise CannotJudge(f"基线不是合法 JSON: {BASELINE_REL} ({e})") from e
    files = data.get("files")
    if not isinstance(files, dict):
        raise CannotJudge(f"基线缺 files 映射: {BASELINE_REL}")
    return data


def check_literals(man: dict, update: bool, show_diff: bool) -> None:
    out("[contract] (b) 字面量白名单棘轮")
    counts, hits = scan_literals(man)
    if update:
        payload = {
            "_comment": ("字面量棘轮基线(按 文件×清单键 计数)。规则 = 不得新增:"
                         "计数只许减不许增;新增位置必须改用常量模块, 或在该行加 "
                         "`contract-allowed: <理由>` 注释(测试专用逃生门)。"
                         "重生成: python3 tests/contract/check-contract-single-source.py "
                         "--update-baseline"),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tracked_files": len(_tracked_files()),
            "hits": sum(sum(v.values()) for v in counts.values()),
            "files": {k: dict(sorted(v.items())) for k, v in sorted(counts.items())},
        }
        (REPO / BASELINE_REL).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        out(f"  · 基线已重写: {BASELINE_REL} "
            f"(文件 {len(counts)}, 命中 {payload['hits']}, 跟踪文件 {payload['tracked_files']})")
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
                viol("b", f"{rel} {key}={man[key]!r} 计数 {n} > 基线 {b} "
                          f"(新增位置: {where or '(未定位)'})")
                out(f"  ✗ {rel} {key}={man[key]!r}: 当前 {n} > 基线 {b} (+{n - b})")
                for ln, k, text in hits.get(rel, []):
                    if k == key:
                        out(f"      {rel}:{ln}: {text}")
    for rel, was in sorted(base_files.items()):
        for key, b in sorted(was.items()):
            n = int(counts.get(rel, {}).get(key, 0))
            if n < b:
                removed += 1
                if show_diff:
                    out(f"  · 减少 {rel} {key}={man[key]!r}: {b} → {n}")
    total_now = sum(sum(v.values()) for v in counts.values())
    total_base = sum(sum(v.values()) for v in base_files.values())
    if added == 0:
        out(f"  ✓ 无新增位置(文件 {len(counts)} 命中 {total_now};基线 {len(base_files)} 文件 "
            f"/ {total_base} 命中;减少 {removed} 处/字面量 属允许方向)")
    else:
        out(f"  ✗ 新增 {added} 处(应改用常量模块,或加 `{ALLOW_MARKER} <理由>` 注释)")


# ── 规则 (c): SKILL.md frontmatter 契约名 ─────────────────────────────────
def _frontmatter_fields(path: Path, rel: str) -> tuple:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise CannotJudge(f"{rel}: 首行不是 frontmatter 分隔符 `---`")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise CannotJudge(f"{rel}: frontmatter 未闭合")
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
    out("[contract] (c) SKILL.md frontmatter 契约名(1 真源 + 4 物化产物)")
    want_name = man["agent_skill_name"]
    want_tool = man["agent_toolset_name"]
    ok = 0
    src = REPO / SKILL_SOURCE
    if not src.is_file():
        viol("c", f"{SKILL_SOURCE}: 单一真源 SKILL.md 缺失")
        out(f"  ✗ {SKILL_SOURCE}: 缺失")
    else:
        targets = [(SKILL_SOURCE, False)] + [(p, True) for p in SKILL_MATERIALIZED]
        for rel, materialized in targets:
            path = REPO / rel
            if not path.is_file():
                if materialized:
                    # 物化产物缺失 = 生成物尚未生成(跑 scripts/materialize-resources.sh)
                    raise CannotJudge(
                        f"{rel}: 物化产物不存在, frontmatter 无法判定(先跑 "
                        f"scripts/materialize-resources.sh;门禁已在物化后调用本检查)")
                raise CannotJudge(f"{rel}: 不存在")
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
        out(f"  ✓ {ok}/{ok} 份 frontmatter == 清单(宿主按 `/{want_name}` 查表 ⇒ 这是断链防线)")


# ── main ───────────────────────────────────────────────────────────────────
def main(argv: list) -> int:
    global REPO
    ap = argparse.ArgumentParser(description="AIMail agent 侧契约单一真源门禁")
    ap.add_argument("--repo", default="", help="仓根(默认由本文件位置推导)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="重写字面量棘轮基线(只在有意变更后跑)")
    ap.add_argument("--show-baseline-diff", action="store_true",
                    help="打印计数减少明细")
    args = ap.parse_args(argv)

    REPO = Path(args.repo).expanduser().resolve() if args.repo \
        else Path(__file__).resolve().parents[2]

    try:
        man, man_path = load_manifest()
    except CannotJudge as e:
        out(f"[contract] CANNOT JUDGE (rc=2): {e}")
        return 2
    out(f"[contract] manifest: {man_path}")
    out(f"[contract] 契约键 {len(REQUIRED_KEYS)}: " +
        ", ".join(f"{k}={man[k]!r}" for k in REQUIRED_KEYS))

    # 三条规则都跑完(一条判不了不影响其余规则的判决输出)
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
        out("[contract] GAP(未覆盖,已登记,非通过项):")
        for rule, msg in _GAPS:
            out(f"  · ({rule}) {msg}")
    if _VIOLATIONS:
        out(f"[contract] FAIL — {len(_VIOLATIONS)} 项违约"
            + (f";另有 {len(cannot)} 项判不了" if cannot else "") + ":")
        for rule, msg in _VIOLATIONS:
            out(f"  ✗ ({rule}) {msg}")
        for c in cannot:
            out(f"  ? 判不了: {c}")
        return 1
    if cannot:
        out(f"[contract] CANNOT JUDGE (rc=2) — {len(cannot)} 项无法判定:")
        for c in cannot:
            out(f"  ? {c}")
        return 2
    if args.update_baseline:
        out("[contract] PASS(基线已重写)")
        return 0
    out("[contract] PASS — 契约单一真源一致, 无新增字面量位置, frontmatter 契约名正确")
    return 0


REPO = Path(__file__).resolve().parents[2]

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except CannotJudge as _e:  # 兜底:任何"判不了"都不许伪装成功
        print(f"[contract] CANNOT JUDGE (rc=2): {_e}")
        sys.exit(2)
    except Exception as _e:  # noqa: BLE001 — 未预期异常同样是"判不了",绝不返回 0
        import traceback
        traceback.print_exc()
        print(f"[contract] CANNOT JUDGE (rc=2): 未预期异常 {_e.__class__.__name__}: {_e}")
        sys.exit(2)

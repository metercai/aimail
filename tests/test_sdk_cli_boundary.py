"""SDK ↔ CLI 边界契约 + hermes 补丁对称性闸门（P3-2 收口, 2026-09-20）。

把计划文档里的"边界验收"落成可执行断言，防止镜像副本悄悄回流：

1. 平台补丁内容单真源在 pysdk 侧 —— cli/ 不得再出现这些块的第二份副本
   （历史：cli/hermes/*.sh 在 9596207 退役，CLI 改为委派 SDK 安装器）。
2. pysdk/ 的**可执行路径**不得调用/引用 CLI 程序（cli/runtime_bundle.py 等）；
   注释与文档不受限（人读的说明允许提及）。
3. 反向：cli/ 不得 import SDK 的平台模块；唯一允许的可选加速是 aimail_base
   （存在则用、不存在则按同一公式回退，见 cli/_common.py 模块 docstring）。
4. patch/unpatch 必须共用同一批模块级常量：unpatch 引用的补丁内容常量
   必须是 patch 引用的超集（内联复制剥离文本会让本断言失败）。
5. toolsets / profiles 的 patch→unpatch 字节级往返（此前零覆盖，只有 webhook 有）。
"""
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "cli"
PYSDK = REPO / "pysdk"

# pysdk/hermes 的平台补丁内容常量（单真源）——cli/ 下不得有同名定义
PATCH_CONTENT_CONSTANTS = {
    "PROFILES_HOOK", "PROFILES_HOOK_DEL",
    "WEBHOOK_REGISTRY_BLOCK", "WEBHOOK_CALL_BLOCK", "WEBHOOK_A2A_BLOCK",
    "WEBHOOK_ADAPTER_BLOCK", "WEBHOOK_BLOCK1", "WEBHOOK_BLOCK2",
    "WEBHOOK_BLOCK3", "WEBHOOK_BLOCK4", "WEBHOOK_BLOCK5",
    "TOOLSET_AIMAIL", "CORE_TOOL_NAMES",
}

# CLI 程序名（pysdk 的可执行路径不得引用）
CLI_PROGRAMS = (
    "runtime_bundle", "runtime_core", "check_status", "repair", "deploy_bridge",
    "setup_system", "send_welcome", "ping_test", "request_persona",
    "register_agent", "reconcile", "deregister_agent",
)

CLI_REF_RE = re.compile(r"(?<![\w/])cli/(?:%s)\b" % "|".join(CLI_PROGRAMS))


def _sources(root: Path, suffixes=(".py", ".sh")):
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix not in suffixes:
            continue
        if "__pycache__" in p.parts:
            continue
        yield p


def _blank_spans(text: str, spans) -> str:
    """把给定 (lineno, col, end_lineno, end_col) 区间替换为空格（保留行号）。"""
    lines = text.splitlines(keepends=True)
    for l0, c0, l1, c1 in spans:
        for ln in range(l0, l1 + 1):
            line = lines[ln - 1]
            start = c0 if ln == l0 else 0
            end = c1 if ln == l1 else len(line)
            lines[ln - 1] = line[:start] + " " * max(0, end - start) + line[end:]
    return "".join(lines)


def _py_code_only(path: Path) -> str:
    """Python 源码：注释与 docstring 置空，其余（含普通字符串字面量）保留。

    保留普通字符串是有意的 —— 可执行引用通常就是字符串里的路径
    （subprocess([sys.executable, "cli/runtime_bundle.py"])），不能一并抹掉。
    """
    text = path.read_text(encoding="utf-8")
    spans = []
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            d = first.value
            spans.append((d.lineno, d.col_offset, d.end_lineno, d.end_col_offset))
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            spans.append((tok.start[0], tok.start[1], tok.end[0], tok.end[1]))
    return _blank_spans(text, spans)


def _sh_code_only(path: Path) -> str:
    """Shell：整行注释与非执行的输出语句（echo/printf 面向人的文案）置空。"""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("#") or re.match(r"^(echo|printf)\b", stripped):
            out.append("\n" if line.endswith("\n") else "")
        else:
            out.append(line)
    return "".join(out)


# ── 1/2. cli/ 不得再持有平台补丁内容的副本 ────────────────────────────

def test_cli_has_no_platform_patch_content_copy():
    offenders = []
    for path in _sources(CLI):
        text = path.read_text(encoding="utf-8")
        for const in PATCH_CONTENT_CONSTANTS:
            if re.search(r"(?m)^\s*%s\s*[:=]" % re.escape(const), text):
                offenders.append(f"{path.relative_to(REPO)}: defines {const}")
    assert not offenders, (
        "CLI 侧出现平台补丁内容副本（单真源应只在 pysdk/hermes/）:\n"
        + "\n".join(offenders)
    )


def test_no_mirrored_verbatim_claim():
    """淘汰"逐字镜像自 cli/"这类会在源退役后变成谎话的注释。"""
    offenders = []
    for root in (CLI, PYSDK):
        for path in _sources(root, suffixes=(".py", ".sh", ".md")):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "mirrored verbatim" in line.lower():
                    offenders.append(f"{path.relative_to(REPO)}:{i}")
    assert not offenders, "残留 mirrored-verbatim 表述: " + ", ".join(offenders)


# ── 3. pysdk 可执行路径不得引用 CLI 程序 ──────────────────────────────

def test_pysdk_executable_paths_do_not_reference_cli_programs():
    offenders = []
    for path in _sources(PYSDK):
        code = (_py_code_only(path) if path.suffix == ".py"
                else _sh_code_only(path))
        for i, line in enumerate(code.splitlines(), 1):
            if CLI_REF_RE.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{i}: {line.strip()}")
    assert not offenders, (
        "pysdk 可执行路径引用了 CLI 程序（应改为 aimail payload … / SDK 自带实现）:\n"
        + "\n".join(offenders)
    )


# ── 4. 反向：cli/ 的 import 不得越界 ─────────────────────────────────

# CLI 允许 import 的 SDK 侧模块（实测契约，非猜测；边界稿 §7 原口径
# "仅 aimail_base 可选" 过宽，已列为待 owner 裁决的订正项）：
#   aimail_base   —— 纯函数可选加速：存在则用，不存在则按同一公式回退
#                    （cli/_common.py 模块 docstring 明写）
#   gateway_api   —— 共享读库：pysdk/gateway_api.py 的 activate_system
#                    注明 "CLI L1-ONLY：(cl)i/setup_system.py 经此共享客户端
#                    调用；随 wheel 分发仅为 CLI 共用同一读库"(AUDIT-1 P2-2)
#   aimail_tools  —— 复用 _GatewayClient 完整方法集（cli/repair.py，不复制实现）
#   install       —— SDK 自足安装/卸载入口，CLI 由注册表 fn 表驱动委派
#                    （cli/aimail _sdk_install/_sdk_uninstall，9596207 设计）
# 禁止的是 pysdk 的**平台补丁模块**（hermes/*、deer-flow/*）与第三方未声明依赖。
SHARED_SDK_IMPORTS = {"aimail_base", "gateway_api", "aimail_tools", "install"}
THIRD_PARTY_IMPORTS = {"yaml"}


def test_cli_imports_stay_within_boundary():
    local = {p.stem for p in CLI.glob("*.py")} | {"aimail"}
    allowed = (local | set(sys.stdlib_module_names)
               | SHARED_SDK_IMPORTS | THIRD_PARTY_IMPORTS)
    offenders = []
    for path in list(CLI.glob("*.py")) + [CLI / "aimail"]:
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # 相对导入 = CLI 内部
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for n in names:
                if n and n not in allowed:
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}: import {n}")
    assert not offenders, (
        "cli/ 出现越界 import（白名单见 SHARED_SDK_IMPORTS，改动需先改契约）:\n"
        + "\n".join(offenders)
    )


# ── 5. patch/unpatch 单真源（内联复制会被这条抓住）────────────────────

@pytest.mark.parametrize("modname", ["patch_webhook", "patch_profiles", "toolsets"])
def test_unpatch_shares_patch_content_constants(modname):
    path = PYSDK / "hermes" / f"{modname}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_consts = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in PATCH_CONTENT_CONSTANTS:
                    module_consts.add(t.id)
    assert module_consts, f"{modname}: 未找到补丁内容常量"

    used = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith(("patch", "unpatch")):
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            used[node.name] = names
    patchers = [n for n in used if n.startswith("patch")]
    unpatchers = [n for n in used if n.startswith("unpatch")]
    assert patchers and unpatchers, f"{modname}: 缺少 patch/unpatch 函数"

    patched_consts = set().union(*(used[n] for n in patchers)) & module_consts
    stripped_consts = set().union(*(used[n] for n in unpatchers)) & module_consts
    missing = patched_consts - stripped_consts
    assert not missing, (
        f"{modname}: unpatch 未引用这些单真源常量（疑内联复制）: {sorted(missing)}"
    )


# ── 6. toolsets / profiles 往返（此前零覆盖）─────────────────────────

FAKE_TOOLSETS = '''_HERMES_CORE_TOOLS = [
    "read_file",
    "write_file",
]


def get_toolsets():
    return _HERMES_CORE_TOOLS
'''

FAKE_PROFILES = '''import shutil


def _maybe_register_gateway_service(canon):
    pass


def create_profile(name):
    profile_dir = "/tmp/" + name
    canon = name
    _maybe_register_gateway_service(canon)
    return profile_dir


def delete_profile(name):
    path = "/tmp/" + name
    shutil.rmtree(path, onerror=lambda *a: None)
    return True
'''


def test_toolsets_patch_unpatch_roundtrip(tmp_path):
    """patch 登记 7 个工具名 → unpatch 必须逐字节还原（含新增行数）。"""
    from toolsets import CORE_TOOL_NAMES, patch_toolsets, unpatch_toolsets

    (tmp_path / "toolsets.py").write_text(FAKE_TOOLSETS)
    orig = (tmp_path / "toolsets.py").read_text()

    assert patch_toolsets(str(tmp_path)) is True
    patched = (tmp_path / "toolsets.py").read_text()
    for name in CORE_TOOL_NAMES:
        assert patched.count(f'    "{name}",') == 1, f"{name} 未登记或被重复插入"
    assert "read_file" in patched  # 宿主原有内容未被破坏

    assert unpatch_toolsets(tmp_path / "toolsets.py") > 0
    assert (tmp_path / "toolsets.py").read_text() == orig, "unpatch 必须逐字节还原"

    # 二次往返仍稳定
    assert patch_toolsets(str(tmp_path)) is True
    assert unpatch_toolsets(tmp_path / "toolsets.py") > 0
    assert (tmp_path / "toolsets.py").read_text() == orig


def test_profiles_patch_unpatch_roundtrip(tmp_path):
    """profile_created/profile_deleted 钩子插入 → unpatch 必须逐字节还原。"""
    from patch_profiles import patch_profiles, unpatch_profiles

    target = tmp_path / "profiles.py"
    target.write_text(FAKE_PROFILES)
    orig = target.read_text()

    assert patch_profiles(str(target)) is True
    patched = target.read_text()
    assert 'trigger_profile_hooks("profile_created"' in patched
    assert 'trigger_profile_hooks("profile_deleted"' in patched

    assert unpatch_profiles(target) >= 1
    assert target.read_text() == orig, "unpatch 必须逐字节还原"

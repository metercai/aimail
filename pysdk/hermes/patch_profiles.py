"""Apply amail profile hooks patch to Hermes hermes_cli/profiles.py.

Adds trigger_profile_hooks() calls for profile_created and profile_deleted
events, enabling automatic amail address registration and API key cleanup.

Auto-detects Hermes commit version and adjusts insertion points accordingly.
See HERMES_PATCH_MAP.md for details.

Library form of cli/hermes/apply_profiles_patch.py: logic lives in
patch_profiles(target_path) -> bool (True when the file was modified); stderr
diagnostics unchanged. Runnable compat: python3 patch_profiles.py <path/to/profiles.py>

Library now also ships the unpatch side: unpatch_profiles(fp) -> int removes the
profile hooks by exact-text block stripping (PROFILES_HOOK / PROFILES_HOOK_DEL
mirrored verbatim from cli/aimail).
"""

import os
import re
import subprocess
import sys
from pathlib import Path

# ── 双形态自举(repo pysdk/ ↔ pip site-packages/aimail/)──
# repo 形态: dirname(dirname(__file__)) = pysdk/(含 aimail_base.py 等 flat core,
# 裸 import 可用)→ 加入 sys.path。pip 形态: 该目录 = site-packages/(无 flat core),
# 需先 import aimail 触发 aimail/__init__.py glue(把 aimail/ 目录插 sys.path,
# flat core 裸 import 才可用)。
_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isfile(os.path.join(_CORE_DIR, "aimail_base.py")):
    if _CORE_DIR not in sys.path:
        sys.path.insert(0, _CORE_DIR)
else:
    try:
        import aimail  # noqa: F401  (pip 形态 glue)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# Commit → anchor position mapping
#
# Each entry: (since_commit, {"register": N, "return": N, "delete": N})
# "since_commit" means this entry applies when HEAD is an ancestor
# of or equal to since_commit. Ordered oldest → newest.
# ═══════════════════════════════════════════════════════════════

PROFILES_ANCHOR_MAP = [
    # (anchor commit, {_maybe_register_line, first_return_after_register, deleted_print_line})
    ("4d22b8293374", {"register": 880, "return": 882, "delete": 1074}),
    ("88dbf9510", {"register": 899, "return": 901, "delete": 1093}),
    ("7a318aae2", {"register": 930, "return": 932, "delete": 1124}),
    ("9b5f7b63c", {"register": 930, "return": 932, "delete": 1176}),
    ("723c2331b", {"register": 932, "return": 934, "delete": 1178}),
    ("40d7c264f", {"register": 971, "return": 973, "delete": 1217}),
    ("d82f9fa7f", {"register": 1012, "return": 1014, "delete": 1258}),
]

# ── Helpers ───────────────────────────────────────────────────

def _resolve_git_root(target_path: str) -> str:
    """Return the git root directory for the target file."""
    try:
        d = os.path.dirname(os.path.abspath(target_path))
        r = subprocess.run(
            ["git", "-C", d, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(target_path))


def _get_hermes_commit(git_root: str) -> str:
    """Return the short (12-char) HEAD commit hash."""
    try:
        r = subprocess.run(
            ["git", "-C", git_root, "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _is_ancestor(git_root: str, ancestor: str, commit: str) -> bool:
    """Return True if `ancestor` is an ancestor of `commit` (or equal)."""
    try:
        r = subprocess.run(
            ["git", "-C", git_root, "merge-base", "--is-ancestor", ancestor, commit],
            capture_output=True, text=True, timeout=5
        )
        return r.returncode == 0
    except Exception:
        return False


def _resolve_anchors(git_root: str, commit: str) -> dict:
    """Find the best anchor positions for a given Hermes commit."""
    anchors = {"register": 1012, "return": 1014, "delete": 1258}  # default (newest)
    if commit == "unknown":
        return anchors
    for since_commit, mapping in reversed(PROFILES_ANCHOR_MAP):
        if _is_ancestor(git_root, since_commit, commit):
            return mapping
    return anchors


def _auto_detect_anchors(lines: list) -> dict:
    """Auto-scan file for anchor positions by known string markers."""
    anchors = {}
    for i, line in enumerate(lines, 1):
        if "_maybe_register_gateway_service(canon)" in line and "register" not in anchors:
            anchors["register"] = i
        if "return profile_dir" in line.strip() and "return" not in anchors:
            # Only pick the first occurrence AFTER the register point
            if "register" in anchors and i > anchors["register"]:
                anchors["return"] = i
        if "Profile '" in line and "deleted" in line and "delete" not in anchors:
            anchors["delete"] = i
    return anchors


def _find_return_after_register(content: str, lines: list, register_line: int) -> int:
    """Find the first `return profile_dir` after the _maybe_register line."""
    search_start = register_line - 1  # 0-indexed
    for i in range(search_start, len(lines)):
        if lines[i].strip() == "return profile_dir":
            return i + 1  # 1-indexed
    return register_line + 2  # fallback


def _find_delete_print_line(content: str) -> int:
    """Find the print(f\"...Profile '...' deleted.\") line."""
    for m in re.finditer(r"Profile '.*?' deleted\.", content):
        line_end = content.find('\n', m.end())
        if line_end == -1:
            line_end = len(content)
        line_start = content.rfind('\n', 0, m.start()) + 1
        line = content[line_start:line_end].strip()
        if line.startswith("print("):
            return content[:line_end+1].count('\n') + 1
    return 0



def patch_profiles(target_path: str) -> bool:
    """Apply both amail hook sub-patches to a Hermes hermes_cli/profiles.py file.

    Returns True if the file was modified. Idempotent: already-patched files are
    detected and reported as ALREADY PATCHED.
    """
    if not os.path.isfile(target_path):
        print(f"patch_profiles: target file not found: {target_path}", file=sys.stderr)
        return False

    git_root = _resolve_git_root(target_path)
    hermes_commit = _get_hermes_commit(git_root)
    anchors = _resolve_anchors(git_root, hermes_commit)

    print(f"Hermes commit: {hermes_commit}", file=sys.stderr)
    print(f"Anchors: register={anchors['register']} return={anchors['return']} delete={anchors['delete']}", file=sys.stderr)

    with open(target_path) as f:
        content = f.read()

    lines = content.split('\n')
    _original_lines = list(lines)
    patched = False

    # 插入文本统一引用模块级单真源常量(PROFILES_HOOK / PROFILES_HOOK_DEL,
    # 见文件尾部定义)——unpatch_profiles() 剥离的字节与这里插入的字节
    # 逐字节同源,杜绝 patch↔unpatch 漂移。
    hook_created = PROFILES_HOOK        # profile_created 完整插入字节
    hook_deleted = PROFILES_HOOK_DEL    # profile_deleted 完整插入字节

    # ── Patch 1: profile creation hook (always replace) ───────────
    # Remove old instance if present(兼容旧形:aimail_base 直 import + 裸调用;
    # 新形:aimail.hermes 限定调用。created/deleted 两实例基础缩进不同
    # (4/12 空格)→ 各行缩进 [ \t]* 不校验;import 行 [^\n]* 通配两种形态,
    # 调用行 [A-Za-z_][\w.]*\.? 前缀通配裸函数与 aimail_hermes. 限定)
    content = re.sub(
        r'[ \t]*# ── Fire integration hooks \(AmailGateway\) ──\n'
        r'[ \t]*try:\n'
        r'[ \t]*from [^\n]*\n'
        r'[ \t]*(?:[A-Za-z_][\w.]*\.)?trigger_profile_hooks\("profile_created".*?'
        r'[ \t]*except (?:ImportError|Exception):\n'
        r'[ \t]*pass[^\n]*\n',
        '',
        content, count=1, flags=re.DOTALL
    )
    # Insert before "return profile_dir"
    if "return profile_dir" in content and "_maybe_register_gateway_service" in content:
        marker = "_maybe_register_gateway_service(canon)"
        rest = content[content.index(marker):]
        m = re.search(r'\n(    return profile_dir)\n', rest)
        if m:
            insertion = rest[:m.start(1)] + hook_created + rest[m.start(1):]
            content = content[:content.index(marker)] + insertion
            patched = True
            print("Patch 1: profile_created hook added/updated", file=sys.stderr)
        else:
            print("WARNING: could not find 'return profile_dir' after marker — patch 1 skipped", file=sys.stderr)
    else:
        print("WARNING: could not find insertion point — patch 1 skipped", file=sys.stderr)

    # ── Patch 2: profile deletion hook (always replace) ───────────
    # Remove old instance if present(同 Patch 1:兼容新旧两种 import/调用形态)
    content = re.sub(
        r'[ \t]*# ── Fire integration hooks \(AmailGateway\) ──\n'
        r'[ \t]*try:\n'
        r'[ \t]*from [^\n]*\n'
        r'[ \t]*(?:[A-Za-z_][\w.]*\.)?trigger_profile_hooks\("profile_deleted".*?'
        r'[ \t]*except (?:ImportError|Exception):\n'
        r'[ \t]*pass[^\n]*\n',
        '',
        content, count=1, flags=re.DOTALL
    )
    # Insert after the rmtree fallback line. 注意:rmtree 可能在 try/except
    # 内(如 _rmtree_with_retry 的 onexc/onerror fallback 行)——在 try 块内
    # 插钩子会打断宿主 except 链(语法错误)。只匹配 onerror 回退行(重试
    # 成功删除后的安全点),不匹配 try 内首行调用。
    _rmtree_re = r'^.*shutil\.rmtree\([^\n]*onerror[^\n]*\).*?$'
    for m in re.finditer(_rmtree_re, content, re.MULTILINE):
        line_end = content.find('\n', m.end())
        if line_end == -1:
            line_end = len(content)
        content = content[:line_end+1] + hook_deleted + content[line_end+1:]
        patched = True
        print("Patch 2: profile_deleted hook added/updated", file=sys.stderr)
        break
    else:
        # 无 onerror 行 → 回退:最后一个 rmtree(profile_dir) 行(旧版布局)
        _m_last = None
        for _m in re.finditer(r'^.*shutil\.rmtree\([^\n]*profile_dir[^\n]*\).*$', content, re.MULTILINE):
            _m_last = _m
        if _m_last is not None:
            line_end = content.find('\n', _m_last.end())
            if line_end == -1:
                line_end = len(content)
            content = content[:line_end+1] + hook_deleted + content[line_end+1:]
            patched = True
            print("Patch 2: profile_deleted hook added/updated (last-rmtree fallback)", file=sys.stderr)
        else:
            print("WARNING: could not find shutil.rmtree with profile_dir — patch 2 skipped", file=sys.stderr)

    if patched:
        with open(target_path, "w") as f:
            f.write(content)
        print("OK")
    else:
        print("ALREADY PATCHED")

    # ── Version diagnosis ─────────────────────────────────────────
    if hermes_commit != "unknown":
        _in_map = any(
            _is_ancestor(git_root, sc, hermes_commit)
            for sc, _ in PROFILES_ANCHOR_MAP
        )
        if not _in_map:
            detected = _auto_detect_anchors(_original_lines)
            print(file=sys.stderr)
            print(f"  ╔═══ NEW HERMES COMMIT: {hermes_commit}", file=sys.stderr)
            print("  ║ Not in PROFILES_ANCHOR_MAP — auto-detected:", file=sys.stderr)
            for k in ["register", "return", "delete"]:
                v = detected.get(k, "?")
                print(f"  ║   {k}: {v}", file=sys.stderr)
            print("  ║", file=sys.stderr)
            print("  ║ Suggested anchor entry:", file=sys.stderr)
            ret = detected.get("return", "?")
            reg = detected.get("register", "?")
            dlt = detected.get("delete", "?")
            print(f'  ║   ("{hermes_commit}", {{"register": {reg},'
                  f' "return": {ret}, "delete": {dlt}}}),', file=sys.stderr)
            print("  ║ Add to PROFILES_ANCHOR_MAP and commit.", file=sys.stderr)
            print("  ╚═══", file=sys.stderr)

    return patched


# ═══════════════════════════════════════════════════════════════
#  exact-text patch removal for hermes_cli/profiles.py — blocks must
#  match what patch_profiles() inserts. Migrated from cli/aimail
#  (unpatch section, lines 1307-1545).
# ═══════════════════════════════════════════════════════════════

def strip_block(text: str, block: str) -> tuple:
    """Remove a known exact string block from text.
    Returns (new_text, True if removed).
    """
    if block not in text:
        return text, False
    return text.replace(block, '', 1), True


def strip_trailing_blanks(text: str) -> str:
    """Remove excess blank lines left after block removal."""
    return re.sub(r'\n{4,}', '\n\n\n', text)


PROFILES_HOOK = """    # ── Fire integration hooks (AmailGateway) ──
    try:
        from aimail.aimail_base import trigger_profile_hooks
        trigger_profile_hooks(\"profile_created\", canon, str(profile_dir))
    except ImportError:
        pass  # aimail (pip) not installed

"""

PROFILES_HOOK_DEL = """            # ── Fire integration hooks (AmailGateway) ──
            try:
                from aimail.aimail_base import trigger_profile_hooks
                trigger_profile_hooks(\"profile_deleted\", canon, str(profile_dir))
            except ImportError:
                pass  # aimail (pip) not installed

"""


def unpatch_profiles(fp: Path) -> int:
    if not fp.exists():
        return 0
    text = fp.read_text()
    original = text
    changes = 0

    for block in [PROFILES_HOOK, PROFILES_HOOK_DEL]:
        text, ok = strip_block(text, block)
        if ok:
            changes += 1

    text = strip_trailing_blanks(text)

    if text != original:
        fp.write_text(text)
        print(f"  ✓ profiles.py: {changes} hook(s) removed")
    else:
        print("  - profiles.py: no changes")
    return changes


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 patch_profiles.py <path/to/profiles.py>", file=sys.stderr)
        sys.exit(1)
    patch_profiles(sys.argv[1])

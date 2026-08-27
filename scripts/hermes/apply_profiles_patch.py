#!/usr/bin/env python3
"""Apply amail profile hooks patch to Hermes hermes_cli/profiles.py.

Adds trigger_profile_hooks() calls for profile_created and profile_deleted
events, enabling automatic amail address registration and API key cleanup.

Auto-detects Hermes commit version and adjusts insertion points accordingly.
See HERMES_PATCH_MAP.md for details.

Usage: python3 apply_profiles_patch.py <path/to/profiles.py>
"""
import sys
import re
import os
import subprocess

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


# ── Main ──────────────────────────────────────────────────────

if len(sys.argv) < 2:
    print("Usage: python3 apply_profiles_patch.py <path/to/profiles.py>", file=sys.stderr)
    sys.exit(1)

target = sys.argv[1]
git_root = _resolve_git_root(target)
hermes_commit = _get_hermes_commit(git_root)
anchors = _resolve_anchors(git_root, hermes_commit)

print(f"Hermes commit: {hermes_commit}", file=sys.stderr)
print(f"Anchors: register={anchors['register']} return={anchors['return']} delete={anchors['delete']}", file=sys.stderr)

with open(target) as f:
    content = f.read()

lines = content.split('\n')
_original_lines = list(lines)
patched = False

hook_created = '''
    # ── Fire integration hooks (AmailGateway) ──
    try:
        from aimail.aimail_base import trigger_profile_hooks
        trigger_profile_hooks("profile_created", canon, str(profile_dir))
    except ImportError:
        pass  # aimail (pip) not installed
'''

hook_deleted = '''            # ── Fire integration hooks (AmailGateway) ──
            try:
                from aimail.aimail_base import trigger_profile_hooks
                trigger_profile_hooks("profile_deleted", canon, str(profile_dir))
            except ImportError:
                pass  # aimail (pip) not installed
'''

# ── Patch 1: profile creation hook (always replace) ───────────
# Remove old instance if present(兼容 tools.aimail_base 旧名与 pip 新名;
# created/deleted 两实例基础缩进不同(4/12 空格)→ 各行缩进 [ \t]* 不校验)
content = re.sub(
    r'[ \t]*# ── Fire integration hooks \(AmailGateway\) ──\n'
    r'[ \t]*try:\n'
    r'[ \t]*from [^\n]*import trigger_profile_hooks\n'
    r'[ \t]*trigger_profile_hooks\("profile_created".*?'
    r'[ \t]*except ImportError:\n'
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
        insertion = rest[:m.start(1)] + hook_created + '\n' + rest[m.start(1):]
        content = content[:content.index(marker)] + insertion
        patched = True
        print("Patch 1: profile_created hook added/updated", file=sys.stderr)
    else:
        print("WARNING: could not find 'return profile_dir' after marker — patch 1 skipped", file=sys.stderr)
else:
    print("WARNING: could not find insertion point — patch 1 skipped", file=sys.stderr)

# ── Patch 2: profile deletion hook (always replace) ───────────
# Remove old instance if present(兼容 tools.aimail_base 旧名与 pip 新名;
# deleted 实例基础缩进 12 空格 → 各行缩进 [ \t]* 不校验;按 profile_deleted 锚定)
content = re.sub(
    r'[ \t]*# ── Fire integration hooks \(AmailGateway\) ──\n'
    r'[ \t]*try:\n'
    r'[ \t]*from [^\n]*import trigger_profile_hooks\n'
    r'[ \t]*trigger_profile_hooks\("profile_deleted".*?'
    r'[ \t]*except ImportError:\n'
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
    with open(target, "w") as f:
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

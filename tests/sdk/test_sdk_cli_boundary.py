"""SDK ↔ CLI boundary contract + hermes patch symmetry gate (P3-2 closure, 2026-09-20).

Turns the "boundary acceptance" in the planning document into executable assertions, preventing mirror copies from quietly flowing back:

1. The single source of truth for platform patch content is on the pysdk side —— cli/ must not contain a second copy of these blocks
   (History: cli/hermes/*.sh retired in 9596207, CLI changed to delegate to SDK installer).
 2. the **executable paths** of pysdk/ must not call/reference the CLI program (cli/runtime_bundle.py etc.);
   comments and documentation are not restricted (human-readable explanations are allowed to mention them).
3. Reverse: cli/ must not import SDK platform modules; the only allowed optional acceleration is aimail_base
   (use if exists, otherwise fall back to the same formula, see cli/_common.py module docstring).
4. patch/unpatch must share the same batch of module-level constants: unpatch references the patch content constants
   Must be a superset of the patch references (inlining copies that strip text would cause this assertion to fail).
5. toolsets / profiles patch→unpatch byte-level round-trip (previously zero coverage, only webhook had it).
"""
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
CLI = REPO / "cli"
PYSDK = REPO / "pysdk"

# Platform patch content constants for pysdk/hermes (single source of truth) — no same-named definitions allowed under cli/
PATCH_CONTENT_CONSTANTS = {
    "PROFILES_HOOK", "PROFILES_HOOK_DEL",
    "WEBHOOK_REGISTRY_BLOCK", "WEBHOOK_CALL_BLOCK", "WEBHOOK_A2A_BLOCK",
    "WEBHOOK_ADAPTER_BLOCK", "WEBHOOK_BLOCK1", "WEBHOOK_BLOCK2",
    "WEBHOOK_BLOCK3", "WEBHOOK_BLOCK4", "WEBHOOK_BLOCK5",
    "TOOLSET_AIMAIL", "CORE_TOOL_NAMES",
}

# CLI program name (pysdk executable paths must not reference this)
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
    """Replace the given (lineno, col, end_lineno, end_col) range with spaces (preserving line numbers)."""
    lines = text.splitlines(keepends=True)
    for l0, c0, l1, c1 in spans:
        for ln in range(l0, l1 + 1):
            line = lines[ln - 1]
            start = c0 if ln == l0 else 0
            end = c1 if ln == l1 else len(line)
            lines[ln - 1] = line[:start] + " " * max(0, end - start) + line[end:]
    return "".join(lines)


def _py_code_only(path: Path) -> str:
    """Python source: blank out comments and docstrings, keep the rest (including regular string literals).

    Keeping plain strings is intentional —— executable references are usually just paths in strings
    (subprocess([sys.executable, "cli/runtime_bundle.py"])), which must not be wiped out together.
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
    """Shell: blank out whole-line comments and non-executable output statements (echo/printf human-facing text)."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("#") or re.match(r"^(echo|printf)\b", stripped):
            out.append("\n" if line.endswith("\n") else "")
        else:
            out.append(line)
    return "".join(out)


# ── 1/2. cli/ must no longer hold copies of platform patch content ────────────────────────────

def test_cli_has_no_platform_patch_content_copy():
    offenders = []
    for path in _sources(CLI):
        text = path.read_text(encoding="utf-8")
        for const in PATCH_CONTENT_CONSTANTS:
            if re.search(r"(?m)^\s*%s\s*[:=]" % re.escape(const), text):
                offenders.append(f"{path.relative_to(REPO)}: defines {const}")
    assert not offenders, (
        "Platform patch content copy found on CLI side (single source of truth should only be in pysdk/hermes/):\n"
        + "\n".join(offenders)
    )


def test_no_mirrored_verbatim_claim():
    """Phase out comments like "mirrored verbatim from cli/" that become lies after the source is retired."""
    offenders = []
    for root in (CLI, PYSDK):
        for path in _sources(root, suffixes=(".py", ".sh", ".md")):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "mirrored verbatim" in line.lower():
                    offenders.append(f"{path.relative_to(REPO)}:{i}")
    assert not offenders, "Residual mirrored-verbatim phrasing: " + ", ".join(offenders)


# ── 3. pysdk executable paths must not reference CLI programs ──────────────────────────────

def test_pysdk_executable_paths_do_not_reference_cli_programs():
    offenders = []
    for path in _sources(PYSDK):
        code = (_py_code_only(path) if path.suffix == ".py"
                else _sh_code_only(path))
        for i, line in enumerate(code.splitlines(), 1):
            if CLI_REF_RE.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{i}: {line.strip()}")
    assert not offenders, (
        "pysdk executable path references CLI program (should change to aimail install --payload … / SDK built-in implementation):\n"
        + "\n".join(offenders)
    )


# ── 4. Reverse: cli/ imports must not cross boundaries ─────────────────────────────────

# CLI allows SDK-side modules to be imported (empirically verified contract, not guesswork; boundary draft §7 original wording
# "only aimail_base optional" is too broad, listed as a correction item pending owner decision):
#   aimail_base   —— pure function optional acceleration: use if present, otherwise fall back to the same formula
#                    (explicitly stated in cli/_common.py module docstring)
#   gateway_api   —— shared read DB: activate_system in pysdk/gateway_api.py
#                    notes "CLI L1-ONLY: (cl)i/setup_system.py calls via this shared client
#                    distributed with wheel only for CLI to share the same read library" (AUDIT-1 P2-2)
#   aimail_tools  —— reuse the full method set of _GatewayClient (cli/repair.py, do not duplicate the implementation)
#   install       —— SDK self-contained install/uninstall entry, CLI is driven by registry fn table delegation
#                    (cli/aimail _sdk_install/_sdk_uninstall, 9596207 design)
# Forbidden are pysdk **platform patch modules** (hermes/*, deer-flow/*) and undeclared third-party dependencies.
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
                if node.level:  # relative import = CLI internal
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for n in names:
                if n and n not in allowed:
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}: import {n}")
    assert not offenders, (
        "cli/ out-of-bounds import detected (whitelist see SHARED_SDK_IMPORTS, changes require updating the contract first):\n"
        + "\n".join(offenders)
    )


# ── 5. patch/unpatch single source of truth (inline copies will be caught by this)────────────────────

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
    assert module_consts, f"{modname}: patch content constants not found"

    used = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith(("patch", "unpatch")):
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            used[node.name] = names
    patchers = [n for n in used if n.startswith("patch")]
    unpatchers = [n for n in used if n.startswith("unpatch")]
    assert patchers and unpatchers, f"{modname}: missing patch/unpatch functions"

    patched_consts = set().union(*(used[n] for n in patchers)) & module_consts
    stripped_consts = set().union(*(used[n] for n in unpatchers)) & module_consts
    missing = patched_consts - stripped_consts
    assert not missing, (
        f"{modname}: unpatch does not reference these single source of truth constants (suspected inline copy): {sorted(missing)}"
    )


# ── 6. toolsets / profiles round-trip (previously zero coverage)─────────────────────────

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
    """patch registers 7 tool names → unpatch must restore byte-for-byte (including added line count)."""
    from toolsets import CORE_TOOL_NAMES, patch_toolsets, unpatch_toolsets

    (tmp_path / "toolsets.py").write_text(FAKE_TOOLSETS)
    orig = (tmp_path / "toolsets.py").read_text()

    assert patch_toolsets(str(tmp_path)) is True
    patched = (tmp_path / "toolsets.py").read_text()
    for name in CORE_TOOL_NAMES:
        assert patched.count(f'    "{name}",') == 1, f"{name} not registered or inserted duplicately"
    assert "read_file" in patched  # host original content not corrupted

    assert unpatch_toolsets(tmp_path / "toolsets.py") > 0
    assert (tmp_path / "toolsets.py").read_text() == orig, "unpatch must restore byte-for-byte"

    # Second round-trip still stable
    assert patch_toolsets(str(tmp_path)) is True
    assert unpatch_toolsets(tmp_path / "toolsets.py") > 0
    assert (tmp_path / "toolsets.py").read_text() == orig


def test_profiles_patch_unpatch_roundtrip(tmp_path):
    """profile_created/profile_deleted hook insertion → unpatch must restore byte-for-byte."""
    from patch_profiles import patch_profiles, unpatch_profiles

    target = tmp_path / "profiles.py"
    target.write_text(FAKE_PROFILES)
    orig = target.read_text()

    assert patch_profiles(str(target)) is True
    patched = target.read_text()
    assert 'trigger_profile_hooks("profile_created"' in patched
    assert 'trigger_profile_hooks("profile_deleted"' in patched

    assert unpatch_profiles(target) >= 1
    assert target.read_text() == orig, "unpatch must restore byte-for-byte"


# ── 6. Gate op set ratchet (Contract v1.0 §4.1; Second Order: Boundary Solidification, independent of functional verification)──────
# `prompt-test` = (a) Adjudicated read-only op (Contract §4.1 three op additions, owner 2026-10-10):
# Name validation (`name_ok`) + L5 filter/match (`rules[].match`), both transport paths are isomorphic.
CONVERGED_OPS = {"assemble", "update", "teardown", "prompt-test"}
LEGACY_OPS_WHITELIST = {          # Whitelist period: legacy ops remain available, removal must go through major version
    "version", "iter_bindings", "ensure_webhook_secret",
    "resolve_register_webhook_url", "register_agent_email", "backfill_binding",
}


def test_door_op_surface_is_ratcheted():
    """Gate exposure surface must be exactly = converged set ∪ whitelist period legacy set (one extra is red = interface drift)."""
    import json as _json
    import os as _os
    import subprocess as _sp
    import sys as _sys

    sdk_ops = REPO / "pysdk" / "sdk_ops.py"
    r = _sp.run([_sys.executable, str(sdk_ops), "version"],
                capture_output=True, text=True, env=dict(_os.environ))
    assert r.returncode == 0, r.stderr
    ops = set(_json.loads(r.stdout)["result"]["ops"])
    assert CONVERGED_OPS <= ops, f"Missing required converged op: {sorted(CONVERGED_OPS - ops)}"
    extra = ops - CONVERGED_OPS - LEGACY_OPS_WHITELIST
    assert not extra, f"Unregistered gate op appeared (interface drift): {sorted(extra)}"


# ── Contract v1.0 §5 Second Order (Additional Layer, Independent Report, Prohibited from Replacing Functional Verification)────────────────────────
# 3) System-level configuration is **read-only**: no symbols for "writing system-level configuration" shall appear on the SDK side (CLI writes, SDK read-only).
def test_sdk_never_writes_system_level_gateway_config():
    repo = Path(__file__).resolve().parent.parent.parent
    offenders = []
    banned = ("save_gateway_config", "write_gateway_config", "set_gateway_config")
    for f in list((repo / "pysdk").rglob("*.py")):
        src = f.read_text(encoding="utf-8", errors="replace")
        for b in banned:
            if b in src:
                offenders.append(f"{f.relative_to(repo)}: {b}")
    assert not offenders, f"System-level configuration write symbols appeared on SDK side (out of bounds): {offenders}"


# 4) SDK→CLI reverse call **whitelist**: Locations in source code that actually "execute" aimail, their subcommands must be within the whitelist.
#    Only recognize command-line tokens (quoted ["aimail","<sub>"] or string commands containing run/spawn/exec),
#    Examples in prose/documentation strings are not counted.
def test_reverse_call_abi_is_whitelisted():
    repo = Path(__file__).resolve().parent.parent.parent
    allowed = {"install", "uninstall", "address", "version"}
    tok_list = re.compile(r"""["']aimail["']\s*,\s*["']([a-z][a-z-]*)["']""")
    tok_cmd = re.compile(r"""["']aimail\s+([a-z][a-z-]*)""")
    exec_hint = ("subprocess", "run(", "Popen", "execFile", "exec(", "spawn", "system(", "check_output")
    offenders = []
    roots = [repo / "pysdk", repo / "tssdk"]
    for root in roots:
        for f in list(root.rglob("*.py")) + list(root.rglob("*.ts")):
            if "node_modules" in str(f) or "/dist/" in str(f):
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                t = line.strip()
                if t.startswith(("//", "#", "*", "/*")):
                    continue
                subs = tok_list.findall(line)
                if any(h in line for h in exec_hint):
                    subs += tok_cmd.findall(line)
                for sub in subs:
                    if sub not in allowed:
                        offenders.append(f"{f.relative_to(repo)}:{i}: aimail {sub}")
    assert not offenders, f"SDK→CLI reverse call outside whitelist appeared: {offenders[:8]}"

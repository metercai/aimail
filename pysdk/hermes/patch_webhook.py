"""Apply AIMail preprocessor patch to Hermes gateway/platforms/webhook.py.

Adds:
  1. PREPROCESS_REGISTRY dict + register_preprocessor() function
  2. Preprocessor invocation in webhook handler (before prompt rendering)

Auto-detects Hermes commit version and adjusts insertion points accordingly.
See HERMES_PATCH_MAP.md for details.

Library form of the retired cli/hermes/apply_webhook_patch.py — the CLI no
longer installs platforms itself, it delegates to this SDK: the 7 sub-patches
live in patch_webhook(target_path) -> bool (True when the file was modified);
stderr diagnostics unchanged. Runnable compat:
python3 patch_webhook.py <path/to/webhook.py>

Library also ships the unpatch side: unpatch_webhook(fp) -> int removes the
patch by exact-text block stripping. Single source of truth = this module: the
insertion text lives in the shared module constants (WEBHOOK_ADAPTER_BLOCK /
WEBHOOK_REGISTRY_BLOCK / WEBHOOK_CALL_BLOCK / WEBHOOK_A2A_BLOCK), so the bytes
unpatch strips are by construction the bytes patch inserted. WEBHOOK_BLOCK1-5
are kept only as legacy-shape passes for hosts patched by older SDK versions.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

# ── Dual-form bootstrap (repo pysdk/ ↔ pip site-packages/aimail/) ──
# repo form: dirname(dirname(__file__)) = pysdk/ (contains aimail_base.py etc flat core,
# Bare import available) → add to sys.path. pip form: this dir = site-packages/ (no flat core),
# need to import aimail first to trigger aimail/__init__.py glue (insert aimail/ dir into sys.path,
# flat core bare import then available).
_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isfile(os.path.join(_CORE_DIR, "aimail_base.py")):
    if _CORE_DIR not in sys.path:
        sys.path.insert(0, _CORE_DIR)
else:
    try:
        import aimail  # noqa: F401  (pip form glue)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# Commit → anchor position mapping
#
# Each entry: (since_commit, {"typing": N, "logger": N, "prompt": N})
# "since_commit" means this entry applies when HEAD is an ancestor
# of or equal to since_commit. Ordered oldest → newest.
# ═══════════════════════════════════════════════════════════════

WEBHOOK_ANCHOR_MAP = [
    # (anchor commit, {typing_import_line, logger_line, prompt_anchor_line})
    ("898b6d7d5", {"typing": 37, "logger": 55, "prompt": 409}),
    ("60531889d", {"typing": 37, "logger": 55, "prompt": 410}),
    ("9c90b3a59", {"typing": 37, "logger": 55, "prompt": 425}),
    ("61ac11872", {"typing": 37, "logger": 55, "prompt": 436}),
    ("bbf02c322", {"typing": 39, "logger": 57, "prompt": 439}),
    ("15aa6884a", {"typing": 39, "logger": 57, "prompt": 451}),
    ("bd8e2ec1a", {"typing": 39, "logger": 57, "prompt": 460}),
    ("afc861550", {"typing": 40, "logger": 58, "prompt": 503}),
    ("f35abb122", {"typing": 40, "logger": 58, "prompt": 552}),
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
    anchors = {"typing": 40, "logger": 58, "prompt": 552}  # default (newest)
    if commit == "unknown":
        return anchors
    # Walk from newest to oldest, pick first match
    for since_commit, mapping in reversed(WEBHOOK_ANCHOR_MAP):
        if _is_ancestor(git_root, since_commit, commit):
            return mapping
    return anchors


def _auto_detect_anchors(lines: list) -> dict:
    """Auto-scan file for anchor positions by known string markers."""
    anchors = {}
    for i, line in enumerate(lines, 1):
        if "from typing import " in line and "typing" not in anchors:
            anchors["typing"] = i
        if "logger = logging.getLogger(__name__)" in line and "logger" not in anchors:
            anchors["logger"] = i
        if "# Format prompt from template" in line and "prompt" not in anchors:
            anchors["prompt"] = i
        if "# Non-blocking" in line and "non_blocking" not in anchors:
            anchors["non_blocking"] = i
    return anchors



# ── Single source of truth for insertion text (canonical insertion blocks) ──────────────────────
# patch_webhook() and unpatch_webhook() share the same set of constants → unpatch strips
# == bytes actually inserted by patch (byte-for-byte symmetric, eliminating drift). unpatch first strips
# Hermes adapter import (patch 7 inserted in middle of blank lines at end of registry block, must be removed
# first to restore registry block continuity), then strips remaining blocks.
WEBHOOK_REGISTRY_BLOCK = """

# ═══════════════════════════════════════════════════════════════
# Preprocess Registry — allows tools modules to register payload
# preprocessors that run before prompt rendering (AimailGateway)
# ═══════════════════════════════════════════════════════════════

PREPROCESS_REGISTRY: Dict[str, Callable] = {}


def register_preprocessor(name: str, fn: Callable) -> None:
    \"\"\"Register a payload preprocessor function.

    Preprocessors receive (payload: dict, headers: dict) and return
    the (possibly modified) payload dict. Called before prompt
    rendering so the Agent sees preprocessed data.
    \"\"\"
    PREPROCESS_REGISTRY[name] = fn

"""
WEBHOOK_CALL_BLOCK = '''
        # ── Preprocess payload (AimailGateway integration) ──────────
        preprocess_name = route_config.get("preprocess")
        if preprocess_name:
            preprocessor = PREPROCESS_REGISTRY.get(preprocess_name)
            if preprocessor:
                try:
                    _pre = preprocessor(payload, dict(request.headers))
                except Exception as e:
                    logger.error(
                        "[webhook] preprocessor '%s' failed: %s",
                        preprocess_name, e
                    )
                    _pre = payload
                if _pre is None:
                    # Preprocessor swallowed the event (ping/pong
                    # interception in shared core) — respond ignored,
                    # do NOT continue rendering/agent run.
                    return web.json_response({"status": "ignored", "reason": "preprocess"})
                payload = _pre

'''
WEBHOOK_A2A_BLOCK = '''        # ── a2a_board: consume preprocessor prompt fields ──
        _wp = payload.get("_whoami_prompt")
        if _wp:
            prompt = prompt + "\\n\\n---\\n" + _wp
        _rp = payload.get("_role_prompt")
        if _rp:
            prompt = prompt + "\\n\\n---\\n" + _rp
        _sk = payload.get("_a2a_session_key")
        if _sk:
            session_chat_id = f"webhook:{route_name}:{_sk}"

'''
WEBHOOK_ADAPTER_BLOCK = '''
# ── AimailGateway Hermes adapter (shared core injection + registration) ──
try:
    from aimail.hermes import aimail_hermes  # noqa: F401
except Exception:
    aimail_hermes = None

# ── agent-scope scheduled polling entry (pull-entry, pysdk/aimail_base) ──
# The host (gateway process) initialization finalization hooks up "its own pull wake-up path": addresses activated by address-level activation codes
# cannot obtain push (the gateway writes webhook_url = NULL for them), so they can only poll themselves on a schedule. Only
# agent-scope bindings are enabled; if not bound / disabled by AIMAIL_PULL=0, it is a no-op. The derived thread
# is a daemon ⇒ stops when the process exits, with additional atexit cleanup.
if aimail_hermes is not None:
    try:
        aimail_hermes.ensure_agent_pull_started()
    except Exception:
        pass

'''


# ── Key anchor list (P2, owner 2026-09-30): name → exclusive criterion string for that block ──────────────
# Same source as WEBHOOK_*_BLOCK (criterion strings must appear verbatim in the corresponding insertion block), the caller (and
# the guard in tests/cli/docker/journey-in-host.sh) only checks for "hook in place" based on the strings here.
WEBHOOK_REQUIRED_ANCHORS = (
    ("registry block (PREPROCESS_REGISTRY definition)",
     "PREPROCESS_REGISTRY: Dict[str, Callable] = {}"),
    ("preprocessor call hook (CALL block)",
     "preprocessor = PREPROCESS_REGISTRY.get(preprocess_name)"),
    ("adapter import block (from aimail.hermes import aimail_hermes)",
     "from aimail.hermes import aimail_hermes"),
)


def missing_webhook_anchors(text: str) -> list:
    """P2 (owner 2026-09-30): **Key anchors** after patching must be present —— criteria only increase, never decrease.

    Missing any block means "declared but not implemented": missing registry ⇒ no registry;
    missing call hook ⇒ registry exists but no call, preprocessor **never executes** (agentmail.log always 0 lines,
    while 202 is still returned by native non-blocking receipt ⇒ appearance is "installed"); missing adapter import ⇒
    adapter not attached. Returns list of missing block names (empty = complete).
    """
    return [name for name, anchor in WEBHOOK_REQUIRED_ANCHORS
            if anchor not in text]


def webhook_patch_gaps(target_path: str) -> list:
    """Key anchor health check for disk form (for callers like install_hermes to determine failure)."""
    try:
        with open(target_path) as f:
            text = f.read()
    except OSError as e:
        return [f"webhook.py unreadable: {e}"]
    return missing_webhook_anchors(text)


# ── Prompt rendering anchor for Patch 3 (2026-09-30 forensics, compatible with forms A/B)────────────────
# Form A (old hermes handler): comment line `        # Format prompt from template`.
# Form B (current hermes —— handler rewritten to `with self._profile_scope(profile):`
#   inline rendering, comment removed): anchor is
#   `prompt = self._render_prompt(route_config.get("prompt", ""), payload, ...)`。
# Forensic original text (mirrors aimail-host-hermes's /opt/data/hermes-agent/gateway/platforms/
# webhook.py, 883 lines, not a git repo): `grep -c 'Format prompt from template'` = 0,
# only Form B ⇒ recognizing only A would cause patch 3 to silently skip ⇒ call hook anchor always 0 (preprocessor never executes).
PROMPT_ANCHOR_LEGACY = "# Format prompt from template"
PROMPT_ANCHOR_INLINE = 'prompt = self._render_prompt(route_config.get("prompt", "")'
CALL_BLOCK_MARKER = "# ── Preprocess payload (AimailGateway integration)"
PROMPT_ANCHOR_HINT = ("prompt rendering anchor (one of two): "
                      f"{PROMPT_ANCHOR_LEGACY!r} or {PROMPT_ANCHOR_INLINE!r}")


def _find_call_marker_line(lines: list):
    """Return index of the inserted call-block's marker line (indent-agnostic)."""
    for i, ln in enumerate(lines):
        if ln.strip().startswith(CALL_BLOCK_MARKER):
            return i
    return None


def _find_prompt_anchor_line(lines: list):
    """Return index of the prompt-render anchor line: Form A preferred, then try Form B.

    Form B takes the **first** hit line (= in webhook POST handler after script transformation,
    the `_render_prompt` line); `_render_prompt(...)` calls elsewhere in the file have different forms
    (`return {key: self._render_prompt(value, ...)}`) and will not falsely match.
    """
    for i, ln in enumerate(lines):
        if ln.strip().startswith(PROMPT_ANCHOR_LEGACY):
            return i
    for i, ln in enumerate(lines):
        if ln.strip().startswith(PROMPT_ANCHOR_INLINE):
            return i
    return None


def _strip_call_block_lines(text: str) -> tuple:
    """Strip already inserted call hook block line by line (any indentation) → (new text, whether stripped).

    Line range = [marker, prompt anchor) plus the blank line immediately adjacent to marker —— that blank line is
    created by the leading `\n` of WEBHOOK_CALL_BLOCK; deleting them together is what returns to the pre-patch bytes
    (idempotent re-run does not increase blank lines). patch and unpatch share this function ⇒ byte-for-byte symmetric.
    """
    lines = text.split('\n')
    mi = _find_call_marker_line(lines)
    if mi is None:
        return text, False
    ai = _find_prompt_anchor_line(lines)
    if ai is None or ai <= mi:
        return text, False
    start = mi - 1 if (mi > 0 and not lines[mi - 1].strip()) else mi
    del lines[start:ai]
    return '\n'.join(lines), True


def patch_webhook(target_path: str) -> bool:
    """Apply all AIMail sub-patches to a Hermes webhook.py file.

    Returns True if the file was modified (any add/update patch applied).
    Idempotent: already-patched files are detected and reported as ALREADY PATCHED.

    P2 (owner 2026-09-30): **Missing anchor no longer "WARNING then return success"** —— if any key anchor is missing
    i.e., return False (ERROR line names which block is missing); caller must distinguish "already applied" from "failed" by reading
    `webhook_patch_gaps(path)` to distinguish "already patched" from "patch failed" (empty = complete).
    """
    if not os.path.isfile(target_path):
        print(f"patch_webhook: target file not found: {target_path}", file=sys.stderr)
        return False

    git_root = _resolve_git_root(target_path)
    hermes_commit = _get_hermes_commit(git_root)
    anchors = _resolve_anchors(git_root, hermes_commit)

    print(f"Hermes commit: {hermes_commit}", file=sys.stderr)
    print(f"Anchors: typing={anchors['typing']} logger={anchors['logger']} prompt={anchors['prompt']}", file=sys.stderr)

    with open(target_path) as f:
        content = f.read()

    lines = content.split('\n')
    _original_lines = list(lines)  # snapshot for diagnosis
    patched = False

    # ── Patch 1: add Callable to typing import ────────────────────
    m = re.search(r'(from typing import .+)', content)
    if m and "Callable" not in m.group(1):
        content = content.replace(m.group(1), m.group(1) + ", Callable", 1)
        patched = True
        print("Patch 1: Callable added to import", file=sys.stderr)

    # ── Patch 2: add PREPROCESS_REGISTRY after logger (always replace) ──
    # Remove old instance if present. The removal must start at the box
    # header line ('# ══...' above the comment): the inserted block carries
    # its own box header, so a removal starting at the comment line leaves
    # one orphan header behind per re-run (blank-line drift would be
    # harmless, an extra box line would survive unpatch).
    content = re.sub(
        r'# ═{10,}\n# Preprocess Registry — allows tools modules to register payload\n'
        r'.*?PREPROCESS_REGISTRY\[name\] = fn\n+',
        '',
        content, count=1, flags=re.DOTALL
    )
    registry = WEBHOOK_REGISTRY_BLOCK

    # Insert after logger = logging.getLogger(__name__)
    logger_marker = "logger = logging.getLogger(__name__)"
    if logger_marker in content:
        content = content.replace(logger_marker, logger_marker + registry, 1)
        patched = True
        print("Patch 2: PREPROCESS_REGISTRY added/updated", file=sys.stderr)
    else:
        print("WARNING: could not find logger marker — patch 2 skipped", file=sys.stderr)

    # ── Patch 3: add preprocessor call in webhook handler (always replace) ──
    # Form A (old hermes, has `# Format prompt from template` comment): use original byte
    # form —— inserted text is byte-for-byte symmetric with unpatch's exact-strip, do not modify this section.
    # Form B (current hermes, comments removed): delete old blocks line by line + render anchors inline before the anchor line using **anchor line indentation**
    # insertion; otherwise patch 3 silently skips ⇒ call hook anchor is always 0.
    # NOTE: must NOT reuse the module-level `target` variable (holds the
    # webhook.py path from sys.argv[1]) — assigning it here silently
    # redirected every later `open(target, "w")` to a file literally named
    # "# Format prompt from template", leaving webhook.py unpatched while
    # the script reported success. Use a dedicated local name.
    if PROMPT_ANCHOR_LEGACY in content:
        # old_end uses an unindented anchor —— when the patch is re-run, that line may exist with different indentation (8-space manual patch
        # vs 16 spaces script patch), hardcoded indentation causes ValueError and interruption (webhook.py already patched
        # scenario).
        old_start = '        # ── Preprocess payload (AimailGateway integration) ──────────'
        if old_start in content:
            before = content[:content.index(old_start)]
            after  = content[content.index(PROMPT_ANCHOR_LEGACY):]
            content = before + after
        prompt_anchor = PROMPT_ANCHOR_LEGACY
        if prompt_anchor in content:
            content = content.replace(prompt_anchor, WEBHOOK_CALL_BLOCK + "        " + prompt_anchor, 1)
            patched = True
            print("Patch 3: preprocessor call added/updated", file=sys.stderr)
        else:
            print(f"WARNING: could not find {prompt_anchor!r} — patch 3 skipped; "
                  f"{PROMPT_ANCHOR_HINT}", file=sys.stderr)
    else:
        _l3 = content.split('\n')
        _mi = _find_call_marker_line(_l3)
        if _mi is not None:
            _ai = _find_prompt_anchor_line(_l3)
            if _ai is not None and _ai > _mi:
                _start = _mi - 1 if (_mi > 0 and not _l3[_mi - 1].strip()) else _mi
                del _l3[_start:_ai]
                print("Patch 3: removed existing preprocessor call block", file=sys.stderr)
        _ai = _find_prompt_anchor_line(_l3)
        if _ai is None:
            print(f"WARNING: no prompt-render anchor found — patch 3 skipped; "
                  f"{PROMPT_ANCHOR_HINT}", file=sys.stderr)
        else:
            # Anchor line indentation determines block indentation (current hermes anchor is inside `with self._profile_scope`, 12 spaces
            # ⇒ relative to canonical 8 spaces +4); if anchor line indentation < 8, no indentation (canonical as-is).
            _lead = len(_l3[_ai]) - len(_l3[_ai].lstrip(' '))
            _extra = max(_lead - 8, 0)
            _blk = WEBHOOK_CALL_BLOCK
            if _extra:
                _blk = ''.join((' ' * _extra + _seg) if _seg.strip() else _seg
                               for _seg in _blk.splitlines(keepends=True))
            _head = '\n'.join(_l3[:_ai])
            _tail = '\n'.join(_l3[_ai:])
            content = (_head + '\n' if _ai > 0 else '') + _blk + _tail
            patched = True
            print(f"Patch 3: preprocessor call added/updated "
                  f"(inline-render hermes form, indent +{_extra})", file=sys.stderr)

    # ── Patch 4: REMOVE legacy _log_ping_event from webhook.py (2026-08-16) ──
    # _log_ping_event moved to shared core aimail_base — webhook.py must
    # not carry its own copy (double-write of ping-pong log lines).
    content, _nr4 = re.subn(
        r'\n+def _log_ping_event\(.*?(?=\n(?:def |\Z))',
        '',
        content,
        count=0,
        flags=re.DOTALL
    )
    if _nr4:
        print(f"Patch 4: removed {_nr4} legacy _log_ping_event definition(s)", file=sys.stderr)
    else:
        print("Patch 4: no legacy _log_ping_event found (already clean)", file=sys.stderr)
    # ── Patch 5: REMOVE legacy ping-pong interception block (2026-08-16) ──
    # Ping/pong interception moved into the shared core
    # (aimail_base.process_inbound_mail, registered as the webhook
    # preprocessor). The webhook.py-level block is now dead code that
    # double-handles pings (preprocessor already swallowed them → payload is
    # None). Remove every legacy instance.
    content, _nr5 = re.subn(
        r'        # ── Ping-pong interception.*?pong_returned"\}\)\n+',
        '',
        content, count=0, flags=re.DOTALL
    )
    if _nr5:
        print(f"Patch 5: removed {_nr5} legacy ping-pong block(s)", file=sys.stderr)
    else:
        print("Patch 5: no legacy ping-pong block found (already clean)", file=sys.stderr)

    if patched:
        with open(target_path, "w") as f:
            f.write(content)
        print("OK")
    else:
        print("ALREADY PATCHED")

    # ── Version diagnosis ─────────────────────────────────────────
    # If commit is not in ANCHOR_MAP, auto-detect positions and
    # suggest a new entry for the Hermes team to commit.
    if hermes_commit != "unknown":
        _in_map = any(
            _is_ancestor(git_root, sc, hermes_commit)
            for sc, _ in WEBHOOK_ANCHOR_MAP
        )
        if not _in_map:
            detected = _auto_detect_anchors(_original_lines)
            print(file=sys.stderr)
            print(f"  ╔═══ NEW HERMES COMMIT: {hermes_commit}", file=sys.stderr)
            print("  ║ Not in WEBHOOK_ANCHOR_MAP — auto-detected:", file=sys.stderr)
            for k in ["typing", "logger", "prompt", "non_blocking"]:
                v = detected.get(k, "?")
                print(f"  ║   {k}: {v}", file=sys.stderr)
            print("  ║", file=sys.stderr)
            print("  ║ Suggested anchor entry:", file=sys.stderr)
            print(f'  ║   ("{hermes_commit}", {{"typing": {detected.get("typing","?")},'
                  f' "logger": {detected.get("logger","?")},'
                  f' "prompt": {detected.get("prompt","?")}}}),', file=sys.stderr)
            print("  ║ Add to WEBHOOK_ANCHOR_MAP and commit.", file=sys.stderr)
            print("  ╚═══", file=sys.stderr)


    # ── Patch 6: add a2a_board prompt field consumer (always replace) ──
    # Remove old instance if present
    _p6_marker = '        # ── a2a_board: consume preprocessor prompt fields ──'
    _p6_skip = False
    if _p6_marker in content:
        # Remove from marker to the blank line before next code. The end
        # anchor ('\n\n        # Store delivery info') assumes the handler
        # body indents 8 spaces; on files where the surrounding context
        # differs (older/newer layout), the anchor may be absent — treat a
        # leftover instance as already-applied and skip (never crash the
        # re-run, never duplicate the block).
        _p6_start = content.index(_p6_marker)
        _p6_end_marker = '\n\n        # Store delivery info'
        _p6_end = content.find(_p6_end_marker, _p6_start)
        if _p6_end != -1:
            content = content[:_p6_start] + content[_p6_end:]
        else:
            print("Patch 6: existing a2a block end-anchor not found — "
                  "skipping (assumed applied)", file=sys.stderr)
            _p6_skip = True

    _p6_block = WEBHOOK_A2A_BLOCK

    # Insert after session_chat_id assignment
    if not _p6_skip:
        _p6_target = 'session_chat_id = f"webhook:{route_name}:{delivery_id}"'
        if _p6_target in content:
            # Insert after the session_chat_id line (after the newline)
            content = content.replace(
                _p6_target + '\n',
                _p6_target + '\n' + _p6_block,
                1
            )
            patched = True
            print("Patch 6: a2a_board prompt field consumer added/updated", file=sys.stderr)
        else:
            print("WARNING: could not find session_chat_id assignment — patch 6 skipped", file=sys.stderr)

    # ── Patch 7: import Hermes adapter (pip form: aimail.hermes.aimail_hermes) ──
    # First remove any old adapter import block (tools.hermes old name/new name, or pip name), then insert
    # pip form import —— idempotent across pysdk/→pip migration.
    _p7_old_re = re.compile(
        r'# ── AimailGateway Hermes adapter \(shared core injection \+ registration\) ──\n'
        r'try:\n'
        r'    from [^\n]*?# noqa: F401\n'
        r'except Exception:\n'
        r'    pass\n'
    )
    content, _n7 = _p7_old_re.subn('', content, count=0)
    if _n7:
        patched = True
        print(f"Patch 7: removed {_n7} old adapter import block(s)", file=sys.stderr)

    _p7_block = WEBHOOK_ADAPTER_BLOCK
    if "from aimail.hermes import aimail_hermes" not in content:
        # Insert after the PREPROCESS_REGISTRY definition block (after register_preprocessor)
        _p7_target = "PREPROCESS_REGISTRY[name] = fn"
        if _p7_target in content:
            content = content.replace(_p7_target + "\n", _p7_target + "\n" + _p7_block, 1)
            patched = True
            print("Patch 7: aimail_hermes adapter import added (pip form)", file=sys.stderr)
        else:
            print("WARNING: could not find PREPROCESS_REGISTRY block — patch 7 skipped", file=sys.stderr)
    else:
        print("Patch 7: aimail_hermes already present (pip form)", file=sys.stderr)

    if patched:
        with open(target_path, "w") as f:
            f.write(content)
        print("OK")
    else:
        print("ALREADY PATCHED")

    # ── P2 hard validation (owner 2026-09-30): if any critical anchor block is missing ⇒ explicit failure, no longer
    # "return success after WARNING" (old behavior: if CALL anchor not found, only print warning, patch_webhook
    # still returns success ⇒ install prints applied/already, but preprocessor is never called).
    _missing = missing_webhook_anchors(content)
    if _missing:
        print("ERROR: critical anchor missing — " + "; ".join(_missing)
              + " — the preprocessor will not be called (the patch is incomplete); "
                "patch_webhook fails; check the host webhook.py version/anchors",
              file=sys.stderr)
        print("      Expected anchor original text (print which block is missing):", file=sys.stderr)
        for _nm, _an in WEBHOOK_REQUIRED_ANCHORS:
            if _nm in _missing:
                print(f"        · {_nm}: {_an!r}", file=sys.stderr)
        print(f"        · {PROMPT_ANCHOR_HINT}", file=sys.stderr)
        return False

    return patched


# ═══════════════════════════════════════════════════════════════
#  exact-text patch removal (fallback when hermes-agent is not a git
#  repo) — blocks must match what the patch inserts. Migrated from
#  cli/aimail (unpatch section, lines 1307-1524).
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

WEBHOOK_BLOCK1 = """
# ═══════════════════════════════════════════════════════════════
# Preprocess Registry — allows tools modules to register payload
# preprocessors that run before prompt rendering (AimailGateway)
# ═══════════════════════════════════════════════════════════════

PREPROCESS_REGISTRY: Dict[str, Callable] = {}


def register_preprocessor(name: str, fn: Callable) -> None:
    \"\"\"Register a payload preprocessor function.

    Preprocessors receive (payload: dict, headers: dict) and return
    the (possibly modified) payload dict. Called before prompt
    rendering so the Agent sees preprocessed data.
    \"\"\"
    PREPROCESS_REGISTRY[name] = fn


"""

# Block 2: Preprocessor invocation
# Inserted BEFORE "# Format prompt from template"
WEBHOOK_BLOCK2 = """        # ── Preprocess payload (AimailGateway integration) ──────────
        preprocess_name = route_config.get("preprocess")
        if preprocess_name:
            preprocessor = PREPROCESS_REGISTRY.get(preprocess_name)
            if preprocessor:
                try:
                    payload = preprocessor(payload, dict(request.headers))
                except Exception as e:
                    logger.error(
                        "[webhook] preprocessor '%s' failed: %s",
                        preprocess_name, e
                    )

"""

# Block 3: a2a_board prompt field consumer
# Inserted AFTER session_chat_id, BEFORE "# Store delivery info"
WEBHOOK_BLOCK3 = """        # ── a2a_board: consume preprocessor prompt fields ──
        _wp = payload.get("_whoami_prompt")
        if _wp:
            prompt = prompt + "\n\n---\n" + _wp
        _rp = payload.get("_role_prompt")
        if _rp:
            prompt = prompt + "\n\n---\n" + _rp
        _sk = payload.get("_a2a_session_key")
        if _sk:
            session_chat_id = f"webhook:{route_name}:{_sk}"
"""


# Block 3: Ping-pong interception
# Inserted BEFORE "# Non-blocking"
WEBHOOK_BLOCK4 = """        # ── Ping-pong interception (end-to-end test) ────────────────
        ping_subject = (payload.get("subject") or "").strip()
        if ping_subject.startswith("__aimail_ping__:"):
            ping_id = ping_subject.split(":", 1)[1].strip()
            if ping_id:
                try:
                    import json as _json, os as _os, sys as _sys
                    from datetime import datetime, timezone
                    _tools_dir = _os.path.join(_os.path.dirname(__file__), "..", "..", "tools")
                    _sys.path.insert(0, _os.path.abspath(_tools_dir) + "/hermes")
                    from aimail_tools import send_mail as _send_mail
                    pong_body = _json.dumps({
                        "ping_id": ping_id,
                        "event": {"prompt": prompt, "route": route_name,
                                  "delivery_id": delivery_id, "skills": skills},
                    }, indent=2)
                    _log_ping_event("ping_intercepted", ping_id, payload, "")
                    pong_result = _send_mail(
                        to=payload.get("from", ""),
                        subject="__aimail_pong__:" + ping_id, body=pong_body,
                        message_id=payload.get("message_id") or "",
                    )
                    pong_status = "ok" if pong_result.get("success") else pong_result.get("error", "?")
                except Exception as _e:
                    pong_status = str(_e)
                    logger.error("[ping] send_mail failed: %s", _e)
                _log_ping_event("pong_sent", ping_id, payload, pong_status)
            return web.json_response({"pong": ping_id, "status": "pong_sent"})

        elif ping_subject.startswith("__aimail_pong__:"):
            ping_id = ping_subject.split(":", 1)[1].strip()
            if ping_id:
                _log_ping_event("pong_returned", ping_id, payload, "")
            return web.json_response({"pong": ping_id, "status": "pong_returned"})

"""

# Block 5: _log_ping_event (was Block 4)
# Inserted AFTER session_chat_id, BEFORE "# Store delivery info" function (appended at end of file)
WEBHOOK_BLOCK5 = """
def _log_ping_event(dir_: str, ping_id: str, payload: dict, pong_status: str):
    \"\"\"Append a JSON line to agentmail.log for ping-pong tracking.\"\"\"
    import json, os as _os
    from datetime import datetime, timezone
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "dir": dir_, "ping_id": ping_id,
        "from": payload.get("from", ""),
        "to": payload.get("to", ""),
    }
    if pong_status:
        entry["pong_status"] = pong_status
    # Three-layer closure (2026-09-23): authoritative log = {home}/systems/{sid}/{addr}/agentmail.log
    _home = _os.environ.get("AIMAIL_HOME", "") or _os.path.expanduser("~/.aimail")
    _sid = ""
    _email = ""
    _pdir = _os.environ.get("HERMES_PROFILE_DIR", "")
    if not _pdir:
        _pdir = _os.path.expanduser("~/.hermes")
    _pointer = _os.path.join(_pdir, ".agentmail")
    if _os.path.isfile(_pointer):
        try:
            import json as _json
            _pd = _json.load(open(_pointer))
            _sid = _pd.get("system_id", "")
            _email = _pd.get("email", "")
        except:
            pass
    _log_dir = ""
    if _sid:
        _cleaned = (_email or "default").replace("@", "_")
        _log_dir = _os.path.join(_home, "systems", _sid, _cleaned)
    log_path = _os.path.join(_log_dir, "agentmail.log") if _log_dir else ""

    try:
        _os.makedirs(_log_dir, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\\n")
    except Exception:
        pass
"""


def unpatch_webhook(fp: Path) -> int:
    if not fp.exists():
        return 0
    text = fp.read_text()
    original = text
    changes = 0

    # 1) Canonical blocks — byte-identical to what patch_webhook() inserts
    #    today (patch and unpatch share the same set of constants, see WEBHOOK_*_BLOCK definitions).
    #    Order sensitive: patch 7's Hermes-adapter import is spliced into the middle of the trailing blank lines of patch 2 registry block
    #    (registry trailing `fn\n\n` is split), must first strip the adapter block
    #    to restore registry block continuity, then it can be matched as a whole block.
    for name, block in [
        ("Hermes adapter import",    WEBHOOK_ADAPTER_BLOCK),
        ("PREPROCESS_REGISTRY",      WEBHOOK_REGISTRY_BLOCK),
        ("preprocessor invocation",  WEBHOOK_CALL_BLOCK + "        "),
        ("a2a_board prompt fields",  WEBHOOK_A2A_BLOCK),
    ]:
        text, ok = strip_block(text, block)
        if ok:
            changes += 1

    # Form B (current hermes) call hook block is inserted with anchor line indentation (+4) ⇒ the exact-strip above
    # does not match, here strip by line (same function as patch ⇒ byte-for-byte symmetric). Form A has been
    # removed by exact-strip, marker not present ⇒ this step is no-op (order cannot be reversed: stripping by line first would eat
    # the original indentation of Form A anchor line, unpatch no longer restores byte-for-byte).
    _text2, _stripped = _strip_call_block_lines(text)
    if _stripped:
        text = _text2
        changes += 1

    # 2) Legacy blocks (byte form injected by old pysdk/cli; modern patch no longer inserts,
    #    only used to clean up hosts still with old patches) — best effort, no match is no-op.
    for name, block in [
        ("legacy PREPROCESS_REGISTRY",   WEBHOOK_BLOCK1),
        ("legacy preprocessor call",     WEBHOOK_BLOCK2),
        ("legacy a2a_board fields",      WEBHOOK_BLOCK3),
        ("ping-pong interception",       WEBHOOK_BLOCK4),
        ("_log_ping_event",              WEBHOOK_BLOCK5),
    ]:
        text, ok = strip_block(text, block)
        if ok:
            changes += 1

    # Adapter import block (pip form aimail.hermes / legacy tools.hermes) — remove
    text, n = re.subn(
        r'# ── AimailGateway Hermes adapter \(shared core injection \+ registration\) ──\n'
        r'try:\n    from [^\n]*?# noqa: F401\nexcept Exception:\n    pass\n',
        '', text, count=0)
    if n:
        changes += n

    # Block 5: Remove Callable from typing import
    text, n = re.subn(
        r'(from typing import .+?), Callable\n',
        r'\1\n',
        text, count=1
    )
    if n:
        changes += 1

    text = strip_trailing_blanks(text)

    if text != original:
        fp.write_text(text)
        print(f"  ✓ webhook.py: {changes} block(s) removed")
    else:
        print("  - webhook.py: no changes")
    return changes


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 patch_webhook.py <path/to/webhook.py>", file=sys.stderr)
        sys.exit(1)
    patch_webhook(sys.argv[1])

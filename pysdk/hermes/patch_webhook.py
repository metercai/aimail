"""Apply amail preprocessor patch to Hermes gateway/platforms/webhook.py.

Adds:
  1. PREPROCESS_REGISTRY dict + register_preprocessor() function
  2. Preprocessor invocation in webhook handler (before prompt rendering)

Auto-detects Hermes commit version and adjusts insertion points accordingly.
See HERMES_PATCH_MAP.md for details.

Library form of cli/hermes/apply_webhook_patch.py: the 7 sub-patches live in
patch_webhook(target_path) -> bool (True when the file was modified); stderr
diagnostics unchanged. Runnable compat: python3 patch_webhook.py <path/to/webhook.py>

Library now also ships the unpatch side: unpatch_webhook(fp) -> int removes the
patch by exact-text block stripping (WEBHOOK_BLOCK1-5 mirrored verbatim from
cli/agentmail).
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



def patch_webhook(target_path: str) -> bool:
    """Apply all 7 amail sub-patches to a Hermes webhook.py file.

    Returns True if the file was modified (any add/update patch applied).
    Idempotent: already-patched files are detected and reported as ALREADY PATCHED.
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
    # Remove old instance if present
    content = re.sub(
        r'# Preprocess Registry \u2014 allows tools modules to register payload\n'
        r'.*?PREPROCESS_REGISTRY\[name\] = fn\n+',
        '',
        content, count=1, flags=re.DOTALL
    )
    registry = """

# ═══════════════════════════════════════════════════════════════
# Preprocess Registry — allows tools modules to register payload
# preprocessors that run before prompt rendering (AmailGateway)
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

    # Insert after logger = logging.getLogger(__name__)
    logger_marker = "logger = logging.getLogger(__name__)"
    if logger_marker in content:
        content = content.replace(logger_marker, logger_marker + registry, 1)
        patched = True
        print("Patch 2: PREPROCESS_REGISTRY added/updated", file=sys.stderr)
    else:
        print("WARNING: could not find logger marker — patch 2 skipped", file=sys.stderr)

    # ── Patch 3: add preprocessor call in webhook handler (always replace) ──
    # Remove old instance if present: from the comment marker to blank line before # Format prompt
    # old_end 用无缩进锚点 —— 补丁重跑时该行可能以不同缩进存在(8 空格手工补丁
    # vs 16 空格脚本补丁),硬编码缩进会导致 ValueError 且中断(webhook.py 已补丁
    # 场景)。
    old_start = '        # ── Preprocess payload (AmailGateway integration) ──────────'
    old_end   = '# Format prompt from template'
    if old_start in content and old_end in content:
        before = content[:content.index(old_start)]
        after  = content[content.index(old_end):]
        content = before + after
    call_block = '''
        # ── Preprocess payload (AmailGateway integration) ──────────
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
    # Insert before "# Format prompt from template"
    # NOTE: must NOT reuse the module-level `target` variable (holds the
    # webhook.py path from sys.argv[1]) — assigning it here silently
    # redirected every later `open(target, "w")` to a file literally named
    # "# Format prompt from template", leaving webhook.py unpatched while
    # the script reported success. Use a dedicated local name.
    prompt_anchor = "# Format prompt from template"
    if prompt_anchor in content:
        content = content.replace(prompt_anchor, call_block + "        " + prompt_anchor, 1)
        patched = True
        print("Patch 3: preprocessor call added/updated", file=sys.stderr)
    else:
        print("WARNING: could not find '# Format prompt from template' — patch 3 skipped", file=sys.stderr)

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
    # None) and its __agentmail_pong__ prefix mismatched the gateway's
    # __amail_pong__ P0 interception. Remove every legacy instance.
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
    if _p6_marker in content:
        # Remove from marker to the blank line before next code
        _p6_start = content.index(_p6_marker)
        _p6_end = content.index('\n\n        # Store delivery info', _p6_start)
        content = content[:_p6_start] + content[_p6_end:]

    _p6_block = '''        # ── a2a_board: consume preprocessor prompt fields ──
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

    # Insert after session_chat_id assignment
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

    # ── Patch 7: import Hermes adapter (pip 形态: aimail.hermes.aimail_hermes) ──
    # 先移除任何旧适配器 import 块(tools.hermes 旧名/新名,或 pip 名),再插入
    # pip 形态 import —— 跨 pysdk/→pip 迁移幂等。
    _p7_old_re = re.compile(
        r'# ── AmailGateway Hermes adapter \(shared core injection \+ registration\) ──\n'
        r'try:\n'
        r'    from [^\n]*?# noqa: F401\n'
        r'except Exception:\n'
        r'    pass\n'
    )
    content, _n7 = _p7_old_re.subn('', content, count=0)
    if _n7:
        patched = True
        print(f"Patch 7: removed {_n7} old adapter import block(s)", file=sys.stderr)

    _p7_block = '''
# ── AmailGateway Hermes adapter (shared core injection + registration) ──
try:
    from aimail.hermes import aimail_hermes  # noqa: F401
except Exception:
    pass

'''
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

    return patched


# ═══════════════════════════════════════════════════════════════
#  exact-text patch removal (fallback when hermes-agent is not a git
#  repo) — blocks must match what the patch inserts. Migrated from
#  cli/agentmail (unpatch section, lines 1307-1524).
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
# preprocessors that run before prompt rendering (AmailGateway)
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
WEBHOOK_BLOCK2 = """        # ── Preprocess payload (AmailGateway integration) ──────────
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
        if ping_subject.startswith("__agentmail_ping__:"):
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
                        subject="__agentmail_pong__:" + ping_id, body=pong_body,
                        message_id=payload.get("message_id") or "",
                    )
                    pong_status = "ok" if pong_result.get("success") else pong_result.get("error", "?")
                except Exception as _e:
                    pong_status = str(_e)
                    logger.error("[ping] send_mail failed: %s", _e)
                _log_ping_event("pong_sent", ping_id, payload, pong_status)
            return web.json_response({"pong": ping_id, "status": "pong_sent"})

        elif ping_subject.startswith("__agentmail_pong__:"):
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
    _log_dir = _os.environ.get("AIMAIL_HOME") or _os.environ.get("AGENTMAIL_HOME", "")
    if not _log_dir:
        # Resolve email from profile dir .agentmail pointer
        _pdir = _os.environ.get("HERMES_PROFILE_DIR", "")
        if not _pdir:
            _pdir = _os.path.expanduser("~/.hermes")
        _pointer = _os.path.join(_pdir, ".agentmail")
        if _os.path.isfile(_pointer):
            try:
                import json as _json
                _pd = _json.load(open(_pointer))
                _email = _pd.get("email", "")
                if _email:
                    _log_dir = _os.path.expanduser("~/.agentmail/mail/" + _email.replace("@", "_"))
            except:
                pass
    if not _log_dir:
        _log_dir = _os.path.expanduser("~/.agentmail/mail/default")
    log_path = _os.path.join(_log_dir, "agentmail.log")
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

    for name, block in [
        ("PREPROCESS_REGISTRY",        WEBHOOK_BLOCK1),
        ("preprocessor invocation",    WEBHOOK_BLOCK2),
        ("a2a_board prompt fields",    WEBHOOK_BLOCK3),
        ("ping-pong interception",     WEBHOOK_BLOCK4),
        ("_log_ping_event",            WEBHOOK_BLOCK5),
    ]:
        text, ok = strip_block(text, block)
        if ok:
            changes += 1

    # Adapter import block (pip 形态 aimail.hermes / legacy tools.hermes) — remove
    text, n = re.subn(
        r'# ── AmailGateway Hermes adapter \(shared core injection \+ registration\) ──\n'
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

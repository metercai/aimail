"""toolsets.py — amail toolset registration for hermes-agent/toolsets.py.

patch_toolsets(hermes_dir) registers the 6 amail tool names into the
_HERMES_CORE_TOOLS list (port of cli/hermes/install-tools.sh step 2);
unpatch_toolsets(fp) removes them plus the TOOLSET_AMAIL dict block by
exact-text stripping (blocks mirrored verbatim from cli/agentmail —
who patches, who unpatchs). Runnable compat:
  python3 toolsets.py patch   <hermes-agent-dir>
  python3 toolsets.py unpatch <path/to/toolsets.py>
"""

import os
import re
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
#  exact-text patch removal for hermes-agent/toolsets.py — TOOLSET_AMAIL
#  must match what the installer inserts. Migrated from cli/agentmail
#  (unpatch section, lines 1307-1574).
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


TOOLSET_AMAIL = """    "agentmail": {
        "description": "Agent email tools: send, contacts, contact profiles, and thread summaries via amail",
        "tools": ["send_mail", "manage_contacts", "contact_profile", "set_contact_profile", "email_summary", "set_email_summary"],
        "includes": [],
    },
"""
# _HERMES_CORE_TOOLS tool names added by install-tools.sh
CORE_TOOL_NAMES = ["send_mail", "manage_contacts", "contact_profile",
                   "set_contact_profile", "email_summary", "set_email_summary"]


def patch_toolsets(hermes_dir: str) -> bool:
    """Register the 6 amail tool names into hermes-agent/toolsets.py.

    Port of install-tools.sh step 2 (python heredoc): each missing name is
    inserted as '    "<name>",' right after '_HERMES_CORE_TOOLS = ['
    (before the first list element). Idempotent — skips names already
    present anywhere in the file. Returns True when the file was modified.
    """
    toolsets_path = Path(hermes_dir) / "toolsets.py"
    if not toolsets_path.is_file():
        print(f"  hermes toolsets: {toolsets_path} missing (skip registration)")
        return False
    content = toolsets_path.read_text(encoding="utf-8")
    needs_write = False
    for name in CORE_TOOL_NAMES:
        if f'"{name}"' not in content:
            content = re.sub(
                r"(_HERMES_CORE_TOOLS\s*=\s*\[)",
                r'\1\n    "' + name + '",',
                content,
                count=1,
            )
            needs_write = True
    if not needs_write:
        print("  hermes toolsets: already registered (skip)")
        return False
    toolsets_path.write_text(content, encoding="utf-8")
    print("  hermes toolsets: registered core tool names")
    return True


def unpatch_toolsets(fp: Path) -> int:
    if not fp.exists():
        return 0
    text = fp.read_text()
    original = text
    changes = 0

    text, ok = strip_block(text, TOOLSET_AMAIL)
    if ok:
        changes += 1

    # Remove amail tool names from _HERMES_CORE_TOOLS
    for tool in [
        "set_email_summary", "email_summary",
        "set_contact_profile", "contact_profile",
        "manage_contacts", "send_mail",
    ]:
        line = f'    "{tool}",\n'
        if line in text:
            text = text.replace(line, '', 1)
            changes += 1

    if text != original:
        fp.write_text(text)
        print(f"  ✓ toolsets.py: {changes} item(s) removed")
    else:
        print("  - toolsets.py: no changes")
    return changes


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("patch", "unpatch"):
        print("Usage: python3 toolsets.py <patch|unpatch> <hermes_dir|toolsets.py>",
              file=sys.stderr)
        sys.exit(1)
    if sys.argv[1] == "patch":
        sys.exit(0 if patch_toolsets(sys.argv[2]) else 1)
    unpatch_toolsets(Path(sys.argv[2]))

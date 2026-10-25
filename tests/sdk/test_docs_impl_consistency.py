"""pytest wrapper for the documentation ↔ implementation consistency gate (there is another direct call in L0).

The gate body = ``tests/contract/check-docs-consistency.py`` (exit code 0=pass / 1=violation /
2=cannot judge). Here it is run as a RED-GREEN tri-state, to prevent "documentation promised, implementation didn't catch up" from drifting back:

* Default two bootstrap documents ⇒ rc=0;
* Intentionally write a wrong backtick symbol name ⇒ rc=1 and include ``file:line``;
* Write a contract value with one character wrong ⇒ rc=1 (literal hit on the single source of truth);
* Documentation unreadable ⇒ rc=2 (cannot judge, never pretend to pass).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "contract" / "check-docs-consistency.py"
MANIFEST = ROOT / "contract" / "aimail-contract.json"
DOC_EN = ROOT / "docs" / "agent-self-setup.md"
DOC_ZH = ROOT / "docs" / "agent-self-setup_zh.md"
# 2026-10-23: Original platform documentation retired and deleted, ownership of host-side contract facts
# migrated to the SDK adaptation layer's sole maintenance document (bilingual pair).
PLATFORM_EN = ROOT / "docs" / "agent-adapter-playbook.md"


def _run(*args: str) -> tuple:
    p = subprocess.run([sys.executable, str(SCRIPT), *args],
                       cwd=str(ROOT), capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def test_docs_are_consistent():
    rc, out = _run()
    assert rc == 0, out
    assert "PASS" in out, out


def test_bad_symbol_name_is_red_with_file_line(tmp_path):
    text = DOC_EN.read_text(encoding="utf-8")
    assert "`activate_address_code`" in text, "Documentation should have a backtick reference to activate_address_code"
    bad = tmp_path / "bad-doc.md"
    bad.write_text(text.replace("`activate_address_code`",
                                "`activate_address_code_typo`"), encoding="utf-8")
    rc, out = _run("--en", str(bad))
    assert rc == 1, out
    assert "activate_address_code_typo" in out, out
    assert "bad-doc.md:" in out, out  # file:line must be provided


def test_wrong_contract_value_is_red(tmp_path):
    """Contract values are pinned where they live: the host doc. A typo there
    must stay red (the agent-facing self-setup doc carries no infra names)."""
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    text = PLATFORM_EN.read_text(encoding="utf-8")
    assert man["binding_file"] in text, "Precondition: platform documentation originally referenced the source of truth value"
    # Corrupt the value so that "the source of truth value can no longer appear" (prefix-style substrings also count as hits, so it must be replaced entirely)
    bad_text = text.replace(man["binding_file"], man["binding_file"].replace(".", "_"))
    assert man["binding_file"] not in bad_text, "Precondition: the bad documentation indeed no longer contains the source of truth value"
    bad = tmp_path / "bad-platform.md"
    bad.write_text(bad_text, encoding="utf-8")
    rc, out = _run("--platform-en", str(bad))
    assert rc == 1, out
    assert "binding_file" in out, out  # report names which source of truth value is missing


def test_platform_doc_path_drift_is_red(tmp_path):
    """Host-side facts (inbound path + ports) are pinned to the platform doc:
    a typo there must stay red even though the self-setup doc is clean."""
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    text = PLATFORM_EN.read_text(encoding="utf-8")
    assert man["inbound_path"] in text, "Precondition: the platform documentation already references the true source path"
    bad_text = text.replace(man["inbound_path"], man["inbound_path"].replace("/", "_"))
    assert man["inbound_path"] not in bad_text
    bad = tmp_path / "bad-platform.md"
    bad.write_text(bad_text, encoding="utf-8")
    rc, out = _run("--platform-en", str(bad))
    assert rc == 1, out
    assert "inbound_path" in out, out


def test_missing_doc_cannot_judge():
    rc, out = _run("--en", str(ROOT / "docs" / "no-such-self-setup-doc.md"))
    assert rc == 2, out
    assert "CANNOT JUDGE" in out, out


def test_zh_doc_is_covered_when_present():
    # Owner ruling 2026-10-25: agent-self-setup ships EN-only to keep a single
    # activation entry point. The zh pair is therefore optional — when absent
    # the gate runs in EN-only mode (no zh assertion); when present it must be
    # non-empty so a half-removed pair cannot silently pass.
    if not DOC_ZH.is_file():
        return
    text = DOC_ZH.read_text(encoding="utf-8")
    assert text.strip(), "Chinese pair cannot be an empty file if present"

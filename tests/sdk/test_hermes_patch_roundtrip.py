"""Hermes webhook patch byte-round-trip baseline.

The Rust migration eval flags the hermes text patches as the highest
fidelity risk: unpatch must restore exactly what patch inserted (uninstall
relies on byte-level restore). These tests pin that symmetry on a minimal
webhook.py replica carrying the four anchor markers the patcher keys on.
"""
import pytest

from patch_webhook import (
    patch_webhook,
    unpatch_webhook,
    missing_webhook_anchors,
    webhook_patch_gaps,
)

FAKE_WEBHOOK = """# fake webhook.py — minimal Hermes-webhook-like structure
import logging
logger = logging.getLogger(__name__)

from typing import Optional

PREPROCESS_REGISTRY = {}


def handle_webhook(route_name, route_config, payload, request):
    # Format prompt from template
    prompt = payload.get("prompt", "")
    delivery_id = payload.get("delivery_id", "")
    session_chat_id = f"webhook:{route_name}:{delivery_id}"
    # Store delivery info
    return prompt, session_chat_id
"""


@pytest.fixture
def webhook_py(tmp_path):
    p = tmp_path / "webhook.py"
    p.write_text(FAKE_WEBHOOK)
    return p


def test_patch_roundtrip_byte_identical(webhook_py):
    orig = webhook_py.read_text()
    assert patch_webhook(str(webhook_py)) is True, "patch must modify"
    patched = webhook_py.read_text()
    assert len(patched) > len(orig)  # insertions happened
    # sanity: the pip-form adapter import + registry are present
    assert "from aimail.hermes import aimail_hermes" in patched
    assert "PREPROCESS_REGISTRY[name] = fn" in patched

    removed = unpatch_webhook(webhook_py)
    assert removed > 0
    assert webhook_py.read_text() == orig, "unpatch must restore bytes exactly"


def test_patch_stable_across_runs(webhook_py):
    """Idempotency at the functional level: re-patching must not duplicate
    any inserted block (whitespace may drift a blank line between runs —
    known, absorbed by unpatch's strip_trailing_blanks)."""
    assert patch_webhook(str(webhook_py)) is True
    once = webhook_py.read_text()
    patch_webhook(str(webhook_py))  # second run
    twice = webhook_py.read_text()
    markers = [
        "from aimail.hermes import aimail_hermes",   # patch 7 (adapter import)
        "PREPROCESS_REGISTRY[name] = fn",            # patch 2 (registry def)
        "Preprocess payload (AimailGateway integration)",  # patch 3 (call)
        "a2a_board: consume preprocessor prompt fields",  # patch 6 (a2a)
    ]
    for m in markers:
        assert once.count(m) == 1 and twice.count(m) == 1, f"block duplicated: {m}"
    # whitespace-only drift allowed; semantic content (strip blank lines)
    # must be identical
    import re
    assert re.sub(r"\n+", "\n", twice) == re.sub(r"\n+", "\n", once)


def test_unpatch_on_clean_file_is_noop(webhook_py):
    orig = webhook_py.read_text()
    removed = unpatch_webhook(webhook_py)
    assert removed == 0
    assert webhook_py.read_text() == orig


# ═══════════════════════════════════════════════════════════════════════════
# P2(2026-09-30) Call hook anchor: two host hermes forms + red-green bidirectional
# Forensics: mirror of aimail-host-hermes's /opt/data/hermes-agent/gateway/platforms/
# webhook.py (883 lines) **does not have** `# Format prompt from template` (current hermes
# rewrites handler to `with self._profile_scope(profile):` + inline `_render_prompt`),
# only recognizes form A ⇒ patch 3 silently skipped ⇒ hook anchor always 0 (preprocessor never executes).
# ═══════════════════════════════════════════════════════════════════════════
HOOK_ANCHOR = "preprocessor = PREPROCESS_REGISTRY.get(preprocess_name)"

FAKE_WEBHOOK_INLINE = """# fake webhook.py — current hermes form (inline prompt rendering, no Format-prompt comment)
import logging
logger = logging.getLogger(__name__)

from typing import Optional


class Handler:
    async def _handle_webhook(self, request, route_config, profile):
        payload = {}
        with self._profile_scope(profile):
            script = route_config.get("script")
            if script:
                payload = script
            prompt = self._render_prompt(route_config.get("prompt", ""), payload, "e", "r")
        delivery_id = "1"
        session_chat_id = f"webhook:{route_name}:{delivery_id}"
        return prompt, session_chat_id
"""

FAKE_WEBHOOK_NO_ANCHOR = """# fake webhook.py — has neither the legacy comment anchor nor the inline render anchor
import logging
logger = logging.getLogger(__name__)

from typing import Optional

delivery_id = "1"
"""


@pytest.fixture
def webhook_py_inline(tmp_path):
    p = tmp_path / "webhook.py"
    p.write_text(FAKE_WEBHOOK_INLINE)
    return p


def test_patch_inline_render_form_inserts_call_hook(webhook_py_inline):
    """Local form (current hermes) ⇒ call hook anchor ≥1 and all key anchors present, file is still valid Python."""
    assert patch_webhook(str(webhook_py_inline)) is True, "patch must modify"
    patched = webhook_py_inline.read_text()
    assert patched.count(HOOK_ANCHOR) == 1, "Call hook must be inserted exactly once"
    assert webhook_patch_gaps(str(webhook_py_inline)) == [], "All key anchors must be present"
    compile(patched, str(webhook_py_inline), "exec")  # Indentation follows the anchor line, cannot corrupt the file

    # Idempotent re-run: still succeeds, hook does not double
    assert patch_webhook(str(webhook_py_inline)) is True
    twice = webhook_py_inline.read_text()
    assert twice.count(HOOK_ANCHOR) == 1, "Idempotent re-run inserted the hook block twice"

    # unpatch strips the hook (same line range rule as patch ⇒ symmetric)
    unpatch_webhook(webhook_py_inline)
    assert HOOK_ANCHOR not in webhook_py_inline.read_text()


def test_patch_unknown_form_fails_with_expected_anchor(tmp_path, capsys):
    """Non-matching form ⇒ rc≠0 (patch_webhook returns False) and message contains **expected anchor original text**."""
    p = tmp_path / "webhook.py"
    p.write_text(FAKE_WEBHOOK_NO_ANCHOR)
    assert patch_webhook(str(p)) is False, "Must judge as failure if anchors cannot be aligned"
    err = capsys.readouterr().err
    assert "ERROR: critical anchor missing" in err
    # Expected anchor original text: call hook anchor + two sets of prompt rendering anchors, name the missing block
    assert HOOK_ANCHOR in err, "Failure message must contain call hook anchor original text"
    assert "# Format prompt from template" in err, "Failure message must contain legacy rendering anchor original text"
    assert '_render_prompt(route_config.get("prompt", "")' in err, "Failure message must contain inline rendering anchor original text"
    assert missing_webhook_anchors(p.read_text()) != []
    assert webhook_patch_gaps(str(p)) != []

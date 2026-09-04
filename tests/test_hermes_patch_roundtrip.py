"""Hermes webhook patch byte-round-trip baseline.

The Rust migration eval flags the hermes text patches as the highest
fidelity risk: unpatch must restore exactly what patch inserted (uninstall
relies on byte-level restore). These tests pin that symmetry on a minimal
webhook.py replica carrying the four anchor markers the patcher keys on.
"""
import pytest

from patch_webhook import patch_webhook, unpatch_webhook

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
        "Preprocess payload (AmailGateway integration)",  # patch 3 (call)
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

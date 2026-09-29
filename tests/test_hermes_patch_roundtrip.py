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
# P2(2026-09-30) 调用钩子锚:两种宿主 hermes 形态 + 红绿双向
# 取证:镜像 aimail-host-hermes 的 /opt/data/hermes-agent/gateway/platforms/
# webhook.py(883 行)**没有** `# Format prompt from template`(现行 hermes 把
# handler 改写成 `with self._profile_scope(profile):` + 行内 `_render_prompt`),
# 只认形态 A ⇒ patch 3 静默跳过 ⇒ 钩子锚恒 0(preprocessor 永不执行)。
# ═══════════════════════════════════════════════════════════════════════════
HOOK_ANCHOR = "preprocessor = PREPROCESS_REGISTRY.get(preprocess_name)"

FAKE_WEBHOOK_INLINE = """# fake webhook.py — 现行 hermes 形态(行内 prompt 渲染, 无 Format-prompt 注释)
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

FAKE_WEBHOOK_NO_ANCHOR = """# fake webhook.py — 既没有 legacy 注释锚, 也没有行内渲染锚
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
    """本机形态(现行 hermes)⇒ 调用钩子锚 ≥1 且关键锚全齐、文件仍是合法 Python。"""
    assert patch_webhook(str(webhook_py_inline)) is True, "patch must modify"
    patched = webhook_py_inline.read_text()
    assert patched.count(HOOK_ANCHOR) == 1, "调用钩子必须恰好插入一次"
    assert webhook_patch_gaps(str(webhook_py_inline)) == [], "关键锚必须全齐"
    compile(patched, str(webhook_py_inline), "exec")  # 缩进跟着锚行走, 不能写坏文件

    # 幂等重跑:仍成功、钩子不翻倍
    assert patch_webhook(str(webhook_py_inline)) is True
    twice = webhook_py_inline.read_text()
    assert twice.count(HOOK_ANCHOR) == 1, "幂等重跑把钩子块插了两份"

    # unpatch 剥掉钩子(与 patch 同一行区间规则 ⇒ 对称)
    unpatch_webhook(webhook_py_inline)
    assert HOOK_ANCHOR not in webhook_py_inline.read_text()


def test_patch_unknown_form_fails_with_expected_anchor(tmp_path, capsys):
    """不匹配形态 ⇒ rc≠0(patch_webhook 返回 False)且文案含**期望锚原文**。"""
    p = tmp_path / "webhook.py"
    p.write_text(FAKE_WEBHOOK_NO_ANCHOR)
    assert patch_webhook(str(p)) is False, "锚齐不了就必须判失败"
    err = capsys.readouterr().err
    assert "ERROR: 关键锚缺失" in err
    # 期望锚原文:调用钩子锚 + 两套 prompt 渲染锚, 缺哪块点名哪块
    assert HOOK_ANCHOR in err, "失败文案必须带调用钩子锚原文"
    assert "# Format prompt from template" in err, "失败文案必须带 legacy 渲染锚原文"
    assert '_render_prompt(route_config.get("prompt", "")' in err, "失败文案必须带行内渲染锚原文"
    assert missing_webhook_anchors(p.read_text()) != []
    assert webhook_patch_gaps(str(p)) != []

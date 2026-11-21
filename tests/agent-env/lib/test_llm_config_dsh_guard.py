"""F3 guard (2026-11-20 QA): the dsh writer converges a duplicate `agent-default-model`
block and the verifier flags one it cannot explain.

The QA image `aimail-host-dsh` carried TWO `- id: agent-default-model` rows in
`<home>/profiles/<p>/cordis.patch.yml`. The writer's whole-row upsert is the single
repair point (a duplicate already in the profile layer must not survive a write), and
the verifier is the independent re-read layer (a duplicate on disk is a real red, never
a same-value no-op the check may wave through).

Run: python3 -m pytest tests/agent-env/lib/test_llm_config_dsh_guard.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent / "llm-config.py"
_spec = importlib.util.spec_from_file_location("llm_config_dsh_guard", _LIB)
assert _spec is not None and _spec.loader is not None
llm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(llm)

# The L2 gate endpoint (api.xiaomimimo.com) is the OpenAI route: it exercises the
# branch that writes the agent-default-model override — the exact row F3 duplicated.
URL = "https://api.xiaomimimo.com/v1"
MODEL = "mimo-v2.6-flash"


def _seed(home: Path, patch_text: str) -> Path:
    prof = home / "profiles" / "web"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "cordis.yml").write_text("x: 1\n", encoding="utf-8")
    (prof / "cordis.patch.yml").write_text(patch_text, encoding="utf-8")
    return prof / "cordis.patch.yml"


def _rows(p: Path, row_id: str) -> int:
    return sum(1 for ln in p.read_text(encoding="utf-8").splitlines()
               if ln.strip() == f"- id: {row_id}")


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    return tmp_path / "dsh-home"


def test_write_dedups_preexisting_agent_default_model_row(home: Path) -> None:
    """F3 as found: the file already carries the block twice; one write must leave exactly one."""
    dup = (
        "- id: llm-pi-ai\n"
        "  config:\n"
        "    providers:\n"
        "      xiaomi:\n"
        "        apiKeyEnv: LLM_API_KEY\n"
        "        api: openai-completions\n"
        f"        baseURL: {URL}\n"
        "        models:\n"
        f"          - id: {MODEL}\n"
        "            name: MiMo v2.6 flash\n"
        "            contextWindow: 128000\n"
        "            maxTokens: 8192\n"
        "            input: [text]\n"
        "            reasoningEfforts: false\n"
        "- id: agent-default-model\n"
        "  config:\n"
        "    provider: xiaomi\n"
        f"    model: {MODEL}\n"
        "- id: agent-default-model\n"
        "  config:\n"
        "    provider: xiaomi\n"
        f"    model: {MODEL}\n"
    )
    p = _seed(home, dup)
    assert _rows(p, "agent-default-model") == 2  # precondition: the QA state
    llm.write_dsh(home, URL, MODEL, "dummy")
    assert _rows(p, "agent-default-model") == 1, "writer must converge the duplicate"
    assert _rows(p, "llm-pi-ai") == 1, "the sibling managed row must survive untouched"


def test_write_is_idempotent_from_clean_state(home: Path) -> None:
    """The writer itself must never CREATE a duplicate: two runs, one row."""
    p = _seed(home, "# warmup rows\n- id: skill-filesystem\n  disabled: false\n")
    for _ in range(2):
        llm.write_dsh(home, URL, MODEL, "dummy")
    assert _rows(p, "agent-default-model") == 1
    assert _rows(p, "llm-pi-ai") == 1


def test_rows_between_duplicates_are_preserved(home: Path) -> None:
    """Dedup must drop the duplicate, not the unrelated row sitting between the two."""
    text = (
        "- id: agent-default-model\n"
        "  config:\n"
        "    provider: old\n"
        "    model: old\n"
        "- id: jobs\n"
        "  name: '@deepseek-ai/dsh-jobs-local'\n"
        "- id: agent-default-model\n"
        "  config:\n"
        "    provider: old\n"
        "    model: old\n"
    )
    p = _seed(home, text)
    llm.write_dsh(home, URL, MODEL, "dummy")
    body = p.read_text(encoding="utf-8")
    assert _rows(p, "agent-default-model") == 1
    assert "- id: jobs" in body, "an unrelated row between duplicates was lost"
    assert "provider: xiaomi" in body  # the surviving row is the writer's, not a stale one


def test_verify_flags_duplicate_rows(home: Path) -> None:
    """Independent layer: a duplicate on disk is red, even when both rows hold the same value."""
    same_value = (
        "- id: llm-pi-ai\n"
        "  config:\n"
        "    providers:\n"
        "      xiaomi:\n"
        "        apiKeyEnv: LLM_API_KEY\n"
        "        api: openai-completions\n"
        f"        baseURL: {URL}\n"
        "        models:\n"
        f"          - id: {MODEL}\n"
        "            name: MiMo v2.6 flash\n"
        "            contextWindow: 128000\n"
        "            maxTokens: 8192\n"
        "            input: [text]\n"
        "            reasoningEfforts: false\n"
        "- id: agent-default-model\n"
        "  config:\n"
        "    provider: xiaomi\n"
        f"    model: {MODEL}\n"
        "- id: agent-default-model\n"
        "  config:\n"
        "    provider: xiaomi\n"
        f"    model: {MODEL}\n"
    )
    _seed(home, same_value)
    bad = llm.verify_dsh(home, URL, MODEL)
    assert any("agent-default-model" in b and "F3" in b for b in bad), bad


def test_verify_clean_file_has_no_false_positive(home: Path) -> None:
    """The guard must not fire on a correctly written layer."""
    p = _seed(home, "[]\n")
    llm.write_dsh(home, URL, MODEL, "dummy")
    bad = llm.verify_dsh(home, URL, MODEL)
    assert bad == [], bad
    assert _rows(p, "agent-default-model") == 1

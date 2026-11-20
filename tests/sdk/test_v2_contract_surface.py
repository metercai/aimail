"""V2 surface conformance: activation prompt, tool surface, address types."""
from pathlib import Path

import os

REPO = Path(__file__).resolve().parents[2]


def _sibling(name: str, env: str) -> Path:
    """Locate a sibling repo: explicit env override → sibling of REPO → ~/<name>."""
    override = os.environ.get(env)
    if override:
        return Path(override).expanduser().resolve()
    candidate = REPO.parent / name
    if candidate.exists():
        return candidate
    fallback = Path.home() / name
    if fallback.exists():
        return fallback
    return candidate


GATEWAY = _sibling("aimail-gateway", "AIMAIL_GATEWAY_DIR")
ADVANCED = _sibling("aimail-advanced", "AIMAIL_ADVANCED_DIR")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_address_activation_prompt_is_install_and_tool_only():
    src = _read(GATEWAY / "src/core/api/activation.rs")
    assert "Activate and bind my AIMail mailbox address {address}" in src
    assert "using `aimail_address`" in src
    assert "agent_aimail" in src
    addr_start = src.index("An AIMail mailbox is ready for your agent")
    addr_end = src.index("This code is sent only once", addr_start)
    addr_block = src[addr_start:addr_end]
    assert "agent-self-setup.md" not in addr_block
    assert "{manager}" not in addr_block


def test_base_apply_page_has_no_system_code_entry():
    src = _read(GATEWAY / "src/core/api/activation.rs")
    page = src[src.index("const APPLY_PAGE"):src.index("pub fn base_activation_router")]
    assert "Claim system" not in page
    assert "apply-system" not in page
    assert "sdomain" not in page


def test_advanced_address_types_and_standalone_guard():
    src = _read(ADVANCED / "src/advanced/api/open_apply.rs")
    for t in ("shared", "shared_system", "dedicated"):
        assert f'"{t}"' in src
    assert 'address_type must be shared|shared_system|dedicated' in src
    assert 'standalone accepts only dedicated-domain address applications' in src


def test_sdk_is_v2_and_no_system_tool():
    import json
    pkg = json.loads(_read(REPO / "tssdk/packages/agent-aimail/package.json"))
    assert pkg["version"] == "0.5.0"
    manifest = json.loads(_read(REPO / "tssdk/packages/agent-aimail/openclaw.plugin.json"))
    tools = manifest["contracts"]["tools"]
    assert "aimail_address" in tools
    assert "aimail_system" not in tools
    assert len(tools) == 15


def test_address_tool_has_no_system_onboarding_params():
    import json
    spec = json.loads(_read(REPO / "pysdk/tool-spec.json"))
    tool = next(t for t in spec if t["name"] == "aimail_address")
    props = tool["parameters"]["properties"]
    for forbidden in ("system_code", "manager", "system_name", "domain"):
        assert forbidden not in props
    assert set(props) == {"action", "code", "email", "gateway_url", "workspace_path"}


def test_v2_docs_do_not_advertise_legacy_install():
    for rel in ("docs/aimail-host-sse-contract-v2.md", "docs/v2-release-notes.md"):
        text = _read(REPO / rel)
        assert "agent-install.sh" not in text
        assert "Follow the step-by-step" not in text

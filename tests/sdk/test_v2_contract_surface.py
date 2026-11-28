"""V2 surface conformance: activation prompt, tool surface, address types."""
from pathlib import Path

import os

import pytest

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


def _sibling_src(repo: Path, rel: str, env: str) -> str:
    """Read a **sibling-repo** source file, or skip (not fail) if that repo is not
    in this checkout.

    These are cross-repo contract checks (SDK ↔ gateway / advanced-gateway source).
    They run wherever the sibling repo is present (local dev, with both repos side by
    side). In the aimail CI the sibling repos are NOT checked out — and the SDK release
    does not bundle the gateway — so the authoritative cross-repo verification for a
    release is L3 (end-to-end against *published* gateway releases) + the gateway repo's
    own CI, not this dev-time cross-check. ⇒ absent source = honest skip (cannot judge
    here), never a false pass: where the sibling IS present the full assertions run."""
    p = repo / rel
    if not p.is_file():
        pytest.skip(
            f"sibling repo {repo.name} not in this checkout ({p} absent) — cross-repo "
            f"contract check runs where {env}/{repo.name} is present (local dev); the "
            f"release's authoritative cross-repo verification is L3 + the gateway repo's CI"
        )
    return _read(p)


def test_address_activation_prompt_is_install_and_tool_only():
    src = _sibling_src(GATEWAY, "src/core/api/activation.rs", "AIMAIL_GATEWAY_DIR")
    # The activation mail must send the agent to the TOOL and to the host's own plugin
    # mechanism — never to a CLI. The sentences themselves live in the gateway repo and do
    # change (v1.0.15 reworded the prompt: "Use `aimail_address` to activate and bind the
    # dedicated AIMail mailbox {address}"), so pin the INVARIANTS, not a copy: a pinned
    # sentence turns every gateway wording fix into an SDK red (measured 2026-11-24).
    assert "aimail_address" in src
    assert "agent_aimail" in src or "agent-aimail" in src
    assert "activate" in src and "bind" in src
    addr_start = src.index("An AIMail mailbox is ready for your agent")
    addr_end = src.index("This code is sent only once", addr_start)
    addr_block = src[addr_start:addr_end]
    assert "plugin" in addr_block.lower()          # install = the host's plugin mechanism
    assert "agent-self-setup.md" not in addr_block
    assert "{manager}" not in addr_block


def test_base_apply_page_has_no_system_code_entry():
    src = _sibling_src(GATEWAY, "src/core/api/activation.rs", "AIMAIL_GATEWAY_DIR")
    page = src[src.index("const APPLY_PAGE"):src.index("pub fn base_activation_router")]
    assert "Claim system" not in page
    assert "apply-system" not in page
    assert "sdomain" not in page


def test_advanced_address_types_and_standalone_guard():
    src = _sibling_src(ADVANCED, "src/advanced/api/open_apply.rs", "AIMAIL_ADVANCED_DIR")
    for t in ("shared", "shared_system", "dedicated"):
        assert f'"{t}"' in src
    assert 'address_type must be shared|shared_system|dedicated' in src
    assert 'standalone accepts only dedicated-domain address applications' in src


def test_sdk_is_v2_and_no_system_tool():
    import json
    pkg = json.loads(_read(REPO / "tssdk/packages/agent-aimail/package.json"))
    # The gate asserts the exact release coordinate, so a bump is deliberate and visible.
    # Moves with each release: 0.5.0 (never published) -> 0.5.1 -> 0.5.2.
    assert pkg["version"] == "0.6.1"
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

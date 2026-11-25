"""Pin the shipped surface: on the SSE-V2 line, nothing of the v1 push/CLI era ships any more.

Measured on agent_aimail-0.3.0-py3-none-any.whl (2026-11-24) and then revised twice on 2026-11-25:
the v1 hermes adapter left first (owner go-ahead), then the CLI-era installer, the door-ops entry and
the v1 deer-flow adapter left with the "keep only SSE-V2" ruling.

  absent  : pysdk/hermes/ (adapter + patchers), pysdk/install.py, pysdk/sdk_ops.py,
            pysdk/deer-flow/ (inbound router + manage + install scripts), pysdk/hosts/
  present : the v2 runtime — flat core modules, v2/, hermes_plugin/, deerflow_plugin/
  scripts : aimail-agent (the activate/pull bootstrap helper the v2 plugins import)

This test keeps the doc and the tree from drifting apart: any retired module coming back fails here.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / "pysdk"

#: Gone from the tree AND the wheel: the whole v1 push/CLI surface.
RETIRED = (
    "hermes",
    "deer-flow",
    "hosts",
    "install.py",
    "sdk_ops.py",
)
#: The v2 surface that must exist (a missing one means the wheel cannot deliver mail at all).
PRESENT = (
    "aimail_base.py",
    "aimail_contract.py",
    "aimail_agent.py",
    "v2/__init__.py",
    "hermes_plugin/__init__.py",
    "deerflow_plugin/__init__.py",
)


def test_the_v1_push_and_cli_surface_is_gone():
    back = sorted(m for m in RETIRED if (SDK / m).exists())
    assert not back, (
        f"retired v1 surface came back: {back} — SSE-V2 hosts deliver by pulling and bind only "
        "through an activation code; see docs/qa/KEEP-ONLY-SSE-V2-2026-11-25.md"
    )


def test_the_v2_runtime_is_present():
    missing = sorted(m for m in PRESENT if not (SDK / m).is_file())
    assert not missing, f"v2 runtime modules missing: {missing}"


def test_readme_does_not_claim_the_retired_surface_still_ships():
    readme = (SDK / "README.md").read_text(encoding="utf-8").lower()
    assert "no longer shipped" in readme
    assert "hermes adapter is gone" in readme

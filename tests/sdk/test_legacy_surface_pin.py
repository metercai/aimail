"""Pin the 0.3.0 legacy surface exactly as the built wheel behaves (measured, not assumed).

Measured on agent_aimail-0.3.0-py3-none-any.whl in a clean venv (2026-11-24):
  absent : patch_webhook, patch_profiles, register_profiles, ensure_config, toolsets
  present: install.py, sdk_ops.py, hermes/aimail_hermes.py   (importable)
  scripts: aimail-agent only  (no `aimail` CLI entry point)

The handoff's G-2 said the legacy surface was "moved out of the wheel"; the artifact says only the
five patch modules left. This test keeps the doc and the artifact from drifting apart again:
removing one of the present modules without updating README/§8.2 fails here, and re-introducing one of
the absent patch modules fails too.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / "pysdk"

SHIPPED_LEGACY = {"install.py", "sdk_ops.py", "hermes/aimail_hermes.py"}
ABSENT_PATCH = {"patch_webhook.py", "patch_profiles.py", "register_profiles.py",
                "ensure_config.py", "toolsets.py"}


def test_legacy_modules_that_ship_are_the_measured_ones():
    missing = sorted(m for m in SHIPPED_LEGACY if not (SDK / m).is_file())
    assert not missing, (
        f"{missing} vanished — that is a product change: update pysdk/README.md '0.3.0 — breaking', "
        "the handoff G-2 and this list in the same commit"
    )


def test_patch_era_modules_are_really_gone_from_the_source_tree():
    still = sorted(m for m in ABSENT_PATCH if (SDK / m).is_file())
    assert not still, f"patch-era module(s) came back: {still}"


def test_no_cli_entry_point_for_the_legacy_installer():
    """Only aimail-agent may be installed as a console script; no `aimail` CLI, no installer."""
    txt = (ROOT / "pyproject.toml").read_text()
    # isolate the console-script table instead of grepping the whole file ("[project.scripts]" is
    # legitimate for aimail-agent — my first version failed on its own over-broad assertion).
    block = ""
    for marker in ("[project.scripts]", "[project.entry-points.console_scripts]"):
        if marker in txt:
            rest = txt.split(marker, 1)[1]
            block += rest.split("\n[", 1)[0]
    assert "aimail-agent" in block or "[project.scripts]" in txt, "runtime entry point aimail-agent missing"
    for banned in ("aimail = ", "aimail-install", "install = "):
        assert banned not in block, f"legacy CLI wiring reappeared: {banned!r} in {block!r}"


def test_readme_does_not_claim_the_whole_legacy_surface_is_gone():
    readme = (SDK / "README.md").read_text().lower()
    assert "no longer shipped" in readme
    # the five patch helpers are covered by that sentence; the remaining three still ship, and the
    # README must not imply otherwise without saying so.
    assert ("still ship" in readme) or ("still present" in readme) or ("removal in 0.3.1" in readme), (
        "README must state that install.py / sdk_ops.py / hermes/aimail_hermes.py still ship"
    )

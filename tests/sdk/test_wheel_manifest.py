# -*- coding: utf-8 -*-
"""Every shipped SDK module must actually be in the wheel.

Why this file exists: ``pyproject.toml`` builds the package with an explicit
``force-include`` table (``pysdk/<x>.py → aimail/<x>.py``) because hatchling cannot rename
``packages``. That table is a hand-maintained list, so a new module is silently absent from
the wheel and only fails LATE — inside a real host, as ``cannot import name 'outbound' from
'v2'`` (measured 2026-11-24 on hermes L2: S1-S3 all RED because the plugin could not load).

This test reads the same table and asserts one row per source file, so the gap is caught at
unit-test time instead.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"

#: Directories whose every module must ship (host plugins + the shared V2 core).
SHIPPED_DIRS = ("pysdk/v2", "pysdk/hermes_plugin", "pysdk/deerflow_plugin")


def _force_include_rows() -> dict:
    text = PYPROJECT.read_text(encoding="utf-8")
    block = re.search(r"^\[tool\.hatch\.build\.targets\.wheel\.force-include\]\s*$(.*?)(?=^\[)",
                      text, re.S | re.M)
    assert block, "force-include table not found in pyproject.toml"
    rows = {}
    for src, dst in re.findall(r'^"([^"]+)"\s*=\s*"([^"]+)"\s*$', block.group(1), re.M):
        rows[src] = dst
    return rows


@pytest.mark.parametrize("directory", SHIPPED_DIRS)
def test_every_module_in_a_shipped_package_is_force_included(directory):
    rows = _force_include_rows()
    present = sorted(p.relative_to(ROOT).as_posix()
                     for p in (ROOT / directory).glob("*.py"))
    assert present, "expected at least one module in %s" % directory
    missing = [p for p in present if p not in rows]
    assert not missing, (
        "these modules exist in the tree but are NOT in pyproject's force-include table, so "
        "the published wheel cannot import them (a real host would fail to load the plugin): "
        "%s" % ", ".join(missing))


def test_force_include_rows_point_at_the_installed_package():
    """A destination outside ``aimail/`` would ship a module the package never imports."""
    rows = _force_include_rows()
    bad = {s: d for s, d in rows.items() if not d.startswith("aimail/")}
    assert not bad, "force-include destinations must live under aimail/: %s" % bad


def test_force_include_has_no_dangling_sources():
    """A row naming a deleted file would make the wheel build fail; catch it here first."""
    rows = _force_include_rows()
    dangling = [s for s in rows if s.startswith("pysdk/") and not (ROOT / s).is_file()]
    assert not dangling, "force-include lists files that no longer exist: %s" % dangling

# ── legacy must not ship ────────────────────────────────────────────────────────────

#: 0.2.x source-patch era. Installation is now a pip install + the host's own plugin
#: mechanism, so none of these may reach the published package (§6.8): a shipped
#: ``patch_webhook`` is exactly the "SDK has no CLI, no source patch" promise being broken.
LEGACY_SOURCES = (
    "pysdk/hermes/patch_webhook.py",
    "pysdk/hermes/patch_profiles.py",
    "pysdk/hermes/ensure_config.py",
    "pysdk/hermes/register_profiles.py",
    "pysdk/hermes/toolsets.py",
)


def test_legacy_patch_modules_are_not_shipped():
    rows = _force_include_rows()
    shipped = [s for s in LEGACY_SOURCES if s in rows]
    assert not shipped, (
        "legacy source-patch modules are back in the wheel — installation must be pip + "
        "the host plugin only: %s" % ", ".join(shipped))


def test_legacy_files_still_exist_for_the_explicit_switch_audit():
    """They leave the WHEEL, not the tree: their tests keep proving they stay inert."""
    gone = [s for s in LEGACY_SOURCES if not (ROOT / s).is_file()]
    assert not gone, "legacy modules were deleted instead of unshipped: %s" % gone


def test_host_registration_module_still_ships():
    """`hosts/registry.py` names ``aimail_hermes`` as the hermes register entry point."""
    rows = _force_include_rows()
    assert "pysdk/hermes/aimail_hermes.py" in rows

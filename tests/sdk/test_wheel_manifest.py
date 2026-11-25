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
#: 0.2.x source-patch era, **retired 2026-11-25** (owner go-ahead): the v1 hermes adapter and its
#: patchers are gone from the tree, not just from the wheel. Installation is `pip install
#: agent-aimail` + the host's own plugin entry point, so nothing may bring them back.
RETIRED_SOURCES = (
    "pysdk/hermes/patch_webhook.py",
    "pysdk/hermes/patch_profiles.py",
    "pysdk/hermes/ensure_config.py",
    "pysdk/hermes/register_profiles.py",
    "pysdk/hermes/toolsets.py",
    "pysdk/hermes/aimail_hermes.py",
)


def test_legacy_patch_modules_are_gone_from_the_tree_and_the_wheel():
    rows = _force_include_rows()
    shipped = [s for s in RETIRED_SOURCES if s in rows]
    assert not shipped, "retired v1 hermes sources are back in the wheel: %s" % ", ".join(shipped)
    back = [s for s in RETIRED_SOURCES if (ROOT / s).is_file()]
    assert not back, "retired v1 hermes sources are back in the tree: %s" % ", ".join(back)


def test_host_plugins_ship_and_the_v1_host_layer_does_not():
    """The wheel carries the two v2 host plugins; `pysdk/hosts/` (v1 host registry) is gone."""
    rows = _force_include_rows()
    assert "pysdk/hermes_plugin/__init__.py" in rows
    assert "pysdk/deerflow_plugin/__init__.py" in rows
    assert not (ROOT / "pysdk" / "hosts").exists(), "v1 host registry is back"
    assert not any(r.startswith("pysdk/hosts/") for r in rows), "v1 host registry is back in the wheel"

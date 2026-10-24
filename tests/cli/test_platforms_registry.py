"""Platform registry shape test —— data source = `aimail platform spec --json` (P2c S3b, 2026-10-16).

`cli/platforms.json` is retired (source of truth = host modules `cli/src/hosts/*.rs`);
the assertion semantics in this file are migrated verbatim from tests/sdk/, only the data source changes: the registry is always exported via the **built CLI
Binary **export (black-box, same source as the unit under test ⇒ asserting the shape of the real artifact, not some file in the repo).

Migration source (assertion semantics unchanged):
  · test_dsh_register_default_step.py          → dsh/pi register_default same shape + sorting
  · test_sdk_install_step_contract.py          → node host has no sdk_install / python host has it
  · test_hermes_pkgmgr_adapt.py                → hermes host/docker two-step self-adaptation shape + when gate
  · test_hermes_toolset_ensure.py (Section 3)     → write/read same-source yaml_toolsets glob
  · test_register_name_token_registry.py       → dsh/pi register template {name} token + planning plane

Prerequisite: rust battery has built `cli/target/debug/aimail` (run-cli-gates.sh order guarantees this).
Missing binary ⇒ loud failure (no SKIP: registry shape is a gate for this domain, not an environment option).
"""
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_BIN = _REPO / "cli" / "target" / "debug" / "aimail"
for _d in ("pysdk", os.path.join("pysdk", "hermes")):
    if str(_REPO / _d) not in sys.path:
        sys.path.insert(0, str(_REPO / _d))

import pytest  # noqa: E402


def _spec() -> dict:
    if not _BIN.exists():
        pytest.fail(f"CLI binary missing: {_BIN} (run rust battery first: cargo build)")
    out = subprocess.run([str(_BIN), "platform", "spec", "--json"],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


_SPEC = _spec()
PLATS = _SPEC["platforms"]


# ── dsh register_default isomorphic (from test_dsh_register_default_step.py)─────────────
def _steps(name: str) -> list:
    return PLATS[name]["install_steps"]


def _register_default(name: str) -> dict:
    hits = [s for s in _steps(name) if s.get("kind") == "register_default"]
    assert len(hits) == 1, f"{name} should have exactly one register_default step, actual {len(hits)}"
    return hits[0]


def test_dsh_has_register_default_step():
    st = _register_default("dsh")
    assert st["on_error"] == "warn"
    assert st["warn_hint"], "failure hint field must be present (registration failure must not be silent)"


def test_dsh_register_default_shape_matches_pi():
    """Shape alignment: top-level field set and when sub-field set must both match pi."""
    dsh, pi = _register_default("dsh"), _register_default("pi")
    assert set(dsh) == set(pi), (
        f"register_default node fields differ: dsh={sorted(dsh)} pi={sorted(pi)}")
    assert set(dsh["when"]) == set(pi["when"])
    assert dsh["when"] == {"cfg_complete": True}, "should skip when cfg is not ready (same condition as pi)"
    assert dsh["on_error"] == pi["on_error"] == "warn"
    # Hint field is in template form: both platforms use {sid} placeholder
    assert "{sid}" in dsh["warn_hint"] and "{sid}" in pi["warn_hint"]


def test_dsh_register_default_runs_after_plugin_ensure():
    """Ensure step (spawn) must be before registration: registering before the plugin is installed will fail."""
    kinds = [s.get("kind") for s in _steps("dsh")]
    assert "register_default" in kinds
    assert kinds.index("spawn") < kinds.index("register_default"), kinds


# ── sdk_install symmetric (from test_sdk_install_step_contract.py)─────────────────────
NODE_KINDS = {"node_entry"}
PY_KINDS = {"python_module", "python_script"}


def _step_kinds(p):
    return [s.get("kind") for s in (p.get("install_steps") or [])]


def test_node_hosts_have_no_sdk_install_step():
    offenders = {k: _step_kinds(p) for k, p in PLATS.items()
                 if (p.get("register") or {}).get("kind") in NODE_KINDS and "sdk_install" in _step_kinds(p)}
    assert not offenders, f"node host must not have sdk_install (should be installed by host package manager): {offenders}"


def test_python_hosts_keep_sdk_install_step():
    missing = [k for k, p in PLATS.items()
               if (p.get("register") or {}).get("kind") in PY_KINDS and "sdk_install" not in _step_kinds(p)]
    assert not missing, f"python host must have sdk_install step: {missing}"


def test_dsh_is_aligned_with_other_node_hosts():
    dsh, pi = PLATS["dsh"], PLATS["pi"]
    assert "sdk_install" not in _step_kinds(dsh), "dsh should align with pi: no sdk_install"
    assert (dsh.get("register") or {}).get("kind") == (pi.get("register") or {}).get("kind") == "node_entry"


def test_pysdk_only_assembles_python_hosts():
    """§1 Assembly ownership: node host assembly is within its own package ⇒ pysdk side must not have their assembly entry points or payloads."""
    src = (_REPO / "pysdk" / "install.py").read_text(encoding="utf-8")
    for fn in ("install_dsh", "install_pi", "install_openclaw"):
        assert f"def {fn}(" not in src, f"pysdk/install.py should no longer have node host assembly entry {fn} (§1)"
    for stray in ("dsh", "pi", "openclaw"):
        assert not (_REPO / "pysdk" / stray).exists(), \
            f"pysdk/{stray}/ should not exist (node host assembly belongs to their respective packages, §1)"


# ── hermes package manager self-adaptation (from test_hermes_pkgmgr_adapt.py)─────────────────────
HERMES_STEPS = PLATS["hermes"]["install_steps"]
HOST_STEP = next(s for s in HERMES_STEPS if s["kind"] == "spawn" and s["argv"][0] == "sh")
DOCKER_STEP = next(s for s in HERMES_STEPS if s["kind"] == "spawn" and s["argv"][0] == "docker")


def _body(step) -> str:
    return step["argv"][-1]


def test_host_step_adapts_uv_then_pip_then_ensurepip():
    body = _body(HOST_STEP)
    # Detection order: uv first → venv pip → ensurepip fallback (within single step, zero executor changes)
    assert body.index("command -v uv") < body.index("-m pip --version"), body
    assert body.index("-m pip --version") < body.index("ensurepip"), body
    assert "uv pip install --python" in body, body
    # Target = agent interpreter discovered within domain ({python}, C-4: no pre-set venv literal)
    assert "P='{python}'" in body, body
    assert "hermes-agent/venv" not in body, body


def test_docker_step_adapts_same_order_inside_container():
    body = _body(DOCKER_STEP)
    assert "docker exec" in " ".join(DOCKER_STEP["argv"][:3])
    # All three paths present (uv → venv pip + ensurepip ✓); only assert existence and invariants, do not bind old literals ✗
    assert "command -v uv" in body and "ensurepip" in body and "uv pip install" in body
    assert "-m pip install" in body
    # owner 2026-10-07: must always install **latest released version** (--upgrade ✓), and self-verify importability (otherwise not considered truly installed ✓)
    assert "--upgrade" in body, body
    assert "import aimail" in body, body


def test_when_guards_unchanged():
    # host step: real machine (non-docker) + platform marker directory present to run (interpreter discovered within domain, not in when); docker step: container state
    assert HOST_STEP["when"] == {
        "path_exists": "{home}/hermes-agent",
        "runtime_not": "docker",
    }
    assert DOCKER_STEP["when"] == {"runtime": "docker"}
    # SDK installation failure maintains warn semantics (F9: does not block install main flow)
    assert HOST_STEP["on_error"] == "warn"
    assert DOCKER_STEP["on_error"] == "warn"


# ── hermes write/read same-source glob (from test_hermes_toolset_ensure.py Section 3)──────────────
def test_write_path_is_the_file_check_reads(tmp_path):
    """Write target (ensure_config) and check read glob (yaml_toolsets.health_checks) are same-source."""
    import yaml
    import ensure_config
    import aimail_contract as _contract

    def _toolsets(cfg):
        return cfg.get("platform_toolsets") or {}

    checks = PLATS["hermes"]["health_checks"]
    toolsets_check = next(c for c in checks if c.get("kind") == "yaml_toolsets")

    # Default profile: writes to <HERMES_HOME>/config.yaml
    ensure_config.ensure_profile_config(tmp_path)
    # Named profile: writes to <HERMES_HOME>/profiles/<name>/config.yaml
    named = tmp_path / "profiles" / "p1"
    named.mkdir(parents=True)
    ensure_config.ensure_profile_config(named)

    matched = set()
    for pattern in toolsets_check["configs"]:
        matched.update(glob.glob(pattern.replace("{home}", str(tmp_path))))

    for written in (tmp_path / "config.yaml", named / "config.yaml"):
        assert str(written) in matched, (
            f"Written {written} is not in check read glob {toolsets_check['configs']}"
        )
        cfg = yaml.safe_load(written.read_text(encoding="utf-8")) or {}
        pt = _toolsets(cfg)
        assert _contract.AGENT_TOOLSET_NAME in (pt.get("webhook") or []) \
            or _contract.AGENT_TOOLSET_NAME in (pt.get("cli") or [])


# ── node host register only declares entry nodes (owner 2026-10-17: CLI has no naming responsibility) ──
def test_node_host_register_declares_entry_only():
    """Registrar parameters belong to the host package's built-in op entry ⇒ the registry must not backfill args/argv/preset or other host flags."""
    for plat in ("dsh", "pi", "openclaw"):
        r = PLATS[plat]["register"]
        assert r["kind"] == "node_entry", plat
        for dead in ("args", "argv", "preset"):
            assert dead not in r, f"{plat} register backfilled host flag {dead}"

"""hermes toolset ensure chain unit tests (J4e root cause regression lock, 2026-09-30).

J4e root cause (binary choice fixed = **(i) skip**, not wrong directory):
  iso16 L2 log line 86 `registered:0` (install self-report) and line 557
  `ensure_profile_config: ['config.yaml missing (/opt/data/config.yaml) — skipped']`
       are two appearances of the same gap —
    · First install: config.yaml does not exist yet ⇒ ensure returns directly (no write);
    · Subsequent installs: `.agentmail` pointer same system_id ⇒ register_emails entire segment skipped  contract-allowed: text description (non-code) references contract pointer filename
      (registered:0) ⇒ ensure is not called at all.
  Neither writes platform_toolsets ⇒ webhook session falls back to default toolset ⇒
  agent writes the reply but cannot send it (J4e sent=1 reply=0).
Write/read same source: Write target = HERMES_HOME/<profile>/config.yaml, read target = check's
`yaml_toolsets` glob (registry `hermes.health_checks`, now by `aimail platform spec
--json` export; assertions moved to tests/cli/test_platforms_registry.py),
Both are the same file (this test uses real glob assertions, not relying on "looking the same").

Coverage:
  1. Toolset not registered ⇒ registered after ensure (also fills in if config is missing);
  2. Already registered ⇒ no duplicate write (idempotent: no changes, file bytes unchanged);
  3. Registration marker (pointer already matched ⇒ registered:0) no longer short-circuits ensure.
"""
import json
import os
import re
import sys
from pathlib import Path

import yaml
import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_PATHS = [str(_REPO / _d) for _d in ("cli", "pysdk", os.path.join("pysdk", "hermes"))]


def _scope_paths():
    """Mount the repo's cli/pysdk/pysdk/hermes onto sys.path, returning an undo function (undo immediately after use)."""
    added = [p for p in _PATHS if p not in sys.path]
    for p in reversed(added):
        sys.path.insert(0, p)

    def _undo():
        for p in added:
            while p in sys.path:
                sys.path.remove(p)
    return _undo


_undo = _scope_paths()
import ensure_config  # noqa: E402
import aimail_contract as _contract  # noqa: E402 — single source of truth for contract constants (value = manifest)
_undo()


# aimail_base 6 platform injection points (source of truth: pysdk/hermes/aimail_hermes.py module level
# `core._X = ...` block in pysdk/hermes/aimail_hermes.py). import register_profiles ⇒ import aimail_hermes ⇒
# these 6 globals are **permanently written**, not restored when sys.path is undone.
_CORE_INJECTION_POINTS = (
    "_PROFILE_DIR_RESOLVER", "_CONFIG_LOADER", "_PERSONAS_PROVIDER",
    "_SOUL_PROVIDER", "_SKILLS_PROVIDER", "_BOARD_GATEWAY_SINK",
)
_core_snapshot: dict = {}   # snapshot before import → cleared after neutralization (fixture teardown fallback)


def _restore_core_injections():
    """Restore the aimail_base injection point to its value before import register_profiles."""
    global _core_snapshot
    if not _core_snapshot:
        return
    import aimail_base  # noqa: F401 — at this point it must already be in sys.modules (the snapshot came from it)
    for _attr, _val in _core_snapshot.items():
        setattr(aimail_base, _attr, _val)
    _core_snapshot = {}


def _register_profiles():
    """Lazily import register_profiles and **neutralize** the runtime global side effects it brings in.

    Bisect evidence (2026-09-30, actual run in this repo):
      · Only adding to sys.path                 ⇒ both files run 2 passed (harmless)
      · Adding path + import ensure_config ⇒ 2 passed (harmless)
      · Adding path + **import register_profiles** ⇒ 1 failed (reproduced)

    Pollution mechanism (fixed on 2026-09-30, actual output from probe /tmp/probe_mech.py):
      import register_profiles ⇒ import aimail_hermes ⇒ module-level execution
      `core._PROFILE_DIR_RESOLVER = _resolve_profile_dir; core._CONFIG_LOADER = _load_profile_config`
      (pysdk/hermes/aimail_hermes.py:834-835), permanently writing to the platform injection point in aimail_base that defaults to None.
      Platform injection point **permanently written**. Thus `aimail_tools._aimail_dir()` depends on
      `resolve_system_id_for_email()` 3rd step (platform pointer fallback) starts hitting the local real
      `~/.hermes/.agentmail`:  contract-allowed: textual description (non-code) referencing the contract pointer filename
        BEFORE: sid=''  resolver=None  loader=None
        AFTER : sid='shared-token-8872d2bb'  resolver=<_resolve_profile_dir>  loader=<_load_profile_config>
      `.search/index.db` thus lands in `systems/shared-token-…/…/mail/`, while
      test_local_mail_search asserts `_leaf(home)` = `systems/_unassigned/…/mail/`
      is never created ⇒ `sqlite3.OperationalError: unable to open database file`
      (`search_mail()["count"]==1` still passes, because write and read share the same source and shift together).
      The string `… registration failed: 'NoneType' object has no attribute 'register'` during import
      is noise from hermes's own registry being None, and is **unrelated** to this failure (the test calls
      aimail_tools.search_mail directly, bypassing the registry).

    neutralization: snapshot the 6 injection points before import, restore immediately after import (`_restore_core_injections`),
    and have fixture teardown do a final safety net. Only the test side is touched; not a single line of product code is changed.
    Deferred import to within the test case: importing during collection would cause the entire process to be injected before any test case runs."""
    global _core_snapshot
    undo = _scope_paths()
    try:
        import aimail_base  # snapshot first after setting up paths (injection points are still clean values at this point)
        _core_snapshot = {a: getattr(aimail_base, a, None)
                          for a in _CORE_INJECTION_POINTS}
        import register_profiles  # noqa: E402
        return register_profiles
    finally:
        _restore_core_injections()
        undo()

_TOOLSET = _contract.AGENT_TOOLSET_NAME  # Contract constant (manifest agent_skill_name / agent_toolset_name)


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _toolsets(cfg: dict) -> dict:
    return cfg.get("platform_toolsets") or {}


# ── 1. Toolset not registered ⇒ registered after ensure ──────────────────────────────────────
@pytest.fixture(autouse=True)
def _restore_hermes_env(monkeypatch):
    """Double insurance (added after 2026-09-30 fixed pollution):
    1) register_emails() will **directly write** os.environ(HERMES_PROFILE_DIR=…, without going through
       monkeypatch). After leakage, subsequent tests read values pointing to deleted tmp directories.
       monkeypatch.delenv will **restore the original value** after the test case ends (including cases where it originally did not exist).
    2) import register_profiles injected aimail_base platform injection point (see
       _register_profiles docstring's BEFORE/AFTER evidence) as a fallback after the test case ends**
       once more** after the test case ends —— in the normal path, _register_profiles' finally has already restored it, here it only prevents
       injection occurring again within the test case body."""
    monkeypatch.delenv("HERMES_PROFILE_DIR", raising=False)
    yield
    _restore_core_injections()


def test_ensure_registers_toolset_when_config_exists_but_toolset_missing(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({"model": {"default": "m"}}), encoding="utf-8")

    changes = ensure_config.ensure_profile_config(tmp_path)

    cfg = _read(cfg_path)
    pt = _toolsets(cfg)
    assert _TOOLSET in (pt.get("webhook") or []), changes
    assert _TOOLSET in (pt.get("cli") or []), changes
    assert cfg["platforms"]["webhook"]["enabled"] is True
    # Existing keys are not overwritten (idempotent semantics: only fill in missing ones)
    assert cfg["model"] == {"default": "m"}


def test_ensure_creates_config_yaml_when_missing(tmp_path):
    """On first install, hermes has not yet generated config.yaml —— no more "skipped" allowed."""
    cfg_path = tmp_path / "config.yaml"
    assert not cfg_path.exists()

    changes = ensure_config.ensure_profile_config(tmp_path)

    assert cfg_path.exists(), changes
    cfg = _read(cfg_path)
    pt = _toolsets(cfg)
    assert _TOOLSET in (pt.get("webhook") or []), changes
    assert _TOOLSET in (pt.get("cli") or []), changes
    assert cfg["platforms"]["webhook"]["enabled"] is True
    # File creation must appear in changes (observable, not silent)
    assert any(str(cfg_path) in c for c in changes)


# ── 2. Registered ⇒ no duplicate writes ───────────────────────────────────────────────────
def test_ensure_is_idempotent_when_already_registered(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({"model": {"default": "m"}}), encoding="utf-8")
    first = ensure_config.ensure_profile_config(tmp_path)
    assert any("platform_toolsets" in c for c in first)

    before = cfg_path.read_bytes()
    second = ensure_config.ensure_profile_config(tmp_path)

    assert second == [], second
    assert cfg_path.read_bytes() == before


def test_ensure_keeps_existing_webhook_secret(tmp_path):
    """secret changes will cause bridge to forward HMAC 401 — existing ones must be preserved as-is."""
    secret = "s" * 64
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "platforms": {"webhook": {"enabled": True, "port": 8644,
                                  "extra": {"port": 8644, "secret": secret}}},
    }), encoding="utf-8")

    ensure_config.ensure_profile_config(tmp_path)

    cfg = _read(cfg_path)
    assert cfg["platforms"]["webhook"]["extra"]["secret"] == secret
    assert _TOOLSET in (_toolsets(cfg).get("webhook") or [])


# ── 4. Registration marker short-circuit ⇒ after registered:0, configuration is still filled in (core regression) ─────────────
def test_register_emails_ensures_even_when_pointer_matches(tmp_path, monkeypatch, capsys):
    register_profiles = _register_profiles()  # Lazy import, see _register_profiles comment
    home = tmp_path / "hermes-home"
    home.mkdir()
    profiles = home / "profiles"
    named = profiles / "p1"
    named.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": "m"}}), encoding="utf-8")

    system_id = "sid-iso17"
    # Pointer already matched ⇒ old logic skips the entire section (iso16: registered:0)
    (home / _contract.POINTER_FILE).write_text(json.dumps({"system_id": system_id}))
    (named / _contract.POINTER_FILE).write_text(json.dumps({"system_id": system_id}))

    registered = []
    monkeypatch.setattr(register_profiles, "load_gateway_config",
                        lambda: {"system_id": system_id, "admin_key": "k"})
    monkeypatch.setattr(register_profiles.aimail_hermes, "_auto_register_email",
                        lambda name, profile_dir, cfg: registered.append(name))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILES_DIR", str(profiles))

    register_profiles.register_emails()

    out = capsys.readouterr().out
    # Registration idempotent semantics maintained: pointer matched ⇒ no re-registration
    assert "registered:0" in out, out
    assert registered == []

    # But configuration completion must have actually run (old bug: nothing was written here)
    for cfg_dir in (home, named):
        pt = _toolsets(_read(cfg_dir / "config.yaml"))
        assert _TOOLSET in (pt.get("webhook") or []), cfg_dir
        assert _TOOLSET in (pt.get("cli") or []), cfg_dir


def test_register_emails_creates_config_for_fresh_home(tmp_path, monkeypatch, capsys):
    """Brand new HOME (no config.yaml, no pointer): one install must let the session get the toolset."""
    register_profiles = _register_profiles()  # Lazy import, see _register_profiles comment
    home = tmp_path / "fresh-home"
    home.mkdir()
    system_id = "sid-fresh"
    monkeypatch.setattr(register_profiles, "load_gateway_config",
                        lambda: {"system_id": system_id, "admin_key": "k"})
    monkeypatch.setattr(register_profiles.aimail_hermes, "_auto_register_email",
                        lambda name, profile_dir, cfg: None)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILES_DIR", str(home / "profiles"))

    register_profiles.register_emails()

    out = capsys.readouterr().out
    assert "registered:1" in out, out
    pt = _toolsets(_read(home / "config.yaml"))
    assert _TOOLSET in (pt.get("webhook") or []), pt
    assert _TOOLSET in (pt.get("cli") or []), pt


# ── 5. C-13 Lock (2026-10-11): Declaration surface must include YAML parser; missing it must fail loudly ────────
def test_pyyaml_is_a_declared_runtime_dependency():
    """Official uv host (bare CPython) only has ruamel-yaml, no PyYAML, and the install surface
    `pip install agent-aimail` **does not include extra** ⇒ if PyYAML only lies in extra, hermes
    adapter writing to host config will have no parser (C-13). This lock says "must be a hard dependency"."""
    root = Path(__file__).resolve().parent.parent.parent
    pt = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r"(?ms)^dependencies = \[(.*?)\]", pt)
    assert m, "pyproject.toml missing dependencies section"
    assert "PyYAML" in m.group(1), f"PyYAML must be a hard dependency (actual: {m.group(1).strip()})"


def test_missing_yaml_fails_loudly_instead_of_silently_skipping(tmp_path, monkeypatch, capsys):
    """When YAML parser is missing: registration entry must **exit non-zero + print reason**, and not produce config.

    Old behavior (`logger.warning` + `return None`) let install still report ok_text success,
    while platforms.webhook was never landed ⇒ host cannot start (L3 `[4b/6] port not set`).
    """
    register_profiles = _register_profiles()
    home = tmp_path / "no-yaml-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILES_DIR", str(home / "profiles"))
    monkeypatch.setattr(register_profiles, "load_gateway_config",
                        lambda: {"system_id": "sid-noyaml", "admin_key": "k"})
    # Only let "missing yaml" be the sole failure source: registration body (unrelated to YAML) succeeds as usual
    monkeypatch.setattr(register_profiles.aimail_hermes, "_auto_register_email",
                        lambda name, profile_dir, cfg: None)
    # Official uv host condition: no PyYAML in interpreter (sys.modules set to None ⇒ import raises ImportError)
    monkeypatch.setitem(sys.modules, "yaml", None)

    rc = register_profiles.register_emails()

    out = capsys.readouterr().out
    assert rc == 1, f"Missing parser must cause this step to exit non-zero (silent success = old defect); out={out}"
    assert "failed:ensure:" in out, out
    assert not (home / "config.yaml").exists(), "If it cannot be written, no artifact should be left behind"

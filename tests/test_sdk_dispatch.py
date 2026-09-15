"""SDK dispatch contract: the registry's `fn` must reach the SDK entry point.

Pins the 2026-09-15 fix. The table-driven sdk_install/sdk_uninstall steps were
dead ends: the CLI called _sdk_install/_sdk_uninstall WITHOUT the registry's
`fn` field, and both helpers bail out with a warning when fn is empty — so
`aimail install --home <platform-root>` never applied the host patches /
released resources, and `uninstall` never reverted them.
"""
import ast
import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "cli" / "aimail"
_REG = json.loads((_REPO / "cli" / "platforms.json").read_text())
if str(_REPO / "pysdk") not in sys.path:
    sys.path.insert(0, str(_REPO / "pysdk"))

import install as sdk  # noqa: E402  (pysdk SDK entry module)


def _load_cli():
    """cli/aimail has no .py suffix — load it as a module (top level only)."""
    loader = SourceFileLoader("aimail_cli_under_test", str(_CLI))
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("aimail_cli_under_test", loader))
    loader.exec_module(mod)
    return mod


# ── registry side ────────────────────────────────────────────────

def test_registry_sdk_steps_name_an_existing_sdk_entry():
    seen = 0
    for name, plat in _REG["platforms"].items():
        for key in ("install_steps", "uninstall_steps"):
            for st in plat.get(key) or []:
                if st.get("kind") in ("sdk_install", "sdk_uninstall"):
                    seen += 1
                    assert st.get("fn"), f"{name}/{key}: sdk step without fn (dead end)"
                    assert hasattr(sdk, st["fn"]), f"{name}: SDK has no {st['fn']}()"
    assert seen >= 4, "expected the hermes/deerflow install+uninstall steps"


# ── CLI wiring ───────────────────────────────────────────────────

def test_cli_passes_registry_fn_to_the_helpers():
    tree = ast.parse(_CLI.read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in ("_sdk_install", "_sdk_uninstall")]
    assert len(calls) == 2, "one dispatch site per helper (install + uninstall)"
    for call in calls:
        assert any(kw.arg == "fn" for kw in call.keywords), \
            f"{call.func.id}() does not pass the registry fn"


def test_sdk_install_helper_calls_the_registry_entry(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(sdk, "install_hermes",
                        lambda home, sid: seen.update(home=str(home), sid=sid) or 0)
    cli = _load_cli()
    assert cli._sdk_install("hermes", tmp_path, "sid-1", fn="install_hermes") == 0
    assert seen == {"home": str(tmp_path), "sid": "sid-1"}


def test_sdk_install_helper_warns_without_fn(tmp_path, capsys):
    cli = _load_cli()
    assert cli._sdk_install("hermes", tmp_path, "sid-1") == 1
    assert "fn" in capsys.readouterr().out

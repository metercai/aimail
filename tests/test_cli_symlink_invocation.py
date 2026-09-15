"""CLI invoked through the PATH symlink (bootstrap installs it as a symlink).

`~/.local/bin/aimail` is a symlink to `<program root>/aimail-src/cli/aimail`,
so anything the CLI derives from `__file__` must be resolved first.
`_platforms()` used the raw `__file__` and looked for `platforms.json` next to
the symlink -> FileNotFoundError, which took `aimail stats` down in the
bootstrapped shape (2026-09-15, caught while verifying the prod deploy).
"""
import importlib.util
import os
from importlib.machinery import SourceFileLoader
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "cli" / "aimail"


def _load(path, name="aimail_cli_symlink_under_test"):
    loader = SourceFileLoader(name, str(path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


def test_platforms_resolves_through_a_symlink(tmp_path):
    link = tmp_path / "aimail"
    os.symlink(_CLI, link)
    cli = _load(link)
    reg = cli._platforms()
    assert "deerflow" in reg["platforms"]
    # the registry really came from the resolved cli/ dir, not the symlink's dir
    assert not (tmp_path / "platforms.json").exists()

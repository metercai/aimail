# -*- coding: utf-8 -*-
"""_aimail_bootstrap — runtime core location resolution (single source).

Runtime modules are flat-script style (``import aimail_base``) and must
run from any location: the source repository (tools/), an installed bundle
(~/.aimail/... provisioner copies), or site-packages (pip aimail).
This module resolves the directory containing the shared core modules
(aimail_base.py / aimail_tools.py / aimail_board.py /
gateway_api.py) and puts it — plus the calling entry point's own
directory, for sibling imports — on sys.path.

Resolution order (most specific first):
  1. AIMAIL_RUNTIME_DIR env var (explicit override)
  2. the directory above the entry point (bundle / site-packages layout:
     adapter dirs sit directly under the core dir; the dir-name whitelist is
  3. the entry point's own directory (flat layout: entry lives in core)

The repo checkout is deliberately NOT a path fallback: installed runtimes
must be self-contained (bundles) or installed (pip). Development from the
repo works via case 3, which is local by definition.

Entry points use it like:

    def _aimail_bootstrap():
        import importlib.util as _ilu
        _here = os.path.dirname(os.path.abspath(__file__))
        for _d in (_here, os.path.dirname(_here)):
            _p = os.path.join(_d, "_aimail_bootstrap.py")
            if os.path.isfile(_p):
                _spec = _ilu.spec_from_file_location("_aimail_bootstrap", _p)
                _m = _ilu.module_from_spec(_spec)
                sys.modules["_aimail_bootstrap"] = _m
                _spec.loader.exec_module(_m)
                _m.ensure_core(_here)
                return
        raise ImportError("aimail runtime core not found — set AIMAIL_RUNTIME_DIR")

    _aimail_bootstrap()

    import aimail_base as _base          # noqa: E402
"""

import os
import sys


def _core_ok(d):
    return bool(d) and os.path.isfile(os.path.join(d, "aimail_base.py"))


# Note (2026-11-25): the adapter-directory whitelist is gone with the v1 flat adapters
# (`hermes/`, `deer-flow/`). The only Python entry points left are real packages
# (`hermes_plugin`, `deerflow_plugin`), so the "core sits above my adapter dir" step below can no
# longer fire — an adapter dir never appears on sys.path as a parent lookup target.


def ensure_core(self_dir=None):
    """Put the core module dir (and the entry point's own dir) on sys.path.

    Returns the resolved core dir, or None if no candidate exists.
    Idempotent: repeated calls do not duplicate entries.
    """
    self_dir = os.path.abspath(self_dir or os.path.dirname(os.path.abspath(__file__)))
    here = self_dir

    core = None
    # 1. explicit override
    env = os.environ.get("AIMAIL_RUNTIME_DIR", "").strip()
    if env:
        d = os.path.expanduser(env)
        if _core_ok(d):
            core = d
    # 2. parent dir (adapter layout: core sits above the adapter dir)
    # 3. own dir (flat layout: entry point lives in the core dir)
    if core is None and _core_ok(here):
        core = here

    if core is None:
        return None

    # adapter dir first (sibling imports), then core; dedupe both
    for d in (here, core):
        if d not in sys.path:
            sys.path.insert(0, d)
    return core


if __name__ == "__main__":
    print(ensure_core())

# -*- coding: utf-8 -*-
"""Host detection — "given a host home, answer which hosts can actually run here".

Ported from tssdk `src/hosts/detect.ts`, semantics aligned line by line:
  · multi-signal lazy, never throws — a missing signal is only "not detected", not an error;
  · Candidate roots = [home, home/<home_dir>]: caller provides host root (~/.hermes) or parent directory (~);
  · Signal sources (same source of truth as registry):
      ① marker present (host feature directory, e.g. ~/.hermes/hermes-agent)
      ② installed evidence (host's aimail pointer file <root>/<POINTER_FILE> exists)

Division of labor with CLI detect.rs: CLI performs "assembly-time" detection (locating home even in uninstalled state);
this layer performs "runtime" identification (confirming declared matches actual environment at entry load).
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from . import registry

# Pointer filename = contract literal, single source of truth (gate ratchet: forbids writing this literal bare).
# Dual forms: pip layout has core inside aimail package; repo dev / bundle layout has core attached to sys.path.
try:
    from aimail.aimail_contract import POINTER_FILE as _POINTER_FILE
except ImportError:  # pragma: no cover - layout branch
    from aimail_contract import POINTER_FILE as _POINTER_FILE


def _candidate_roots(home: str, host: str) -> List[str]:
    """Candidate roots: [home, home/<home_dir>] (parent directory semantics, consistent with CLI candidate_roots)."""
    roots = [home]
    home_dir = (registry.get_host(host) or {}).get("home_dir", "")
    if home_dir:
        nested = os.path.join(home, home_dir)
        if os.path.isdir(nested):
            roots.append(nested)
    return roots


def _signals_present(home: str, host: str) -> Dict[str, bool]:
    """The set of signals for this host under home (marker / installed pointer)."""
    spec = registry.get_host(host) or {}
    out = {"marker": False, "installed": False}
    for root in _candidate_roots(home, host):
        marker = spec.get("marker", "")
        if marker and os.path.exists(os.path.join(root, marker)):
            out["marker"] = True
        if os.path.isfile(os.path.join(root, _POINTER_FILE)):
            out["installed"] = True
        if out["marker"] and out["installed"]:
            break
    return out


def detect_host(home: str) -> Optional[str]:
    """Returns the id when **exactly one** python host is identified in the home;
    zero or multiple hits ⇒ None (ambiguity/empty; activate decides consistent, no throw)."""
    hits = [h for h in registry.host_ids() if _signals_present(home, h)["marker"]]
    return hits[0] if len(hits) == 1 else None


def detect_hosts_present(home: str) -> List[str]:
    """**All** host ids that hit in the home (no info loss when multiple hosts coexist)."""
    return [h for h in registry.host_ids() if _signals_present(home, h)["marker"]]

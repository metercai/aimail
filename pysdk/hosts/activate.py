# -*- coding: utf-8 -*-
"""Host activation verdict — the verified/consistent semantics of declared vs detected.

Ported from tssdk `src/hosts/activate.ts`, semantics aligned line by line:

  · exactly-one signal  ⇒ verified=True (environment is exactly the declared host)
  · declared hits 0     ⇒ consistent=False (wrong host installed), **warn only, never raise**
    (tssdk original: "never throw on mismatch — the host may be mid-bootstrap")
  · multiple hosts coexist ⇒ still activate by declared, but consistent reflects ambiguity

Entry call sites (isomorphic to tssdk "activate on entry load"):
  hermes:  aimail_hermes.py module load/registration entry
  deerflow: manage.py registration entry
"""
from __future__ import annotations

import os
import sys
from typing import Dict, Optional

from . import detect, registry

_WARN_PREFIX = "[aimail-hosts]"


def _warn(msg: str) -> None:
    # Identification layer never blocks host: diagnostics go to stderr (same semantics as tssdk console.warn)
    print(f"{_WARN_PREFIX} {msg}", file=sys.stderr)


def activate_host(declared: str, home: str) -> Dict[str, object]:
    """Re-verify in the home by declared (the host id explicitly declared by the caller).

    Returns:
      {
        "declared":   str,   # declared value (as-is)
        "detected":   str|None,  # the id when exactly one host hits in the home
        "present":    [str],     # all host ids that hit in the home
        "known":      bool,      # whether declared is in registry
        "verified":   bool,      # exactly one signal and = declared
        "consistent": bool,      # declared ∈ present (wrong host installed ⇒ False)
      }

    never raises: unknown declared / empty detection ⇒ return structure + warn.
    """
    declared = str(declared or "").strip()
    home = str(home or "").strip()
    known = registry.get_host(declared) is not None
    if not known:
        _warn(
            f"unknown declared host {declared!r} "
            f"(known: {', '.join(registry.host_ids())})"
        )

    present = detect.detect_hosts_present(home) if home else []
    det = detect.detect_host(home) if home else None

    verified = known and det is not None and det == declared
    consistent = known and (declared in present)

    if not consistent:
        _warn(
            f"declared host {declared!r} not detected at {home!r} "
            f"(present: {present or 'none'}) — check --home / host layout"
        )
    return {
        "declared": declared,
        "detected": det,
        "present": present,
        "known": known,
        "verified": verified,
        "consistent": consistent,
    }

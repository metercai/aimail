# -*- coding: utf-8 -*-
"""aimail.hosts — Python host identification layer (ported from tssdk hosts trio, 2026-10-21 merge plan).

Host single source of truth under the common subject architecture:

  registry.py   HOSTS table (hermes / deerflow) —— adding a new python host = adding an entry
  detect.py     finds "which hosts are actually installed" in a host home (multi-signal, lazy, never throws)
  activate.py   the verified/consistent verdict of declared vs detected (re-verification at the registration entry)

Alignment discipline (single source of truth, no drift):
  · marker / home_dir consistent field-by-field with CLI host table (cli/src/hosts/{hermes,deerflow}.rs)
  · Semantics consistent with tssdk src/hosts/{registry,detect,activate}.ts:
      exactly-one signal ⇒ verified; mismatch ⇒ warn only, never raise
  · the CLI's host table remains the CLI-side **platform-knowledge** single source (detection/pointer/install steps); this layer is the SDK
    side's **runtime identification** source of truth —— fields on both sides must be consistent, changing one side must sync the other.
"""
from . import detect, registry, activate  # noqa: F401

__all__ = ["detect", "registry", "activate", "activate_host", "HOSTS", "host_ids", "get_host"]

from .registry import HOSTS, host_ids, get_host  # noqa: F401,E402
from .activate import activate_host  # noqa: F401,E402

# Shared fixtures for the aimail CLI/pysdk behavior baseline.
#
# These tests pin the behaviors the future Rust CLI must reproduce (see
# cli-rust-migration-eval.md): v1 signature protocol, check JSON/probe
# semantics, and the hermes patch byte-round-trip. They run against the
# repo sources (cli/ + pysdk/) — no install needed.
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("cli", "pysdk", os.path.join("pysdk", "hermes")):
    _p = os.path.join(_REPO, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

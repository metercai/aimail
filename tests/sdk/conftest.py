# Shared fixtures for the aimail SDK (pysdk) behavior baseline.
#
# These tests pin SDK-side behaviors the release must keep: v1 signature
# protocol, check JSON/probe semantics, binding-file ownership, and the
# hermes patch byte-round-trip. They run against the repo sources (pysdk/)
# — no install needed.
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _d in ("pysdk", os.path.join("pysdk", "hermes")):
    _p = os.path.join(_REPO, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

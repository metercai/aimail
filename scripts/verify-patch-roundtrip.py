#!/usr/bin/env python3
"""AUDIT-1 固化: deer-flow app.py patch↔unpatch 往返 == 原文 + 幂等。
Run: python3 /home/ubuntu/aimail/scripts/verify-patch-roundtrip.py"""
import importlib.util
import os
import sys
import tempfile

PS = "/home/ubuntu/aimail/pysdk"
sys.path.insert(0, PS)

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

md = load("aud_md", os.path.join(PS, "deer-flow/manage.py"))

SAMPLE = '''"""DeerFlow gateway app."""

from fastapi import FastAPI
from app.gateway.routers import (
    agents,
)

app = FastAPI()

if __name__ == "__main__":
    app.include_router(agents.router)
'''

d = tempfile.mkdtemp()
backend = os.path.join(d, "backend")
os.makedirs(os.path.join(backend, "app", "gateway", "routers"))
app_py = os.path.join(backend, "app", "gateway", "app.py")
open(app_py, "w").write(SAMPLE)

# patch → unpatch → original
md.patch_backend_app(backend)
patched = open(app_py).read()
assert patched != SAMPLE and "aimail_inbound" in patched, "patch failed"
md.unpatch_backend_app(backend)
after = open(app_py).read()
assert after == SAMPLE, "roundtrip NOT exact"
print("deerflow app.py roundtrip == original: OK")

# idempotency: unpatch on clean file is a no-op (returns False, content stable)
md.patch_backend_app(backend)
md.patch_backend_app(backend)  # second patch is a skip
p2 = open(app_py).read()
assert p2.count("aimail_inbound") == 2, "patch not idempotent (import + include_router each once)"
print("deerflow patch idempotent (skip on second run): OK")
md.unpatch_backend_app(backend)
assert open(app_py).read() == SAMPLE
print("deerflow ALL OK")

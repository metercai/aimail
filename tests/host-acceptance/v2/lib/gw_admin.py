#!/usr/bin/env python3
"""Register the fixture domain through the gateway admin API (signed, SDK-free)."""
import argparse
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request


def sign(key, method, path, body):
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    ts = str(int(time.time() * 1000))
    base = f"{method.upper()}\n{path}\n{ts}\n{hashlib.sha256(body).hexdigest()}"
    return {"X-Api-Timestamp": ts,
            "X-Api-Signature": hmac.new(key_hash.encode(), base.encode(), hashlib.sha256).hexdigest()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--identity", required=True)
    ap.add_argument("--system", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--manager", default="")
    a = ap.parse_args()
    path = f"/api/v1/admin/systems/{a.system}/domains"
    body = json.dumps({"id": "dom-" + a.domain.replace(".", "-"),
                       "domain": a.domain,
                       **({"manager_address": a.manager} if a.manager else {})}).encode()
    headers = {"content-type": "application/json", "X-Api-Identity": a.identity, **sign(a.key, "POST", path, body)}
    req = urllib.request.Request(a.gateway + path, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(r.status, r.read().decode()[:200])
            return 0
    except urllib.error.HTTPError as e:
        txt = e.read().decode()[:200]
        # 409 = already exists, fine for an idempotent fixture
        print(e.code, txt)
        return 0 if e.code in (200, 201, 409) else 1


if __name__ == "__main__":
    raise SystemExit(main())

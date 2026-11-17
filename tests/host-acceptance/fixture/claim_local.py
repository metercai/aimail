#!/usr/bin/env python3
"""Claim a system code from a locally started gateway using the SMTP sink spool."""
import argparse, json, re, sys, time, urllib.request

def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} {url}: {e.read().decode(errors='replace')}")

def wait_code(spool, email, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            raw = open(spool, "rb").read().decode("utf-8", "replace")
        except FileNotFoundError:
            raw = ""
        for part in raw.split("---AIMAIL-MESSAGE---"):
            if email.lower() in part.lower():
                m = re.search(r"(?<!\d)(\d{6})(?!\d)", part)
                if m:
                    return m.group(1)
        time.sleep(0.3)
    raise SystemExit("verification code not found in SMTP spool")

ap = argparse.ArgumentParser()
ap.add_argument("--gateway", required=True)
ap.add_argument("--spool", required=True)
ap.add_argument("--email", required=True)
ap.add_argument("--name", default="QA")
ap.add_argument("--domain", default="test.local")
ap.add_argument("--kind", choices=["system","address"], default="system")
ap.add_argument("--agentname", default="alice")
a = ap.parse_args()
post(a.gateway + "/api/v1/public/email-verify/send", {"email": a.email})
vcode = wait_code(a.spool, a.email)
post(a.gateway + "/api/v1/public/email-verify/check", {"email": a.email, "code": vcode})
if a.kind == "system":
    r = post(a.gateway + "/api/v1/public/apply-system", {"email": a.email, "name": a.name, "domain": a.domain})
else:
    r = post(a.gateway + "/api/v1/public/apply-address", {"email": a.email, "name": a.name, "agentname": a.agentname, "domain": a.domain})
print(json.dumps(r))

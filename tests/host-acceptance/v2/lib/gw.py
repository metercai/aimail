#!/usr/bin/env python3
"""Fixture-side gateway client for the L2/L3 harness.

Independent of the SDK under test on purpose: provisioning/asserting the fixture
must not go through the code being gated. Implements the v1 API signature
(same canonical algorithm as pysdk.aimail_base.compute_api_signature).

subcommands
  apply   --gateway URL --email E --name N --agentname A --domain D
          -> prints {"address":..., "activation_code":...}
  wl      --gateway URL --key ADMIN --identity SID --system SID --addr A --sender S
          -> creates a bidirectional whitelist entry (fixture convenience)
  code    --gateway URL --key ADMIN --identity SID --system SID --domain D --addr A
          -> batch address activation code (admin/batch path; for negative tests)
"""
import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def sign(api_key: str, method: str, path: str, body: bytes) -> dict:
    if not api_key:
        return {}
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    ts = str(int(time.time() * 1000))
    base = f"{method.upper()}\n{path}\n{ts}\n{hashlib.sha256(body).hexdigest()}"
    sig = hmac.new(key_hash.encode(), base.encode(), hashlib.sha256).hexdigest()
    return {"X-Api-Timestamp": ts, "X-Api-Signature": sig}


def call(gateway, method, path, body=None, key="", identity=""):
    data = b"" if body is None else json.dumps(body).encode()
    headers = {"content-type": "application/json"}
    if key:
        headers.update(sign(key, method, path, data))
        if identity:
            headers["X-Api-Identity"] = identity
    req = urllib.request.Request(gateway + path, data=data if method != "GET" else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"body": raw.decode("utf-8", "replace")[:400]}


def inbox(gateway, email):
    q = urllib.parse.urlencode({"email": email})
    return call(gateway, "GET", f"/api/v1/public/mock-inbox?{q}")[1]


def cmd_apply(a):
    # The gateway verifies name+email+code as one identity (unified activation
    # reform): the code request carries the same name the submit will use.
    st, _ = call(a.gateway, "POST", "/api/v1/public/email-verify/send",
                 {"email": a.email, "name": a.name})
    if st not in (200, 201, 204):
        return {"ok": False, "stage": "verify-send", "status": st}
    codes = []
    # 1) mock inbox (authoritative when present)
    box = inbox(a.gateway, a.email)
    for m in box.get("messages", []):
        if not isinstance(m, dict):
            continue
        for cand in (m.get("text"), m.get("body"), m.get("html")):
            if cand:
                import re
                codes += re.findall(r"\b\d{6}\b", str(cand))
    # 2) SMTP sink spool — only the block addressed to THIS email
    if not codes and a.spool:
        import re, time as _t
        for _ in range(30):
            try:
                txt = open(a.spool, errors="replace").read()
            except FileNotFoundError:
                txt = ""
            for block in re.split(r"(?m)^(?=From |To |Received:)", txt):
                if a.email in block:
                    found = re.findall(r"\b\d{6}\b", block)
                    if found:
                        codes = found[-1:]
                        break
            if codes:
                break
            _t.sleep(0.5)
    if not codes:
        return {"ok": False, "stage": "inbox", "inbox": box}
    seen, cands = set(), []
    for c in reversed(codes):          # newest message first
        if c not in seen:
            seen.add(c); cands.append(c)
    checked = None
    for c in cands[:3]:                # the endpoint has a small attempt budget
        st, _ = call(a.gateway, "POST", "/api/v1/public/email-verify/check",
                     {"email": a.email, "name": a.name, "code": c})
        if st in (200, 201, 204):
            checked = c
            break
    if not checked:
        return {"ok": False, "stage": "verify-check", "candidates": cands[:3], "inbox": box}
    st, body = call(a.gateway, "POST", "/api/v1/public/apply-address",
                    {"email": a.email, "name": a.name, "agentname": a.agentname,
                     "domain": a.domain, "email_verify_code": checked})
    if st != 201 or not body.get("activation_code"):
        return {"ok": False, "stage": "apply", "status": st, "body": body}
    return {"ok": True, "address": body["address"],
            "activation_code": body["activation_code"]}


def cmd_wl(a):
    st, body = call(a.gateway, "POST", "/api/v1/whitelists",
                    {"system_id": a.system, "domain_addr": a.addr,
                     "direction": "all", "value": a.sender,
                     "description": "qa fixture"}, key=a.key, identity=a.identity)
    return {"ok": st in (200, 201), "status": st, "body": body}


def cmd_code(a):
    st, body = call(a.gateway, "POST", "/api/v1/admin/activation-codes/batch",
                    {"code_type": "address", "system_id": a.system, "domain": a.domain,
                     "email_address": a.addr, "count": 1},
                    key=a.key, identity=a.identity)
    codes = (body or {}).get("codes") or []
    return {"ok": st == 200 and bool(codes), "status": st,
            "code": codes[0] if codes else None, "body": body}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["apply", "wl", "code"])
    ap.add_argument("--gateway", default="http://127.0.0.1:18080")
    ap.add_argument("--email", default=""); ap.add_argument("--name", default="QA User")
    ap.add_argument("--agentname", default="qaagent"); ap.add_argument("--domain", default="test.local")
    ap.add_argument("--key", default=""); ap.add_argument("--identity", default="")
    ap.add_argument("--system", default=""); ap.add_argument("--addr", default="")
    ap.add_argument("--sender", default="")
    ap.add_argument("--spool", default="")
    a = ap.parse_args()
    fn = {"apply": cmd_apply, "wl": cmd_wl, "code": cmd_code}[a.cmd]
    out = fn(a)
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""deerflow_drive.py — drive a deer-flow Gateway through its own HTTP API.

Why this file exists
--------------------
The L2 harness needs to run a *real* agent turn inside deer-flow the same way the
dsh/pi/openclaw adapters do: create a conversation, hand it one user message, and
read back what the model did. deer-flow's Gateway exposes exactly that surface, so
the fixture speaks it instead of reverse-engineering the host's internals:

    POST /api/threads                       {thread_id}          -> idempotent create
    POST /api/threads/{tid}/runs            {input, metadata}    -> run record (accepted)
           Idempotency-Key: <key>                                -> host-native dedupe
    GET  /api/threads/{tid}/messages                             -> message history
    GET  /api/threads/{tid}/runs/{run_id}                        -> run status

Auth: the gate runs the host with `DEER_FLOW_AUTH_DISABLED=1` (see E7 in
docs/pysdk-v2-sse-refactor-plan.md), so no credentials are involved. Studio/console
runs are out of scope here.

Stdlib only: this file is copied into host images whose system python has no
third-party packages.

CLI
---
  deerflow_drive.py probe                       # API reachable + thread/run accepted
  deerflow_drive.py turn  --thread T --text "…" [--idem KEY] [--wait SECS]
  deerflow_drive.py messages --thread T [--json]
  deerflow_drive.py status --thread T --run R

Exit codes: 0 = the requested operation succeeded; 1 = it did not (message on stderr);
2 = usage error. Never exits 0 on a partial result — the L2 gate treats 0 as "green".
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_URL = "http://127.0.0.1:8001"


def _call(url: str, path: str, body: dict | None = None, *, method: str | None = None,
          headers: dict | None = None, timeout: float = 60.0) -> tuple[int, object]:
    """One HTTP round. Returns (status, parsed-json-or-text). Never raises on HTTP status."""
    data = None
    hdrs = {"accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["content-type"] = "application/json"
    if headers:
        hdrs.update({k: v for k, v in headers.items() if v})
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method or ("POST" if body is not None else "GET"),
                                 headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode("utf-8", "replace")
    except Exception as e:  # transport failure: caller decides GAP vs RED
        return 0, f"{type(e).__name__}: {e}"


def new_thread_id(prefix: str = "aimail-l2") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def ensure_thread(url: str, thread_id: str, metadata: dict | None = None) -> tuple[bool, str]:
    """Idempotent thread create (the host returns the existing record for a known id)."""
    status, body = _call(url, "/api/threads", {"thread_id": thread_id, "metadata": metadata or {}})
    if status == 200 and isinstance(body, dict) and body.get("thread_id") == thread_id:
        return True, f"thread {thread_id} ready"
    return False, f"create thread {thread_id}: status={status} body={str(body)[:200]}"


def start_run(url: str, thread_id: str, text: str, *, idem: str = "",
              metadata: dict | None = None) -> tuple[bool, dict | str]:
    """POST one run. `accepted` == a run record with a run_id came back (2xx).

    This is the ack oracle for deer-flow: the host has taken the mail into a run, which
    is exactly the contract's "accepted into a session" bar. 409 counts as accepted when
    the body names an existing run (host-native idempotency already took it).
    """
    body = {
        "input": {"messages": [{"role": "user", "content": text}]},
        "metadata": metadata or {},
    }
    hdrs = {"Idempotency-Key": idem} if idem else {}
    status, resp = _call(url, f"/api/threads/{thread_id}/runs", body, headers=hdrs)
    if status in (200, 201) and isinstance(resp, dict) and resp.get("run_id"):
        return True, resp
    if status == 409 and isinstance(resp, dict) and resp.get("run_id"):
        return True, resp
    return False, f"start run: status={status} body={str(resp)[:300]}"


def get_run(url: str, thread_id: str, run_id: str) -> tuple[int, object]:
    return _call(url, f"/api/threads/{thread_id}/runs/{run_id}")


def get_messages(url: str, thread_id: str) -> tuple[int, object]:
    return _call(url, f"/api/threads/{thread_id}/messages")


def run_text(messages: object) -> str:
    """Flatten a /messages response into text (shape-tolerant: list or {messages:[…]})."""
    rows = messages.get("messages") if isinstance(messages, dict) else messages
    if not isinstance(rows, list):
        return ""
    out = []
    for m in rows:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or m.get("type") or ""
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
        out.append(f"{role}: {content}")
    return "\n".join(out)


def wait_message(url: str, thread_id: str, *, timeout: float, need: str = "",
                 poll: float = 2.0) -> tuple[bool, str]:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        status, body = get_messages(url, thread_id)
        if status == 200:
            last = run_text(body)
            if last and (not need or need in last):
                return True, last
        time.sleep(poll)
    return False, last


def cmd_probe(args: argparse.Namespace) -> int:
    url = args.url
    tid = new_thread_id("aimail-probe")
    status, body = _call(url, "/openapi.json", timeout=30)
    if status != 200:
        print(f"FAIL openapi.json status={status} ({str(body)[:120]})", file=sys.stderr)
        return 1
    print(f"ok   openapi.json 200")
    ok, why = ensure_thread(url, tid, {"probe": "thread"}, )
    print(("ok   " if ok else "FAIL ") + why)
    if not ok:
        return 1
    ok, resp = start_run(url, tid, "Reply with the single word: ok", idem=f"aimail-probe-{tid}")
    print(("ok   " if ok else "FAIL ") + (f"run {resp.get('run_id')} status={resp.get('status')}"
                                          if isinstance(resp, dict) else str(resp)))
    return 0 if ok else 1


def cmd_turn(args: argparse.Namespace) -> int:
    ok, why = ensure_thread(args.url, args.thread)
    if not ok:
        print(why, file=sys.stderr)
        return 1
    ok, resp = start_run(args.url, args.thread, args.text, idem=args.idem,
                         metadata=args.metadata or None)
    if not ok:
        print(str(resp), file=sys.stderr)
        return 1
    run_id = resp.get("run_id")
    print(f"run={run_id} status={resp.get('status')}")
    if args.wait <= 0:
        return 0
    found, text = wait_message(args.url, args.thread, timeout=args.wait, need=args.need)
    if text:
        print("── messages ──")
        print(text[-args.tail:] if args.tail else text)
    if not found:
        print(f"! no message matching {args.need!r} within {args.wait}s (run={run_id})", file=sys.stderr)
        return 1
    return 0


def cmd_messages(args: argparse.Namespace) -> int:
    status, body = get_messages(args.url, args.thread)
    if status != 200:
        print(f"status={status} {str(body)[:200]}", file=sys.stderr)
        return 1
    print(json.dumps(body, ensure_ascii=False, indent=2) if args.json else run_text(body))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    status, body = get_run(args.url, args.thread, args.run)
    print(json.dumps(body, ensure_ascii=False, indent=2) if isinstance(body, dict) else f"status={status} {body}")
    return 0 if status == 200 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drive a deer-flow Gateway through its HTTP API (L2 fixture).")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"gateway base URL (default {DEFAULT_URL})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="reachability + thread/run acceptance")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("turn", help="create thread (idempotent) and post one run")
    p.add_argument("--thread", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--idem", default="", help="Idempotency-Key (host-native run dedupe)")
    p.add_argument("--metadata", default="", help="JSON object attached to the run")
    p.add_argument("--wait", type=float, default=0.0, help="seconds to wait for an assistant message")
    p.add_argument("--need", default="", help="substring that must appear in the messages")
    p.add_argument("--tail", type=int, default=2000, help="print only the last N chars")
    p.set_defaults(fn=cmd_turn)

    p = sub.add_parser("messages", help="print the thread history")
    p.add_argument("--thread", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_messages)

    p = sub.add_parser("status", help="print one run record")
    p.add_argument("--thread", required=True)
    p.add_argument("--run", required=True)
    p.set_defaults(fn=cmd_status)

    args = ap.parse_args(argv)
    if getattr(args, "metadata", ""):
        try:
            args.metadata = json.loads(args.metadata)
        except ValueError:
            print("--metadata must be JSON", file=sys.stderr)
            return 2
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())

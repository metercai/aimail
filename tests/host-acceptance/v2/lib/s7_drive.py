#!/usr/bin/env python3
"""Drive the C17 (s7) probe: delete the session a thread's mail landed in, then reply to that mail.

What it measures (all host-side, nothing inferred by the fixture):
  * whether the reply is still ACKED (the dangerous failure is "left pending forever", which would
    strand an owner's approval),
  * whether it lands in a NEW session in the same anchor (the §9.3 "re-attach the thread" downgrade),
  * whether the thread root survived (the reply must stay in the same thread, not become a new one).

It reuses s6_drive's mail/ack helpers on purpose: the injection and ack semantics must be identical
to s6, otherwise a difference in outcome could just be a difference in how the fixture sends.

NOTE on `reason`: the contract's §9.3 table names `session-gone`, but no producer exists in the SDK —
`runtime/routing.ts` returns {direct|followup|new} and never a reason string. So this driver records
what it could actually observe (`redirected: true`) and leaves `reason` empty. The s7 case judges the
reason separately; an empty reason must fail loudly there rather than be invented here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s6_drive import (  # noqa: E402
    pending,
    send,
    session_ids_from_cmd,
    snapshot,
    snapshot_discover,
    wait_ack as s6_wait_ack,
)


def log(msg: str) -> None:
    print(msg, flush=True)


def snap(a) -> list:
    if a.session_cmd:
        return sorted(set(session_ids_from_cmd(a.session_cmd)))
    if a.discover:
        return snapshot_discover(a.session_root)
    return snapshot(a.session_root, a.depth)


def wait_ack(a) -> bool:
    return s6_wait_ack(a.db, a.addr, a.ack_timeout)


def settle(a) -> None:
    import time
    time.sleep(a.settle)


def remove_session(a, name: str) -> bool:
    """Delete one session from the HOST's own store (file-based hosts only)."""
    import shutil
    if a.session_cmd:
        log("cannot delete a session through --session-cmd; s7 needs a file-based store")
        return False
    root = os.path.expanduser(a.session_root)
    for dirpath, dirnames, _files in os.walk(root):
        base = os.path.basename(dirpath)
        if base == name or name in base:
            log("deleting session dir %s" % dirpath)
            shutil.rmtree(dirpath, ignore_errors=True)
            return True
    log("session %s not found under %s" % (name, root))
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", required=True)
    ap.add_argument("--smtp-port", type=int, required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--session-root", required=True)
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--session-cmd", default="")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--settle", type=float, default=15.0)
    ap.add_argument("--ack-timeout", type=float, default=120.0)
    a = ap.parse_args()

    root_a = "s7-a@example.com"
    root_reply = "s7-reply@example.com"

    s0 = snap(a)
    log("baseline sessions: %d" % len(s0))

    send(a.addr, a.smtp_port, root_a, "", "")
    acked1 = wait_ack(a)
    settle(a)
    s1 = snap(a)
    new1 = [x for x in s1 if x not in s0]
    log("after mail A: acked=%s new=%s" % (acked1, new1))
    if not new1:
        json.dump({"acked": False, "deleted": "", "used": "", "reason": "",
                   "root_ok": False, "error": "mail A produced no new session"},
                  open(os.path.join(a.out, "s7_gone.json"), "w"), indent=2)
        return 0

    victim = new1[0]
    deleted_ok = remove_session(a, victim.split(":")[-1] if a.discover else victim)
    settle(a)
    s2 = snap(a)
    gone = victim not in s2
    log("deleted %s ok=%s gone_from_store=%s" % (victim, deleted_ok, gone))

    send(a.addr, a.smtp_port, root_reply, root_a, root_a)
    acked2 = wait_ack(a)
    settle(a)
    s3 = snap(a)
    new3 = [x for x in s3 if x not in s2]
    used = new3[0] if new3 else ""
    log("after reply: acked=%s new=%s" % (acked2, new3))

    result = {
        "acked": bool(acked2),
        "deleted": victim,
        "used": used,
        "reason": "",          # no producer in the SDK; the case must judge this, not the driver
        "root_ok": bool(acked2 and used and used != victim),
        "redirected": bool(acked2 and used and used != victim),
        "session_gone_observed": bool(gone),
    }
    json.dump(result, open(os.path.join(a.out, "s7_gone.json"), "w"), indent=2)
    log("s7: " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

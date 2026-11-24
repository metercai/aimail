#!/usr/bin/env python3
"""Drive the C8 (s6) threading probe from INSIDE a host container.

Why a file and not an inline heredoc: the dsh/deerflow containers are started with
`bash -lc '<body>'`; a single quote anywhere in the body terminates the whole string (that mistake
cost one run). A bind-mounted script has no quoting problem and is testable on its own.

What it does (no host code touched):
  1. inject mail A (new thread)                      -> wait for the ledger to accept it
  2. inject a reply with In-Reply-To/References: A   -> wait
  3. inject mail B (new thread)                      -> wait
  4. after each step, snapshot the HOST's session store (directory names)
  5. write s6.json for cases/s6_threading.sh to judge

The assertion lives in the case, not here: this file only measures. If the session store cannot be
read the snapshots are empty and the case reports a FIXTURE GAP rather than a product verdict.
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import sqlite3
import sys
import time
from email.message import EmailMessage


def send(addr: str, port: int, mid: str, irt: str, refs: str) -> None:
    m = EmailMessage()
    m["From"] = "ext-sender@example.com"
    m["To"] = addr
    m["Subject"] = "C8 threading probe"
    m["Message-ID"] = mid
    if irt:
        m["In-Reply-To"] = irt
    if refs:
        m["References"] = refs
    m.set_content("threading probe " + mid)
    s = smtplib.SMTP("127.0.0.1", port, timeout=20)
    s.send_message(m)
    s.quit()
    print("s6_sent " + mid, flush=True)


def pending(db: str, addr: str) -> int:
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        row = c.execute("select count(*) from pending_deliveries where email=?", (addr,)).fetchone()
        c.close()
        return int(row[0] if row else 0)
    except Exception:
        return -1


def wait_ack(db: str, addr: str, timeout_s: float = 180.0) -> bool:
    """pending observed > 0 and back to 0 ⇒ the host took the mail."""
    deadline = time.monotonic() + timeout_s
    seen = 0
    while time.monotonic() < deadline:
        n = pending(db, addr)
        if n > seen:
            seen = n
        if seen > 0 and n == 0:
            return True
        time.sleep(0.2)
    return False


def snapshot(root: str, depth: int) -> list:
    """Directory names under the host's own session store (host-side identity, not our arithmetic)."""
    out = []
    root = os.path.expanduser(root)
    if not root or not os.path.isdir(root):
        return out
    for dirpath, dirnames, _files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        if rel == ".":
            continue
        if rel.count(os.sep) + 1 == depth:
            out.append(os.path.basename(dirpath))
            dirnames[:] = []
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", required=True)
    ap.add_argument("--smtp-port", type=int, required=True)
    ap.add_argument("--db", required=True, help="gateway ledger db, e.g. /gw/storage/aimail.db")
    ap.add_argument("--out", required=True)
    ap.add_argument("--session-root", required=True, help="host session directory to snapshot")
    ap.add_argument("--depth", type=int, default=2, help="how deep the session ids live")
    ap.add_argument("--settle", type=float, default=15.0, help="seconds to let the host materialise a session")
    a = ap.parse_args()

    steps = [("<s6-a@example.com>", "", ""),
             ("<s6-reply@example.com>", "<s6-a@example.com>", "<s6-a@example.com>"),
             ("<s6-b@example.com>", "", "")]
    acked, snaps = [], [snapshot(a.session_root, a.depth)]
    for mid, irt, refs in steps:
        try:
            send(a.addr, a.smtp_port, mid, irt, refs)
        except Exception as e:  # noqa: BLE001 — a failed send is a fixture fact, not a verdict
            print("s6_send_failed %s: %s" % (mid, e), flush=True)
            acked.append(False)
            snaps.append(snapshot(a.session_root, a.depth))
            continue
        acked.append(wait_ack(a.db, a.addr))
        time.sleep(a.settle)
        snaps.append(snapshot(a.session_root, a.depth))

    def new(before, after):
        return [x for x in after if x not in before]

    n1, n2, n3 = new(snaps[0], snaps[1]), new(snaps[1], snaps[2]), new(snaps[2], snaps[3])
    doc = {
        "acked": acked,
        "root1": "s6-a@example.com", "root2": "s6-a@example.com", "root3": "s6-b@example.com",
        "session1": n1[0] if n1 else "-",
        "session2": n2[0] if n2 else (n1[0] if n1 else "-"),
        "session3": n3[0] if n3 else "-",
        "new_on_reply": len(n2), "new_on_newthread": len(n3),
        "sessions1": len(snaps[1]), "sessions2": len(snaps[2]), "sessions3": len(snaps[3]),
        "workspace": "",
    }
    try:
        b = json.load(open(os.path.join(a.out, "binding.json")))
        doc["workspace"] = b.get("workspace_path") or ""
    except Exception:
        pass
    with open(os.path.join(a.out, "s6.json"), "w") as fh:
        json.dump(doc, fh, indent=2)
    print(json.dumps(doc), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

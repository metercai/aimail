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


def delivered(db: str, mid: str) -> bool:
    """Durable acceptance: an inbound row for this Message-ID that the gateway marked delivered.

    Needed because an active SSE pull can take the mail and ack it inside one poll interval:
    measured 2026-11-27, the gateway log showed 18 received / 18 pending insertions while a
    0.2s sampler never saw the transient, so every driver mail looked "not accepted" and S6/S7/S8
    went RED on hosts that had in fact done the work.
    """
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        row = c.execute(
            "select count(*) from emails where direction='inbound' "
            "and headers like ? and status='delivered'", ("%" + mid + "%",)).fetchone()
        c.close()
        return bool(row and row[0])
    except Exception:
        return False


def wait_ack(db: str, addr: str, mid: str = "", timeout_s: float = 180.0) -> bool:
    """The host took the mail: pending went 0 after being >0, or the durable proof says so."""
    deadline = time.monotonic() + timeout_s
    seen = 0
    while time.monotonic() < deadline:
        n = pending(db, addr)
        if n > seen:
            seen = n
        if seen > 0 and n == 0:
            return True
        # durable fallback: the mail is in, delivered, and nothing is left pending for the address
        if mid and delivered(db, mid) and n == 0:
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


def _sqlite_ids(path: str) -> list:
    """Identity of the host's SESSIONS, not of its transcript events.

    First attempt read every table whose name mentioned "session" and unioned the rows, so
    `session_transcript_active_events` (one row per event) dominated: counts went 43 → 59 → 87 and
    the run looked like a C8 violation while nothing had actually been created. Identity now comes
    from a session-id COLUMN, distinct — event/transcript tables contribute their session_id, which
    is the same identity the host uses.
    """
    out = []
    ID_COLS = ("session_id", "sessionId", "chat_id", "chatId", "thread_id", "threadId", "key")
    try:
        c = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=3)
        tables = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
        for t in tables:
            low = t.lower()
            if "session" not in low:
                continue
            try:
                cols = [r[1] for r in c.execute("pragma table_info(%s)" % t)]
            except Exception:
                continue
            col = next((c0 for c0 in ID_COLS if c0 in cols), None)
            if not col and "id" in cols and "event" not in low and "entry" not in low:
                col = "id"
            if not col:
                continue
            try:
                for (v,) in c.execute("select distinct %s from %s limit 500" % (col, t)):
                    if v is None:
                        continue
                    out.append("db:%s:%s" % (os.path.basename(path), v))
            except Exception:
                continue
        c.close()
    except Exception:
        return []
    return out


def session_ids_from_cmd(cmd: str) -> list:
    """Ask the host itself for its session list (authoritative), instead of interpreting its DB.

    openclaw exposes `openclaw sessions --json`, and reading its sqlite directly mixed several
    id-spaces (row ids like "3", chat ids, and 40-bit counters), which produced meaningless counts
    (22 → 25 → 32) that looked like C8 violations.
    """
    import subprocess
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    except Exception:
        return []
    raw = (r.stdout or "").strip()
    if not raw:
        return []
    try:
        doc = json.loads(raw)
    except Exception:
        return [l.strip() for l in raw.splitlines() if l.strip()]
    if isinstance(doc, dict):
        doc = doc.get("sessions") or doc.get("items") or doc.get("data") or []
    out = []
    for e in doc if isinstance(doc, list) else []:
        if isinstance(e, dict):
            for k in ("sessionId", "session_id", "id", "key", "chatId", "threadId"):
                if e.get(k):
                    out.append(str(e[k]))
                    break
            else:
                out.append(json.dumps(e, sort_keys=True))
        else:
            out.append(str(e))
    return out


def snapshot_discover(root: str) -> list:
    """Layout-agnostic snapshot: session-named directories plus session tables in any sqlite file.

    openclaw keeps transcripts in sqlite and does not document the path, so the fixture discovers
    instead of guessing (guessing cost hermes two runs).
    """
    out = []
    root = os.path.expanduser(root)
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > 5:
            dirnames[:] = []
            continue
        if depth > 0 and "session" in os.path.basename(dirpath).lower():
            out.append("dir:" + rel)
        for f in files:
            if f.endswith((".sqlite", ".sqlite3", ".db")):
                out.extend(_sqlite_ids(os.path.join(dirpath, f)))
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
    ap.add_argument("--session-cmd", default="",
                    help="command whose JSON output lists the host's sessions (authoritative)")
    ap.add_argument("--discover", action="store_true",
                    help="ignore --depth and discover session dirs / session tables under --session-root")
    a = ap.parse_args()

    steps = [("<s6-a@example.com>", "", ""),
             ("<s6-reply@example.com>", "<s6-a@example.com>", "<s6-a@example.com>"),
             ("<s6-b@example.com>", "", "")]
    def snap() -> list:
        if a.session_cmd:
            return sorted(set(session_ids_from_cmd(a.session_cmd)))
        if a.discover:
            return snapshot_discover(a.session_root)
        return snapshot(a.session_root, a.depth)

    acked, snaps = [], [snap()]
    for mid, irt, refs in steps:
        try:
            send(a.addr, a.smtp_port, mid, irt, refs)
        except Exception as e:  # noqa: BLE001 — a failed send is a fixture fact, not a verdict
            print("s6_send_failed %s: %s" % (mid, e), flush=True)
            acked.append(False)
            snaps.append(snap())
            continue
        acked.append(wait_ack(a.db, a.addr, mid))
        time.sleep(a.settle)
        snaps.append(snap())

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

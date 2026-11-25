#!/usr/bin/env python3
"""Drive the C16 (s8) probe: the thread's recorded session sits in ANOTHER anchor.

What it measures — all host-side, nothing inferred by the fixture:

  * **is the reply still acked?** A refusal without a fallback would strand an owner's mail.
  * **did the mail land in the CURRENT anchor** (a session the host created/used now), or was it
    pushed into the recorded session that belongs to a different anchor?
  * **is the abandonment visible?** The product records `anchor` = `anchor-moved` plus the edge and
    the foreign space on the dispatch line; a silent re-rank would be indistinguishable from "there
    was never a direct reply", which is exactly what §16 C16 is meant to forbid.

Why rewrite `workspace_path` instead of deleting the session: the recorded session must stay LIVE and
resumable, so the only thing that can stop the delivery is the anchor check itself. (Deleting it
would make the run prove C17 instead — `s7` already does that.)

Layout note: the meta index is located by its documented layout
`systems/<sid>/<addr>/mail/meta/<2>/<root>.json`; the driver reports how many files it rewrote and
writes that count into the evidence, so "0 rewritten" can never look like a pass.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s6_drive import send  # noqa: E402  (identical injection semantics as s6/s7)
from s7_drive import log, mtimes, settle, snap, wait_ack  # noqa: E402

FOREIGN_ANCHOR = "/work/foreign-anchor"


def point_thread_at_foreign_anchor(aimail_home: str, foreign: str) -> int:
    """Rewrite the thread pointer's space, keeping its session_id (that session stays alive)."""
    pattern = os.path.join(os.path.expanduser(aimail_home), "systems", "*", "*",
                           "mail", "meta", "*", "*.json")
    hits = 0
    files = glob.glob(pattern)
    log("thread-meta probe: %d file(s) matching %s" % (len(files), pattern))
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log("thread-meta unreadable: %s (%s)" % (f, e))
            continue
        if not d.get("session_id"):
            continue
        d["workspace_path"] = foreign
        try:
            json.dump(d, open(f, "w", encoding="utf-8"), indent=2)
            hits += 1
            log("thread-meta %s: session_id=%s workspace_path -> %s"
                % (os.path.basename(f), d["session_id"], foreign))
        except Exception as e:  # noqa: BLE001
            log("thread-meta rewrite failed: %s (%s)" % (f, e))
    return hits


def dispatch_evidence(aimail_home: str) -> dict:
    """Read what the product actually wrote. Never assume the marker exists."""
    out = {"anchor": "", "edge": "", "from": "", "reasons": [], "lines": 0}
    pattern = os.path.join(os.path.expanduser(aimail_home), "systems", "*", "*", "agentmail.log")
    for lf in glob.glob(pattern):
        try:
            blob = open(lf, encoding="utf-8", errors="replace").read()
        except Exception:  # noqa: BLE001
            continue
        for line in blob.strip().splitlines():
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if rec.get("event") != "dispatch":
                continue
            out["lines"] += 1
            if rec.get("reason"):
                out["reasons"].append(str(rec["reason"]))
            if rec.get("anchor"):
                out["anchor"] = str(rec["anchor"])
                out["edge"] = str(rec.get("anchorMovedEdge") or "")
                out["from"] = str(rec.get("anchorMovedFrom") or "")
    return out


def landed_session(session_root: str, before: dict) -> str:
    """Which store entry took the reply: the one written after we sent it (transcripts are compressed,
    so a Message-ID search is not available)."""
    touched = [fp for fp, mt in mtimes(session_root).items() if mt > before.get(fp, 0) + 0.5]
    for fp in sorted(touched):
        parts = fp.split(os.sep)
        for part in reversed(parts[:-1]):
            if part and part != "sessions" and len(part) > 8:
                return part
    return ""


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
    ap.add_argument("--session-glob", default="")
    ap.add_argument("--aimail-home", default=os.environ.get("AIMAIL_HOME", "/root/.aimail"))
    ap.add_argument("--foreign-anchor", default=FOREIGN_ANCHOR)
    ap.add_argument("--settle", type=float, default=15.0)
    ap.add_argument("--ack-timeout", type=float, default=120.0)
    a = ap.parse_args()

    root_a = "s8-a@example.com"
    root_reply = "s8-reply@example.com"
    evidence = os.path.join(a.out, "s8_anchor.json")

    def finish(**kw) -> int:
        json.dump(kw, open(evidence, "w"), indent=2)
        log("s8: " + json.dumps(kw, sort_keys=True))
        return 0

    s0 = snap(a)
    log("baseline sessions: %d" % len(s0))
    send(a.addr, a.smtp_port, root_a, "", "")
    acked1 = wait_ack(a)
    settle(a)
    s1 = snap(a)
    new1 = [x for x in s1 if x not in s0]
    log("after mail A: acked=%s new=%s" % (acked1, new1))
    if not new1:
        return finish(error="mail A produced no new session", acked=False, moved=False,
                      rewritten=0, foreign_session="", used="", landed="", leaks=False)

    victim = new1[0]
    rewritten = point_thread_at_foreign_anchor(a.aimail_home, a.foreign_anchor)
    if rewritten == 0:
        # No thread pointer ⇒ the scenario was never set up. Report it instead of judging a no-op.
        return finish(error="no thread pointer rewritten (mail/meta empty?) — C16 not exercised",
                      acked=False, moved=False, rewritten=0, foreign_session=victim,
                      used="", landed="", leaks=False)
    settle(a)

    before_reply = mtimes(a.session_root)
    send(a.addr, a.smtp_port, root_reply, root_a, root_a)
    acked2 = wait_ack(a)
    settle(a)
    s2 = snap(a)
    new2 = [x for x in s2 if x not in s1]
    landed = landed_session(a.session_root, before_reply)
    ev = dispatch_evidence(a.aimail_home)
    used = landed or (new2[0] if new2 else "")
    log("after reply: acked=%s new=%s landed=%r dispatch=%r" % (acked2, new2, landed, ev))

    return finish(
        acked=bool(acked2),
        moved=ev["anchor"] == "anchor-moved",
        anchor=ev["anchor"],
        edge=ev["edge"],
        moved_from=ev["from"],
        foreign_anchor=a.foreign_anchor,
        rewritten=rewritten,
        foreign_session=victim,
        used=used,
        landed=landed,
        # The defect C16 forbids: the mail ended up in the session of the OTHER anchor.
        leaks=bool(landed) and landed == victim,
        dispatch_lines=ev["lines"],
        reasons=ev["reasons"][-4:],
    )


if __name__ == "__main__":
    sys.exit(main())

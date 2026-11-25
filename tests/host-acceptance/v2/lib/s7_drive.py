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
import glob
import time
import json
import os
import re
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
    if a.session_glob:
        import glob as _g
        pat = os.path.join(os.path.expanduser(a.session_root), a.session_glob)
        return sorted(os.path.basename(p) for p in _g.glob(pat))
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


def mtimes(root: str) -> dict:
    """{file: mtime} over the host's session store — used to see WHICH session a reply touched."""
    out = {}
    for dirpath, _dirs, files in os.walk(os.path.expanduser(root)):
        for f in files:
            fp = os.path.join(dirpath, f)
            try:
                out[fp] = os.path.getmtime(fp)
            except OSError:
                pass
    return out


def remove_session(a, name: str) -> bool:
    """Delete one session from the HOST's own store.

    `--delete-cmd` wins when given: each host stores sessions differently (a dir per session, one JSON
    keyed by chat id, a sqlite row), and guessing a filesystem layout is how fixtures end up measuring
    the wrong thing.
    """
    import shutil
    import subprocess as _sp
    if a.delete_cmd:
        cmd = a.delete_cmd.replace("{id}", name)
        r = _sp.run(cmd, shell=True, capture_output=True, text=True)
        log("delete-cmd rc=%s %s" % (r.returncode, (r.stdout + r.stderr).strip()[:160]))
        return r.returncode == 0
    if a.session_cmd:
        log("cannot delete a session through --session-cmd; s7 needs a file-based store")
        return False
    root = os.path.expanduser(a.session_root)
    for dirpath, dirnames, _files in os.walk(root):
        base = os.path.basename(dirpath)
        if base == name or name in base:
            if os.path.isdir(dirpath):
                log("deleting session dir %s" % dirpath)
                shutil.rmtree(dirpath, ignore_errors=True)
            else:
                log("deleting session file %s" % dirpath)
                try:
                    os.remove(dirpath)
                except OSError as e:
                    log("delete failed: %s" % e)
                    return False
            return True
    log("session %s not found under %s" % (name, root))
    return False



def point_at_missing_session(aimail_home: str) -> str:
    """Rewrite every agentmail.json session_id to an id that never existed; returns the new id.

    Deleting the session directory is not enough on dsh: the registry still believes the session
    exists, so `agents.resume()` succeeds and the producer never fires. The host's own failure mode
    is a stale/unresumable id, which is what this produces.
    """
    new_id = "s7-nonexistent-%d" % int(time.time())
    hits = 0
    # Instrument before claiming "0 configs": the store layout is what it is, not what I assume it is.
    try:
        base = os.path.expanduser(aimail_home)
        log("aimail_home=%s exists=%s" % (base, os.path.isdir(base)))
        for probe, depth in ((os.path.join(base, "systems"), 1),):
            if os.path.isdir(probe):
                for name in sorted(os.listdir(probe))[:4]:
                    sub = os.path.join(probe, name)
                    log("  systems/%s -> %s" % (name, sorted(os.listdir(sub))[:6] if os.path.isdir(sub) else "file"))
        log("  glob hits=%d pattern=%s" % (len(glob.glob(os.path.join(base, "systems", "*", "*", "agentmail.json"))), os.path.join(base, "systems", "*", "*", "agentmail.json")))
    except Exception as e:
        log("layout probe failed: %s" % e)
    for cfg in glob.glob(os.path.expanduser(os.path.join(aimail_home, "systems", "*", "*", "agentmail.json"))):
        try:
            d = json.load(open(cfg, encoding="utf-8"))
        except Exception:
            continue
        if "session_id" in d and d.get("session_id"):
            d["session_id"] = new_id
            try:
                json.dump(d, open(cfg, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                hits += 1
            except Exception as e:
                log("rewrite %s failed: %s" % (cfg, e))
    if hits == 0:
        # Say WHY: absent field vs null vs nested key. Saves a 12-minute run to find out.
        for f in glob.glob(os.path.join(os.path.expanduser(a.aimail_home),
                                        "systems", "*", "*", "agentmail.json")):
            try:
                d = json.load(open(f, encoding="utf-8"))
                log("config keys (%s): %s" % (os.path.basename(os.path.dirname(f)), sorted(d.keys())))
            except Exception as e:
                log("config unreadable: %s (%s)" % (f, e))
    log("point_at_missing_session: rewrote %d config(s) -> %s" % (hits, new_id))
    return new_id if hits else ""


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
    ap.add_argument("--session-glob", default="",
                    help="glob (relative to --session-root) whose MATCHING FILES are the sessions; pi keeps "
                         "one .jsonl per session, so its ids are file basenames")
    ap.add_argument("--stale-session", action="store_true",
                    help="make the target session un-resumable the way the HOST would: rewrite the address "
                         "config's session_id to an id that never existed (dsh: resume then fails)")
    ap.add_argument("--aimail-home", default=os.environ.get("AIMAIL_HOME", "/root/.aimail"),
                    help="where the SDK writes systems/<sid>/<addr>/agentmail.log (for the session-gone reason)")
    ap.add_argument("--delete-cmd", default="",
                    help="shell command that deletes one session; {id} is substituted (default: remove the "
                         "matching file/dir under --session-root, which only works for file-based stores)")
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
        json.dump({"acked": False, "deleted": "", "used": "", "reason": reason,
                   "root_ok": False, "error": "mail A produced no new session"},
                  open(os.path.join(a.out, "s7_gone.json"), "w"), indent=2)
        return 0

    victim = new1[0]
    if a.stale_session:
        # dsh semantics: the session is "gone" when the host cannot resume it, not when the files vanish.
        stale = point_at_missing_session(a.aimail_home)
        deleted_ok = bool(stale)
        victim_target = stale or victim
    else:
        deleted_ok = remove_session(a, victim.split(":")[-1] if a.discover else victim)
        victim_target = victim
    settle(a)
    s2 = snap(a)
    gone = (victim_target not in s2) if not a.stale_session else bool(deleted_ok)
    log("deleted %s ok=%s gone_from_store=%s" % (victim, deleted_ok, gone))

    before_reply = mtimes(a.session_root)
    send(a.addr, a.smtp_port, root_reply, root_a, root_a)
    acked2 = wait_ack(a)
    settle(a)
    s3 = snap(a)
    new3 = [x for x in s3 if x not in s2]
    used = new3[0] if new3 else ""
    log("after reply: acked=%s new=%s" % (acked2, new3))

    # An ack is not proof of delivery: the gateway stops retrying once acked, so "acked but landed
    # nowhere" would silently lose an owner's approval. Session transcripts are compressed (zstd), so
    # searching for the Message-ID does not work; instead diff the session store by mtime — the session
    # that was written after the reply is the one that received it.
    landed = ""
    touched = [fp for fp, mt in mtimes(a.session_root).items() if mt > before_reply.get(fp, 0) + 0.5]
    for fp in sorted(touched):
        parts = fp.split(os.sep)
        for part in reversed(parts[:-1]):
            if part and part not in ("sessions",) and len(part) > 8:
                landed = part
                break
        if landed:
            break
    if landed:
        used = landed
    log("reply touched %d file(s); session inferred: %s" % (len(touched), landed or "NONE"))

    # The producer now exists (dsh: resume failure ⇒ status=session_gone_redirected, detail carries
    # "session-gone: <old-id> not resumable"; appendLog writes it to systems/<sid>/<addr>/agentmail.log).
    # Reading it here keeps the fixture measuring the product instead of asserting a constant.
    reason = ""
    try:
        import glob as _g
        for lf in _g.glob(os.path.expanduser(os.path.join(a.aimail_home, "systems", "*", "*", "agentmail.log"))):
            blob = open(lf, encoding="utf-8", errors="replace").read()
            hit = re.search(r"session-gone[^\"]{0,80}", blob)
            if hit:
                reason = "session-gone" if "session-gone" in hit.group(0) else ""
                break
    except Exception as e:
        log("reason lookup failed: %s" % e)
    log("reason=%r (from agentmail.log)" % reason)

    result = {
        "acked": bool(acked2),
        "deleted": victim,
        "used": used,
        "reason": "",          # no producer in the SDK; the case must judge this, not the driver
        "root_ok": bool(acked2 and used and used != victim),
        "redirected": bool(acked2 and used and used != victim),
        "landed_in_existing": bool(landed and landed in s2),
        "delivered_anywhere": bool(landed),
        "session_gone_observed": bool(gone),
    }
    json.dump(result, open(os.path.join(a.out, "s7_gone.json"), "w"), indent=2)
    log("s7: " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""List / delete threads in a sqlite-backed host store (deerflow: .deer-flow/data/deerflow.db).

Same reasoning as sessions_json.py: the host adapters execute their bodies inside `bash -lc '<body>'`,
so anything with quotes is a trap; a mounted helper has no quoting surface.

The schema is discovered, not assumed: any table whose name mentions "thread" and that has a
thread-ish id column is used. Listing prints a JSON array of {"sessionId": id} so the shared driver can
consume it; deleting removes the row from EVERY thread-ish table so a host cannot resurrect it from a
side table.
"""
from __future__ import annotations

import json
import sqlite3
import sys

ID_COLS = ("thread_id", "threadId", "id", "chat_id", "session_id")


def thread_tables(con) -> list:
    out = []
    for (name,) in con.execute("select name from sqlite_master where type='table'"):
        if "thread" not in name.lower():
            continue
        cols = [r[1] for r in con.execute("pragma table_info(%s)" % name)]
        col = next((c for c in ID_COLS if c in cols), None)
        if col:
            out.append((name, col))
    return out


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: threads_db.py list|delete <db> [id]", file=sys.stderr)
        return 2
    cmd, db = sys.argv[1], sys.argv[2]
    con = sqlite3.connect("file:%s?mode=rw" % db, uri=True)
    tables = thread_tables(con)
    if not tables:
        print("no thread table found in %s" % db, file=sys.stderr)
        return 3
    if cmd == "list":
        ids = set()
        for name, col in tables:
            for (v,) in con.execute("select distinct %s from %s" % (col, name)):
                if v:
                    ids.add(str(v))
        print(json.dumps([{"sessionId": i} for i in sorted(ids)]))
        return 0
    if cmd == "delete":
        if len(sys.argv) < 4:
            print("delete needs an id", file=sys.stderr)
            return 2
        target = sys.argv[3]
        removed = 0
        for name, col in tables:
            cur = con.execute("delete from %s where %s = ?" % (name, col), (target,))
            removed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        con.commit()
        print("deleted %s from %d row(s)" % (target, removed) if removed else "id %s not found" % target)
        return 0 if removed else 4
    print("unknown command %s" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

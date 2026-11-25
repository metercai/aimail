#!/usr/bin/env python3
"""List / delete sessions in a JSON session store (hermes: $HERMES_HOME/sessions/sessions.json).

Why a helper instead of an inline command: the host adapters run their bodies through
`docker exec ... bash -lc '<body>'`, i.e. inside a SINGLE-QUOTED string, where any apostrophe or nested
quote silently breaks the whole stage (paid for twice already). A mounted script has no quoting surface.

Schema tolerance: the store is a dict keyed by chat id in the version we gate, but has also been seen as
{"sessions": {...}}. Both are handled; anything else exits non-zero so the caller records a fixture gap
instead of inventing a session list.
"""
from __future__ import annotations

import json
import sys


def load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def keys_of(doc) -> list:
    if isinstance(doc, dict):
        inner = doc.get("sessions")
        if isinstance(inner, dict):
            return list(inner.keys())
        if isinstance(inner, list):
            return [e.get("sessionId") or e.get("id") or "" for e in inner if isinstance(e, dict)]
        return [k for k in doc.keys() if k != "_README"]
    if isinstance(doc, list):
        return [e.get("sessionId") or e.get("id") or "" for e in doc if isinstance(e, dict)]
    raise SystemExit("unsupported session store shape: %s" % type(doc).__name__)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: sessions_json.py list|delete <store.json> [id]", file=sys.stderr)
        return 2
    cmd, path = sys.argv[1], sys.argv[2]
    doc = load(path)
    if cmd == "list":
        print(json.dumps([{"sessionId": k} for k in keys_of(doc) if k]))
        return 0
    if cmd == "delete":
        if len(sys.argv) < 4:
            print("delete needs an id", file=sys.stderr)
            return 2
        target = sys.argv[3]
        if isinstance(doc, dict) and isinstance(doc.get("sessions"), (dict, list)):
            inner = doc["sessions"]
            if isinstance(inner, dict):
                inner.pop(target, None)
            else:
                doc["sessions"] = [e for e in inner
                                   if (e.get("sessionId") or e.get("id")) != target]
        elif isinstance(doc, dict):
            doc.pop(target, None)
        elif isinstance(doc, list):
            doc = [e for e in doc if (e.get("sessionId") or e.get("id")) != target]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        print("deleted %s" % target)
        return 0
    print("unknown command %s" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

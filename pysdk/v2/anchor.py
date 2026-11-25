"""anchor — the ANCHOR IDENTIFIER that travels to the gateway.

The anchor itself is an agent-side concept (dsh: workspace path, pi: cwd, openclaw: agent
workspace), so the gateway never learns it: what travels is ``sha256(lower(email) + NUL + anchor)``.
The client registers that token at activation (``anchor_id``) and presents it on every pull
(``X-Aimail-Anchor``); the gateway compares it with what it stored, so re-pointing a mailbox locally
produces a different token and the pull is refused before any mail is handed out.

There is deliberately NOTHING local beyond the hash: an earlier revision also kept an ``anchor.json``
ledger next to the binding and judged it at arming. That was dropped (2026-11-25) — both files live in
the user's home, so a user editing both consistently defeats it, and the gateway's witness makes it
redundant. The gateway is the only party that can say "this address is bound to that anchor".

A new host type needs no gateway change: only its own anchor string.
"""
from __future__ import annotations

# ── the gateway-facing identifier ────────────────────────────────────────────
#
# The anchor is an AGENT-side concept (dsh: workspace path, pi: cwd, openclaw: agent workspace), so
# the gateway never learns it. What travels is an opaque token: sha256(lower(email) + NUL + anchor).
# It is registered at activation and presented on every pull; the gateway compares it with what it
# stored, so re-pointing a mailbox locally produces a different token and the pull is refused before
# any mail is handed out. A new host type needs no gateway change — only its own anchor string.
#
# The NUL separator is deliberate: without it ("ab" + "c") and ("a" + "bc") would collide.

ANCHOR_HEADER = "X-Aimail-Anchor"


def anchor_id(email: str, anchor: str) -> str:
    """The anchor identifier the gateway stores and re-checks (empty when either side is empty)."""
    import hashlib
    addr = str(email or "").strip().lower()
    anch = str(anchor or "").strip()
    if not addr or not anch:
        return ""
    return hashlib.sha256((addr + "\u0000" + anch).encode("utf-8")).hexdigest()


def anchor_header(bindings) -> str:
    """The ``X-Aimail-Anchor`` value for a set of bindings: ``email;hash`` pairs, comma separated."""
    parts, seen = [], set()
    for b in bindings or []:
        email = str((b or {}).get("email") or "").strip().lower()
        if not email or email in seen:
            continue
        ident = anchor_id(email, str((b or {}).get("workspace_path") or ""))
        if not ident:
            continue
        seen.add(email)
        parts.append("%s;%s" % (email, ident))
    return ",".join(parts)

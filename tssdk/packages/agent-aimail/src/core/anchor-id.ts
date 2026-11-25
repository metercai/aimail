/**
 * anchor-id — the ANCHOR IDENTIFIER that travels to the gateway.
 *
 * The anchor itself is an agent-side concept (dsh: workspace path, pi: cwd, openclaw: agent
 * workspace), so the gateway never learns it: what travels is `sha256(lower(email) + NUL + anchor)`.
 * The client registers that token at activation (`anchor_id`) and presents it on every pull
 * (`X-Aimail-Anchor`); the gateway compares it with what it stored, so re-pointing a mailbox locally
 * produces a different token and the pull is refused before any mail is handed out.
 *
 * There is deliberately NOTHING local here beyond the hash: an earlier revision also kept an
 * `anchor.json` ledger next to the binding and judged it at arming. That was dropped (2026-11-25) —
 * both files live in the user's home, so a user who edits both consistently defeats it, and the
 * gateway's witness makes it redundant. The gateway is the only party that can say "this address is
 * bound to that anchor".
 *
 * A new host type needs no gateway change: only its own anchor string.
 */
import { sha256Hex } from './api-signature.js'
import type { AgentConfig } from './types.js'

/** Header carrying the anchor claims on every aggregate pull (SSE / long-poll / ack). */
export const ANCHOR_HEADER = 'X-Aimail-Anchor'

// ── the gateway-facing identifier ────────────────────────────────────────────
//
// The anchor is an AGENT-side concept (dsh: workspace path, pi: cwd, openclaw: agent workspace), so
// the gateway never learns it. What travels is an opaque token: sha256(lowercase(email) + NUL +
// anchor). The client registers it at activation and presents it on every pull; the gateway compares
// that token with the one it stored (see the gateway's `enforce_anchor_binding`). Re-pointing a
// mailbox locally therefore produces a different token and the pull is refused — before any mail is
// handed out. A new host type needs no gateway change: only its own anchor string.
//
// The NUL separator is deliberate: without it ("ab" + "c") and ("a" + "bc") would collide.

/** The anchor identifier the gateway stores and re-checks. Empty anchor ⇒ empty id (nothing to claim). */
export function anchorId(email: string, anchor: string): string {
  const addr = String(email ?? '').trim().toLowerCase()
  const anch = String(anchor ?? '').trim()
  if (!addr || !anch) return ''
  return sha256Hex(`${addr}\u0000${anch}`)
}

/** The `X-Aimail-Anchor` value for a set of bindings: `email;hash` pairs, comma separated. */
export function anchorHeader(bindings: readonly Pick<AgentConfig, 'email' | 'workspace_path'>[]): string {
  const parts: string[] = []
  const seen = new Set<string>()
  for (const b of bindings) {
    const email = String(b.email ?? '').trim().toLowerCase()
    if (!email || seen.has(email)) continue
    const id = anchorId(email, String(b.workspace_path ?? ''))
    if (!id) continue
    seen.add(email)
    parts.push(`${email};${id}`)
  }
  return parts.join(',')
}

/**
 * Pure, host-neutral thread/session routing rules used by every SSE adapter.
 * Keeping this file free of host APIs makes the routing contract testable and
 * prevents workspace/session semantics from drifting per host.
 */
export interface RoutingPayload {
  message_id?: string
  references?: unknown
  in_reply_to?: string
  headers?: Record<string, unknown>
}

/** Thread root rule: references[0] → in_reply_to → message_id. */
export function threadRoot(payload: RoutingPayload, headers: Record<string, unknown> = {}): string {
  const refs = payload.references
  const first = Array.isArray(refs) ? refs[0] : typeof refs === 'string' ? refs.split(/\s+/)[0] : ''
  const inReplyTo = String(payload.in_reply_to ?? headers['in-reply-to'] ?? '')
  return String(first || inReplyTo || payload.message_id || '').trim()
}
// ── routing lives in ONE place ───────────────────────────────────────────────
//
// `routeThread()` used to sit here: a thread-level-only rule with a workspace check. dsh's shipped
// hub used it while the fixtures exercised `decideInboundDelivery()` (E1/E2/E3 + C16 + C17), so the
// shipped path had no direct-reply edge and no observable reason. It was deleted on 2026-11-25 and
// every adapter now calls `decideInboundDelivery()` (./routing.ts) — one decision, one place.
//
// Contract §9. A reply whose immediate parent is an OUTBOUND mail we minted must
// go back to the session that minted it — that is the one hard routing rule, so it
// is decided here, purely, and never by string heuristics.
//
// The rule is deliberately strict about WHICH id is the parent:
//   In-Reply-To  = the immediate parent (what this mail answers)
//   References[] = the thread chain; [0] is the THREAD ROOT, the LAST entry is the parent
// Using references[0] here would be wrong: it is always the root, so any reply inside
// a thread we started would look like a direct reply to the first session.

/** Message-IDs travel with angle brackets and stray whitespace; compare them bare. */
export function normalizeMessageId(value: unknown): string {
  return String(value ?? '').trim().replace(/^</, '').replace(/>$/, '').trim()
}

/** The References chain, whichever shape the host hands us. */
export function referencesList(
  payload: RoutingPayload,
  headers: Record<string, unknown> = {},
): string[] {
  const raw = payload.references ?? headers['references'] ?? headers['References']
  const parts = Array.isArray(raw)
    ? raw.map((r) => String(r))
    : typeof raw === 'string'
      ? raw.split(/\s+/)
      : []
  return parts.map(normalizeMessageId).filter((r) => r.length > 0)
}

/**
 * The immediate parent of an inbound mail: `In-Reply-To`, else the LAST reference.
 *
 * `references[0]` is intentionally never used — it is the thread root, not the parent.
 */
export function parentMessageId(
  payload: RoutingPayload,
  headers: Record<string, unknown> = {},
): string {
  const inReplyTo = normalizeMessageId(payload.in_reply_to ?? headers['in-reply-to'] ?? headers['In-Reply-To'])
  if (inReplyTo) return inReplyTo
  const refs = referencesList(payload, headers)
  return refs.length > 0 ? refs[refs.length - 1] : ''
}

/** Resolves an outbound Message-ID to the session that sent it (host-supplied index). */
export type OutboundSessionLookup = (messageId: string) => string | undefined

/**
 * Direct-reply decision.
 *
 * Returns the session that minted the mail being answered, or `null` when this is
 * not a direct reply (⇒ the caller routes by thread instead).
 *
 * Deliberately does NOT fall back to the reference chain when `In-Reply-To` is
 * present but unknown: an unknown parent means the mail answers something we did
 * not send, which is a thread-level fact, not a direct reply. Falling back there
 * would silently turn "reply to somebody else's mail in our thread" into a hard
 * route to a possibly unrelated session.
 */
export function directReplySession(
  payload: RoutingPayload,
  headers: Record<string, unknown> = {},
  lookup: OutboundSessionLookup,
): { sessionId: string; parent: string } | null {
  const declared = normalizeMessageId(payload.in_reply_to ?? headers['in-reply-to'] ?? headers['In-Reply-To'])
  const parent = declared || (referencesList(payload, headers).slice(-1)[0] ?? '')
  if (!parent) return null
  // Only the immediate parent may grant a direct reply; when In-Reply-To was
  // declared, the last reference is NOT a substitute for it.
  const sessionId = String(lookup(parent) ?? '').trim()
  return sessionId ? { sessionId, parent } : null
}

/**
 * Pure, host-neutral thread/session routing rules used by every SSE adapter.
 * Keeping this file free of host APIs makes the routing contract testable and
 * prevents workspace/session semantics from drifting per host.
 */
import type { DeliveryTarget, ThreadMetaLike, WorkspaceRef } from './types.js'

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

/**
 * Decide whether an inbound mail follows up an existing session or starts a
 * disposable session in the binding workspace.
 */
export function routeThread(
  payload: RoutingPayload,
  workspace: WorkspaceRef,
  meta: ThreadMetaLike | undefined,
  headers: Record<string, unknown> = {},
): DeliveryTarget {
  const root = threadRoot(payload, headers)
  const sessionId = String(meta?.session_id ?? '').trim()
  const metaWorkspace = String(meta?.workspace_path ?? '').trim()
  if (root && sessionId && (!metaWorkspace || metaWorkspace === workspace.path)) {
    return { kind: 'followup', sessionId, workspace }
  }
  return { kind: 'new', workspace }
}

// ── direct reply: "the mail I am answering was sent by one of our sessions" ──
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

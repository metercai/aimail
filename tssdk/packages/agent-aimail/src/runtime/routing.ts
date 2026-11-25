/**
 * Inbound routing for the SSE adapters (contract §9).
 *
 * Three outcomes, in this order — and the order IS the contract:
 *
 *   1. **direct**   the mail answers an outbound mail WE sent ⇒ that exact session.
 *                   Hard rule: never downgraded, never replaced by a thread guess.
 *   2. **followup** not a direct reply, but the thread already has a session in the
 *                   same space ⇒ reuse it (a thread may span sessions).
 *   3. **new**      nothing known ⇒ a new session in the bound space.
 *
 * Host specifics stay out of this file: the caller supplies the bound space, and the
 * only I/O is reading the local meta index. That index is written on BOTH sides by
 * shared code: inbound delivery stamps thread ownership, `core/tools.ts` stamps the
 * sending session on every outbound mail (`direction: 'outbound'`).
 */
import type { WorkspaceRef } from './types.js'
import { readLocalMeta } from '../core/meta.js'
import { normalizeMessageId, parentMessageId, threadRoot, type RoutingPayload } from './session-router.js'
/** One abandoned mapping edge because the recorded session lives in ANOTHER anchor (§16 C16). */
export interface AnchorMove {
  /** The session that was refused because of its space. */
  sessionId: string
  /** That session's recorded space — the anchor it belongs to, not ours. */
  space: string
  /** Which edge the mail would have taken: a direct reply (E1) or the thread (E2). */
  edge: 'direct' | 'thread'
}

export type InboundRoute =
  | { kind: 'direct'; sessionId: string; parent: string; workspace: WorkspaceRef; anchorMoved?: AnchorMove }
  | { kind: 'followup'; sessionId: string; workspace: WorkspaceRef; root: string; anchorMoved?: AnchorMove }
  | { kind: 'new'; workspace: WorkspaceRef; root: string; anchorMoved?: AnchorMove }
  /**
   * A recorded session that no longer exists (§9.3 `session-gone`). The caller MUST allocate a
   * fresh session in `workspace`, re-point the thread, deliver, and only then ack — never deliver
   * into `previousSessionId` and never leave the mail pending forever.
   */
  | { kind: 'gone'; previousSessionId: string; parent?: string; workspace: WorkspaceRef; root: string; anchorMoved?: AnchorMove }

export interface InboundRoutingInput {
  /** Our own address (the binding's) — the meta index is per address. */
  email: string
  payload: RoutingPayload
  headers?: Record<string, unknown>
  workspace: WorkspaceRef
  /**
   * Does this session still exist on the host?
   *
   * Recorded ids are *historical*: a user can delete or archive a session while its meta record
   * survives. Without this check a delivery is dispatched into a void, the host still answers
   * "accepted", and the pull loop acks — measured as "acked, zero landing" (C17). Hosts that can
   * answer (a session registry/liveness probe) MUST supply it; a host that cannot leaves it out,
   * and this layer then keeps the old behaviour (the caller is responsible — see §17 obligations).
   */
  sessionExists?: (sessionId: string) => Promise<boolean>
}

/**
 * Session that sent `messageId`, but only when that mail really was OUTBOUND — plus the space that
 * session belonged to at send time (empty = the host recorded none, so the anchor cannot be judged).
 *
 * Inbound mail is also stamped with a session (thread ownership). Treating such a
 * record as "we sent it" would route a reply to somebody else's mail straight back
 * into a session as if it were a direct reply to us.
 */
async function outboundSessionOf(
  email: string,
  messageId: string,
): Promise<{ sessionId: string; spacePath: string } | undefined> {
  if (!messageId) return undefined
  const meta = await readLocalMeta(email, messageId)
  if (!meta) return undefined
  if (String(meta.direction ?? '') !== 'outbound') return undefined
  const sessionId = String(meta.session_id ?? '').trim()
  if (!sessionId) return undefined
  return { sessionId, spacePath: String(meta.workspace_path ?? '').trim() }
}

/** Thread-level session recorded for `root` (any direction), plus its space. */
async function threadSessionOf(
  email: string,
  root: string,
): Promise<{ sessionId: string; spacePath: string } | undefined> {
  if (!root) return undefined
  const meta = await readLocalMeta(email, root)
  if (!meta) return undefined
  const sessionId = String(meta.session_id ?? '').trim()
  if (!sessionId) return undefined
  return { sessionId, spacePath: String(meta.workspace_path ?? '').trim() }
}

/** Existence probe: absent probe ⇒ assume alive (legacy behaviour, documented above). */
async function alive(input: InboundRoutingInput, sessionId: string): Promise<boolean> {
  if (!input.sessionExists) return true
  try {
    return await input.sessionExists(sessionId)
  } catch {
    // A probe that throws is not evidence of absence: keep the historical route and let the
    // caller's own delivery step report the failure (a false "gone" would fork a live thread).
    return true
  }
}

/** Decide where one inbound delivery goes. */
export async function decideInboundDelivery(input: InboundRoutingInput): Promise<InboundRoute> {
  const headers = input.headers ?? {}
  // Normalized once here: `threadRoot` may keep "<id>" from the References header, and a
  // route carrying brackets sometimes and bare ids other times is a footgun for callers.
  const root = normalizeMessageId(threadRoot(input.payload, headers))

  // C16 / §9.2 I2 (no cross-anchor delivery) outranks I1 (a direct reply stays in its session).
  // A recorded session whose space is a DIFFERENT anchor must never receive this mail: abandon that
  // edge, re-rank inside the CURRENT anchor, and carry the abandonment out so the caller can record
  // it (§9.3 `anchor-moved`). Without this check E1 was anchor-blind — a reply to a mail sent from a
  // session in another workspace was delivered straight into that foreign session.
  const here = String(input.workspace.path ?? '').trim()
  const foreign = (
    spacePath: string,
    sessionId: string,
    edge: AnchorMove['edge'],
  ): AnchorMove | undefined =>
    spacePath && here && spacePath !== here ? { sessionId, space: spacePath, edge } : undefined
  let anchorMoved: AnchorMove | undefined

  // 1) direct reply: only the immediate parent, only when it is OUR outbound mail, only in our anchor.
  const parent = parentMessageId(input.payload, headers)
  if (parent) {
    const sent = await outboundSessionOf(input.email, parent)
    if (sent) {
      anchorMoved = foreign(sent.spacePath, sent.sessionId, 'direct')
      if (!anchorMoved) {
        if (await alive(input, sent.sessionId)) {
          return { kind: 'direct', sessionId: sent.sessionId, parent, workspace: input.workspace }
        }
        return { kind: 'gone', previousSessionId: sent.sessionId, parent, workspace: input.workspace, root }
      }
    }
  }

  // 2) thread-level reuse, but only inside the same space.
  const thread = await threadSessionOf(input.email, root)
  if (thread) {
    const movedThread = foreign(thread.spacePath, thread.sessionId, 'thread')
    if (movedThread) {
      anchorMoved = anchorMoved ?? movedThread
    } else {
      if (await alive(input, thread.sessionId)) {
        return { kind: 'followup', sessionId: thread.sessionId, workspace: input.workspace, root, ...(anchorMoved ? { anchorMoved } : {}) }
      }
      return { kind: 'gone', previousSessionId: thread.sessionId, workspace: input.workspace, root, ...(anchorMoved ? { anchorMoved } : {}) }
    }
  }

  // 3) new session in the bound space.
  return { kind: 'new', workspace: input.workspace, root, ...(anchorMoved ? { anchorMoved } : {}) }
}


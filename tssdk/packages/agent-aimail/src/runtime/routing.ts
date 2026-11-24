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

export type InboundRoute =
  | { kind: 'direct'; sessionId: string; parent: string; workspace: WorkspaceRef }
  | { kind: 'followup'; sessionId: string; workspace: WorkspaceRef; root: string }
  | { kind: 'new'; workspace: WorkspaceRef; root: string }

export interface InboundRoutingInput {
  /** Our own address (the binding's) — the meta index is per address. */
  email: string
  payload: RoutingPayload
  headers?: Record<string, unknown>
  workspace: WorkspaceRef
}

/**
 * Session that sent `messageId`, but only when that mail really was OUTBOUND.
 *
 * Inbound mail is also stamped with a session (thread ownership). Treating such a
 * record as "we sent it" would route a reply to somebody else's mail straight back
 * into a session as if it were a direct reply to us.
 */
async function outboundSessionOf(email: string, messageId: string): Promise<string | undefined> {
  if (!messageId) return undefined
  const meta = await readLocalMeta(email, messageId)
  if (!meta) return undefined
  if (String(meta.direction ?? '') !== 'outbound') return undefined
  return String(meta.session_id ?? '').trim() || undefined
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

/** Decide where one inbound delivery goes. */
export async function decideInboundDelivery(input: InboundRoutingInput): Promise<InboundRoute> {
  const headers = input.headers ?? {}
  // Normalized once here: `threadRoot` may keep "<id>" from the References header, and a
  // route carrying brackets sometimes and bare ids other times is a footgun for callers.
  const root = normalizeMessageId(threadRoot(input.payload, headers))

  // 1) direct reply: only the immediate parent, only when it is OUR outbound mail.
  const parent = parentMessageId(input.payload, headers)
  if (parent) {
    const sessionId = await outboundSessionOf(input.email, parent)
    if (sessionId) {
      return { kind: 'direct', sessionId, parent, workspace: input.workspace }
    }
  }

  // 2) thread-level reuse, but only inside the same space.
  const thread = await threadSessionOf(input.email, root)
  if (thread && (!thread.spacePath || thread.spacePath === input.workspace.path)) {
    return { kind: 'followup', sessionId: thread.sessionId, workspace: input.workspace, root }
  }

  // 3) new session in the bound space.
  return { kind: 'new', workspace: input.workspace, root }
}


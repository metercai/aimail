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

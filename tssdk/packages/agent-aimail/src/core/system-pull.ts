/**
 * One system-scoped SSE stream shared by all addresses of that system.
 *
 * This is the host-neutral transport primitive behind the SSE planner: the
 * system/domain key observes every binding in its scope, and this loop hands
 * each delivery to the caller for recipient routing. `startPolling` acks only
 * after the callback resolves, so a routing/delivery failure stays pending.
 */
import { GatewayClient } from './gateway.js'
import {
  startPolling,
  AUTH_FATAL_STREAK,
  type PullDeliveryResult,
  type RequestClient,
} from './address-code.js'
import { PULL_HTTP_TIMEOUT_MS, type PullMode } from './pull-contract.js'

export interface SystemPullMail {
  id: number
  email: string
  headers: Record<string, unknown>
  body: unknown
}

export interface SystemPullOptions {
  systemId: string
  /** Aggregate by local AgentSystemId instead of one system/address. */
  agentSystemId?: string
  gatewayUrl: string
  apiKey: string
  onEmail: (mail: SystemPullMail) => void | Promise<void | PullDeliveryResult>
  log?: (line: string) => void
  client?: RequestClient
  signal?: AbortSignal
  /**
   * Pull cadence (M3: the tier-2 / grouping callers wire their resolved
   * settings here so the stream respects AIMAIL_PULL interval/limit/mode —
   * previously the stream fell to startPolling defaults regardless).
   */
  intervalMs?: number
  limit?: number
  mode?: PullMode
  /**
   * Per-strike auth rejection (401/403) visibility (P1-B §5.2.4): the first
   * strike logs a group-failure line so a dead key is observable immediately,
   * not only when the loop goes fatal at AUTH_FATAL_STREAK.
   */
  onAuthFailure?: (status: number, streak: number) => void
}

export interface SystemPullHandle {
  identity: string
  stop: () => void
  done: Promise<unknown>
}

export async function startSystemPull(opts: SystemPullOptions): Promise<SystemPullHandle> {
  const client = opts.client
    ?? (opts.agentSystemId
      ? new GatewayClient(opts.gatewayUrl, opts.apiKey, PULL_HTTP_TIMEOUT_MS, opts.systemId, {
          streamPath: '/api/v1/agent-system/pending/stream',
          pendingPath: '/api/v1/agent-system/pending',
          ackPath: '/api/v1/agent-system/pending/ack',
          extraHeaders: { 'X-Agent-System-Id': opts.agentSystemId },
        })
      : new GatewayClient(opts.gatewayUrl, opts.apiKey, PULL_HTTP_TIMEOUT_MS, opts.systemId))
  const controller = new AbortController()
  const signal = opts.signal
  const onAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', onAbort, { once: true })
  }
  const done = startPolling(
    client,
    async (mail) => {
      // RETURN the host's typed result (P1-A): a skip must reach
      // deliverBatches or the mail would be acked despite the decline.
      return opts.onEmail({
        id: mail.id,
        email: mail.email,
        headers: (mail.headers ?? {}) as Record<string, unknown>,
        body: mail.body,
      })
    },
    {
      signal: controller.signal,
      ...(opts.intervalMs !== undefined ? { intervalMs: opts.intervalMs } : {}),
      ...(opts.limit !== undefined ? { limit: opts.limit } : {}),
      ...(opts.mode !== undefined ? { mode: opts.mode } : {}),
      ...(opts.onAuthFailure !== undefined
        ? {
            onAuthFailure: (status: number, streak: number) => {
              opts.log?.(
                `[aimail-pull] system ${opts.systemId}: auth rejection (${status}) ` +
                  `strike ${streak} of ${AUTH_FATAL_STREAK}`,
              )
              opts.onAuthFailure!(status, streak)
            },
          }
        : {}),
    },
  )
    .catch((e: unknown) => {
      // Settle done with the error value (P1-B): an auth fatal (PullAuthError)
      // or any loop failure must be observable via `await handle.done`, not
      // silently swallowed into an undefined resolution.
      opts.log?.(`[aimail-pull] system ${opts.systemId}: ${e instanceof Error ? e.message : String(e)}`)
      return e as unknown
    })
    .finally(() => signal?.removeEventListener('abort', onAbort))
  return { identity: opts.systemId, stop: () => controller.abort(), done }
}

/**
 * One system-scoped SSE stream shared by all addresses of that system.
 *
 * This is the host-neutral transport primitive behind the SSE planner: the
 * system/domain key observes every binding in its scope, and this loop hands
 * each delivery to the caller for recipient routing. `startPolling` acks only
 * after the callback resolves, so a routing/delivery failure stays pending.
 */
import { GatewayClient } from './gateway.js'
import { startPolling, type RequestClient } from './address-code.js'
import { PULL_HTTP_TIMEOUT_MS } from './pull-contract.js'

export interface SystemPullMail {
  id: number
  email: string
  headers: Record<string, unknown>
  body: unknown
}

export interface SystemPullOptions {
  systemId: string
  gatewayUrl: string
  apiKey: string
  onEmail: (mail: SystemPullMail) => void | Promise<void>
  log?: (line: string) => void
  client?: RequestClient
  signal?: AbortSignal
}

export interface SystemPullHandle {
  identity: string
  stop: () => void
  done: Promise<unknown>
}

export async function startSystemPull(opts: SystemPullOptions): Promise<SystemPullHandle> {
  const client = opts.client
    ?? new GatewayClient(opts.gatewayUrl, opts.apiKey, PULL_HTTP_TIMEOUT_MS, opts.systemId)
  const controller = new AbortController()
  const signal = opts.signal
  const onAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', onAbort, { once: true })
  }
  const done = startPolling(client, async (mail) => {
    await opts.onEmail({
      id: mail.id,
      email: mail.email,
      headers: (mail.headers ?? {}) as Record<string, unknown>,
      body: mail.body,
    })
  }, { signal: controller.signal })
    .catch((e: unknown) => {
      opts.log?.(`[aimail-pull] system ${opts.systemId}: ${e instanceof Error ? e.message : String(e)}`)
    })
    .finally(() => signal?.removeEventListener('abort', onAbort))
  return { identity: opts.systemId, stop: () => controller.abort(), done }
}

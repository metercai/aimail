/**
 * AIMail mailbox as a PI-Desktop plugin (plan §4 `./desktop` face).
 *
 * The host runs this file in the plugin process and gives it one global: `pi`.
 * Everything below is wiring; the routing rules live in `agent-aimail/desktop`
 * so they are testable without an Electron app.
 *
 * Two host facts shape the file:
 *
 * 1. `pi.net.fetch` answers one whole response, so the plugin cannot hold an SSE
 *    body. It pulls with `mode: 'poll'` through a `GatewayClient` whose
 *    transport is the broker — not Node's `fetch`, which the sandbox does not
 *    put in reach of the permission model.
 * 2. A mail is acknowledged only after the delivery resolved. `startSystemPull`
 *    acks after the callback returns, so every refusal here throws instead of
 *    resolving, and the mail stays pending for the next round.
 */
import { GatewayClient, startSystemPull, type SystemPullHandle } from 'agent-aimail/core'
import type { GatewayFetchImpl } from 'agent-aimail/core'
import {
  createDesktopDelivery,
  createDesktopInbox,
  resolveDesktopConfig,
  type DesktopInbox,
  type DesktopInboxSettings,
  type DesktopSessionItem,
} from 'agent-aimail/desktop'

/** Matches `contributes.services[].id` in manifest.json. */
const SERVICE_ID = 'inbox'
/** Upper bound for one pull request; the gateway holds for at most 50s. */
const PULL_TIMEOUT_MS = 75_000
/** One page of the plugin's own sessions is enough to find a thread. */
const SESSION_PAGE = 200

declare const pi: {
  plugin: {
    getId: () => string
    getSettings: () => Promise<Record<string, unknown>>
    getDataPath: () => Promise<string>
  }
  services: {
    register: (service: {
      id: string
      start: (ctx: { log: (msg: string) => void }) => Promise<void> | void
      stop?: () => Promise<void> | void
    }) => Promise<void>
  }
  session: {
    appendMessage: (input: Record<string, unknown>) => Promise<unknown>
    import: (input: Record<string, unknown>) => Promise<unknown>
    list: (input?: Record<string, unknown>) => Promise<unknown>
  }
  net: {
    fetch: (input: {
      url: string
      method?: string
      headers?: Record<string, string>
      body?: string
      timeoutMs?: number
      responseType?: 'text' | 'base64'
    }) => Promise<{ status: number; headers: Record<string, string>; bodyText: string }>
  }
  ui: {
    showToast: (message: string, level?: 'info' | 'warn' | 'error') => Promise<void>
  }
}

let handle: SystemPullHandle | null = null

/**
 * `pi.net.fetch` behind the client's transport seam.
 *
 * Header names come back lower-cased from the broker; lookups are tolerant so a
 * case-sensitive host does not make `streamProbe` misread a content type.
 */
const brokerFetch: GatewayFetchImpl = async (url, init) => {
  const res = await pi.net.fetch({
    url,
    method: init.method,
    headers: init.headers,
    ...(init.body ? { body: new TextDecoder().decode(init.body) } : {}),
    timeoutMs: PULL_TIMEOUT_MS,
    responseType: 'text',
  })
  return {
    status: res.status,
    text: async () => res.bodyText,
    headers: {
      get: (name: string) => {
        const lower = name.toLowerCase()
        for (const [key, value] of Object.entries(res.headers ?? {})) {
          if (key.toLowerCase() === lower) return value
        }
        return null
      },
    },
  }
}

async function listSessions(): Promise<readonly DesktopSessionItem[]> {
  const page = (await pi.session.list({ limit: SESSION_PAGE })) as {
    items?: DesktopSessionItem[]
  } | null
  return page?.items ?? []
}

function pullClient(config: DesktopInboxSettings): GatewayClient {
  const agentScope = Boolean(config.agentSystemId)
  return new GatewayClient(config.gatewayUrl, config.apiKey, PULL_TIMEOUT_MS, config.systemId, {
    fetchImpl: brokerFetch,
    ...(agentScope
      ? {
          pendingPath: '/api/v1/agent-system/pending',
          ackPath: '/api/v1/agent-system/pending/ack',
          extraHeaders: { 'X-Agent-System-Id': config.agentSystemId as string },
        }
      : {}),
  })
}

function makeInbox(config: DesktopInboxSettings, log: (line: string) => void): DesktopInbox {
  return createDesktopInbox(
    {
      delivery: createDesktopDelivery(pi),
      listSessions,
      log,
    },
    config,
  )
}

async function startInbox(ctx: { log: (msg: string) => void }): Promise<void> {
  const resolved = resolveDesktopConfig(await pi.plugin.getSettings())
  if (!resolved.ok) {
    ctx.log(`[aimail] not started: ${resolved.reason}`)
    await pi.ui.showToast(`AIMail: ${resolved.reason}`, 'warn')
    return
  }
  const config = resolved.config
  const inbox = makeInbox(config, (line) => ctx.log(line))

  handle = await startSystemPull({
    systemId: config.systemId,
    gatewayUrl: config.gatewayUrl,
    apiKey: config.apiKey,
    client: pullClient(config),
    // The broker cannot stream a body, and probing would cost a full request.
    mode: 'poll',
    intervalMs: (config.intervalSeconds ?? 30) * 1000,
    log: (line) => ctx.log(line),
    onEmail: async (mail) => {
      const outcome = await inbox.deliver({ body: mail.body, headers: mail.headers })
      if (!outcome.ok) {
        // Throwing is the only "do not ack" the pull loop understands.
        throw new Error(`${outcome.reason} (delivery ${mail.id} for ${mail.email})`)
      }
      if (config.address) ctx.log(`[aimail] ${config.address}: ${outcome.kind} → ${outcome.sessionId}`)
    },
  })

  const done = handle.done as Promise<unknown>
  void done.then((reason) => {
    ctx.log(`[aimail] pull loop settled: ${reason === undefined ? 'clean' : String(reason)}`)
    handle = null
  })
}

async function stopInbox(): Promise<void> {
  handle?.stop()
  handle = null
}

async function onLoad(): Promise<void> {
  // Registration only: the broker starts a declared service itself.
  await pi.services.register({ id: SERVICE_ID, start: startInbox, stop: stopInbox })
}

async function onUnload(): Promise<void> {
  await stopInbox()
}

// The host evaluates this file as CommonJS and mirrors these onto the plugin.
module.exports = { onLoad, onUnload }

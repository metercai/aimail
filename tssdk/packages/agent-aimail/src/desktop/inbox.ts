/**
 * The desktop inbox: settings → target session → delivery, with no host objects
 * in the middle so the routing rules can be tested without a plugin process.
 *
 * The plugin entry (`aimail-desktop-plugin/src/main.ts`) owns the two things
 * this file refuses to own: the `pi` API surface (transport + session lookups)
 * and the "not acked unless delivered" policy (it throws, and the pull loop
 * therefore acks nothing).
 *
 * Why session lookup goes through a title: PI-Desktop gives a plugin one stable
 * handle per imported session — `externalId` names the *mail* that created it,
 * and there is no host-side thread index. So the thread root travels in the
 * title (contract §11.5 item 2) and is matched here, and a rename by the user
 * becomes a new session for that thread instead of a wrong delivery.
 */
import { threadRoot, type RoutingPayload } from '../runtime/session-router.js'
import {
  createDesktopDelivery,
  importExternalId,
  sessionTitle,
  type DesktopDeliveryResult,
  type DesktopHostApi,
} from './delivery.js'

export interface DesktopInboxSettings {
  gatewayUrl: string
  apiKey: string
  /** GatewaySystemId: one SSE/pull stream per (AgentSystemId, Gateway). */
  systemId: string
  /** Aggregates every binding of this AgentSystemId (the v2 pull path). */
  agentSystemId?: string
  /** Owning address, for logs and the delivered preamble. */
  address?: string
  /** Host project id (numeric) used as the session's space; metadata, not identity. */
  projectId?: string
  intervalSeconds?: number
}

export type DesktopConfigResult =
  | { ok: true; config: DesktopInboxSettings }
  | { ok: false; reason: string }

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

/** Read the plugin's declarative settings; a missing field is a typed refusal, not a guess. */
export function resolveDesktopConfig(raw: Record<string, unknown>): DesktopConfigResult {
  const gatewayUrl = text(raw['gatewayUrl']).replace(/\/+$/, '')
  if (!/^https?:\/\//i.test(gatewayUrl)) return { ok: false, reason: 'gatewayUrl must be an http(s) URL' }
  const apiKey = text(raw['apiKey'])
  if (!apiKey) return { ok: false, reason: 'apiKey is not set' }
  const systemId = text(raw['systemId'])
  if (!systemId) return { ok: false, reason: 'systemId is not set' }

  const address = text(raw['address'])
  const agentSystemId = text(raw['agentSystemId'])
  const projectId = text(raw['projectId'])
  if (projectId && !/^[1-9]\d*$/.test(projectId)) {
    return { ok: false, reason: 'projectId must be a host project id (a positive integer)' }
  }
  const rawInterval = raw['intervalSeconds']
  const intervalSeconds =
    typeof rawInterval === 'number' && Number.isFinite(rawInterval) && rawInterval > 0
      ? Math.round(rawInterval)
      : 30

  return {
    ok: true,
    config: {
      gatewayUrl,
      apiKey,
      systemId,
      intervalSeconds,
      ...(address ? { address } : {}),
      ...(agentSystemId ? { agentSystemId } : {}),
      ...(projectId ? { projectId } : {}),
    },
  }
}

/** One session as the host lists it (the subset this file reads). */
export interface DesktopSessionItem {
  sessionId: string
  title?: string
  externalId?: string | null
}

/**
 * Which session a mail belongs to, or `null` when the thread needs a new one.
 *
 * Direct-reply routing (contract §9 step 3) needs the outbound index, which the
 * plugin does not have; the mail's own `externalId` covers the retry case
 * (an earlier attempt imported this exact mail), and the thread title covers
 * every later mail of the thread.
 */
export function selectTargetSession(
  items: readonly DesktopSessionItem[],
  req: { threadRoot?: string; messageId?: string },
): string | null {
  const root = text(req.threadRoot)
  const mailId = text(req.messageId)
  if (!root && !mailId) return null
  const title = root ? sessionTitle({ text: '', threadRoot: root }) : ''
  const externalId = mailId ? importExternalId({ text: '', messageId: mailId }) : ''
  for (const item of items) {
    const sessionId = text(item.sessionId)
    if (!sessionId) continue
    if (title && text(item.title) === title) return sessionId
    if (externalId && text(item.externalId) === externalId) return sessionId
  }
  return null
}

/** The mail text, as the gateway preprocessed it. */
export function mailText(body: unknown): string {
  if (typeof body === 'string') return body
  if (!body || typeof body !== 'object') return ''
  const payload = body as Record<string, unknown>
  for (const key of ['body', 'text', 'content']) {
    const value = text(payload[key])
    if (value) return value
  }
  return ''
}

/** The RFC822 Message-ID, whichever of the two places the gateway put it in. */
export function mailMessageId(body: unknown, headers: Record<string, unknown> = {}): string {
  const fromHeaders = text(headers['message-id'])
  if (fromHeaders) return fromHeaders
  if (body && typeof body === 'object') {
    const payload = body as Record<string, unknown>
    const direct = text(payload['message_id'])
    if (direct) return direct
    const nested = payload['headers']
    if (nested && typeof nested === 'object') return text((nested as Record<string, unknown>)['message-id'])
  }
  return ''
}

/** The thread root used for session lookup and the session title. */
export function mailThreadRoot(body: unknown, headers: Record<string, unknown> = {}): string {
  if (!body || typeof body !== 'object') return ''
  const payload = body as Record<string, unknown>
  const merged: Record<string, unknown> = {
    ...(payload['headers'] && typeof payload['headers'] === 'object'
      ? (payload['headers'] as Record<string, unknown>)
      : {}),
    ...headers,
  }
  const routing: RoutingPayload = {
    ...(payload['references'] !== undefined ? { references: payload['references'] } : {}),
    ...(text(payload['in_reply_to']) ? { in_reply_to: text(payload['in_reply_to']) } : {}),
    message_id: text(payload['message_id']) || text(merged['message-id']),
  }
  return threadRoot(routing, merged)
}

export type DesktopInboxOutcome =
  | { ok: true; sessionId: string; kind: string }
  | { ok: false; reason: string }

export interface DesktopInboxDeps {
  delivery: ReturnType<typeof createDesktopDelivery>
  /** `pi.session.list` on a real host. */
  listSessions: () => Promise<readonly DesktopSessionItem[]>
  log?: (line: string) => void
  /** RFC3339 send time, so the transcript keeps the mail's own clock. */
  now?: () => string
}

export interface DesktopInbox {
  config: DesktopInboxSettings
  /** Deliver one pulled mail. `ok: false` means "not acked, keep it pending". */
  deliver: (mail: {
    body: unknown
    headers?: Record<string, unknown>
    receivedAt?: number
  }) => Promise<DesktopInboxOutcome>
}

export function createDesktopInbox(deps: DesktopInboxDeps, config: DesktopInboxSettings): DesktopInbox {
  return {
    config,
    async deliver(mail) {
      const headers = mail.headers ?? {}
      const body = mail.body
      const bodyText = mailText(body)
      if (!bodyText) return { ok: false, reason: 'mail-carries-no-text' }

      const messageId = mailMessageId(body, headers)
      const root = mailThreadRoot(body, headers)
      if (!messageId && !root) return { ok: false, reason: 'mail-carries-no-message-id' }

      let sessionId: string | null = null
      try {
        sessionId = selectTargetSession(await deps.listSessions(), {
          ...(root ? { threadRoot: root } : {}),
          ...(messageId ? { messageId } : {}),
        })
      } catch (error) {
        // A failed lookup must not become a new session: that is a duplicate thread.
        deps.log?.(`aimail: session lookup failed (${String(error)}) — leaving the mail pending`)
        return { ok: false, reason: 'session-lookup-failed' }
      }

      const at = mail.receivedAt ? new Date(mail.receivedAt).toISOString() : deps.now?.()
      const request = {
        text: bodyText,
        ...(messageId ? { messageId } : {}),
        ...(root ? { threadRoot: root } : {}),
        ...(at ? { receivedAt: at } : {}),
        ...(config.projectId ? { projectId: config.projectId } : {}),
      }
      const result: DesktopDeliveryResult = sessionId
        ? await deps.delivery.append({ ...request, sessionId })
        : await deps.delivery.create(request)

      if (result.status !== 'accepted') {
        deps.log?.(`aimail: delivery refused (${result.reason}) — leaving the mail pending`)
        return { ok: false, reason: result.reason }
      }
      return { ok: true, sessionId: result.sessionId, kind: result.kind }
    },
  }
}

/** Host API slice this inbox needs; kept here so both faces agree on one shape. */
export type DesktopInboxHost = DesktopHostApi & {
  session: NonNullable<DesktopHostApi['session']> & {
    list: (input?: Record<string, unknown>) => Promise<unknown>
  }
}

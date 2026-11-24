/**
 * Desktop inbox routing (plan §4, contract §11.5).
 *
 * The rules worth pinning are the ones that decide whether a mail may be
 * acknowledged:
 *   - a missing setting is a typed refusal, never a default endpoint;
 *   - a thread maps to one session through the title, and a retry maps through
 *     the mail's own `externalId`;
 *   - a failed lookup must not fall through to "create a new session" (that is
 *     how a thread forks), and a refused delivery must not be reported as ok.
 */
import { describe, expect, it, vi } from 'vitest'

import { createDesktopDelivery } from '../../src/desktop/delivery.js'
import {
  createDesktopInbox,
  mailMessageId,
  mailText,
  mailThreadRoot,
  resolveDesktopConfig,
  selectTargetSession,
  type DesktopSessionItem,
} from '../../src/desktop/inbox.js'

const AT = new Date('2026-11-24T03:04:05.678Z')

const config = {
  gatewayUrl: 'https://gw.example',
  apiKey: 'k-1',
  systemId: 'sys-1',
}

function inboxFor(
  sessions: readonly DesktopSessionItem[],
  session: { appendMessage?: (i: Record<string, unknown>) => Promise<unknown>; import?: (i: Record<string, unknown>) => Promise<unknown> } = {},
) {
  const api = { session: { ...session } }
  const log = vi.fn()
  const port = createDesktopDelivery(api, { now: () => AT })
  const inbox = createDesktopInbox(
    { delivery: port, listSessions: async () => sessions, log },
    { ...config },
  )
  return { inbox, log, port }
}

describe('resolveDesktopConfig', () => {
  it('accepts a complete set and normalizes the URL and interval', () => {
    const res = resolveDesktopConfig({
      gatewayUrl: 'https://gw.example/',
      apiKey: ' k-1 ',
      systemId: 'sys-1',
      address: 'a@gw.example',
      agentSystemId: 'as-1',
      projectId: '7',
      intervalSeconds: 12.6,
    })
    expect(res).toEqual({
      ok: true,
      config: {
        gatewayUrl: 'https://gw.example',
        apiKey: 'k-1',
        systemId: 'sys-1',
        address: 'a@gw.example',
        agentSystemId: 'as-1',
        projectId: '7',
        intervalSeconds: 13,
      },
    })
  })

  it('refuses each missing field by name instead of inventing a default', () => {
    expect(resolveDesktopConfig({ apiKey: 'k', systemId: 's' })).toEqual({
      ok: false,
      reason: 'gatewayUrl must be an http(s) URL',
    })
    expect(resolveDesktopConfig({ gatewayUrl: 'ftp://gw', apiKey: 'k', systemId: 's' })).toEqual({
      ok: false,
      reason: 'gatewayUrl must be an http(s) URL',
    })
    expect(resolveDesktopConfig({ gatewayUrl: 'https://gw', systemId: 's' })).toEqual({
      ok: false,
      reason: 'apiKey is not set',
    })
    expect(resolveDesktopConfig({ gatewayUrl: 'https://gw', apiKey: 'k' })).toEqual({
      ok: false,
      reason: 'systemId is not set',
    })
    expect(
      resolveDesktopConfig({ gatewayUrl: 'https://gw', apiKey: 'k', systemId: 's', projectId: 'p1' }),
    ).toEqual({ ok: false, reason: 'projectId must be a host project id (a positive integer)' })
  })

  it('defaults the interval and leaves optional fields out', () => {
    const res = resolveDesktopConfig({ gatewayUrl: 'https://gw', apiKey: 'k', systemId: 's' })
    expect(res).toEqual({
      ok: true,
      config: { gatewayUrl: 'https://gw', apiKey: 'k', systemId: 's', intervalSeconds: 30 },
    })
  })
})

describe('mail extraction', () => {
  it('reads the text the gateway preprocessed', () => {
    expect(mailText({ body: 'hello' })).toBe('hello')
    expect(mailText({ text: 'hello' })).toBe('hello')
    expect(mailText('hello')).toBe('hello')
    expect(mailText({})).toBe('')
    expect(mailText(null)).toBe('')
  })

  it('finds the Message-ID in the headers or the payload', () => {
    expect(mailMessageId({}, { 'message-id': '<a@x>' })).toBe('<a@x>')
    expect(mailMessageId({ message_id: '<b@x>' })).toBe('<b@x>')
    expect(mailMessageId({ headers: { 'message-id': '<c@x>' } })).toBe('<c@x>')
    expect(mailMessageId({})).toBe('')
  })

  it('takes the thread root from references[0], else in-reply-to, else the id', () => {
    expect(mailThreadRoot({ references: ['<root@x>', '<mid@x>'], message_id: '<m@x>' })).toBe('<root@x>')
    expect(mailThreadRoot({ in_reply_to: '<parent@x>', message_id: '<m@x>' })).toBe('<parent@x>')
    expect(mailThreadRoot({ message_id: '<m@x>' })).toBe('<m@x>')
    expect(mailThreadRoot({ references: '<root@x> <mid@x>' })).toBe('<root@x>')
    expect(mailThreadRoot({ headers: { 'in-reply-to': '<h@x>' } })).toBe('<h@x>')
    expect(mailThreadRoot(null)).toBe('')
  })
})

describe('selectTargetSession', () => {
  it('matches the thread title the import wrote', () => {
    const items = [{ sessionId: 's-1', title: 'AIMail <root@x>' }, { sessionId: 's-2', title: 'other' }]
    expect(selectTargetSession(items, { threadRoot: '<root@x>' })).toBe('s-1')
    expect(selectTargetSession(items, { threadRoot: '<new@x>' })).toBeNull()
  })

  it('matches a retried mail by its own externalId', () => {
    const items = [{ sessionId: 's-3', title: 'AIMail <root@x>', externalId: 'aimail-root-x' }]
    expect(selectTargetSession(items, { messageId: '<root@x>' })).toBe('s-3')
  })

  it('returns null (not a session) when nothing identifies the thread', () => {
    expect(selectTargetSession([{ sessionId: 's-1', title: 'AIMail' }], {})).toBeNull()
    expect(selectTargetSession([{ sessionId: '' }], { threadRoot: '<root@x>' })).toBeNull()
  })
})

describe('deliver', () => {
  const mail = { body: { body: 'hello', message_id: '<m@x>', references: ['<root@x>'] } }

  it('appends into the thread session and reports success', async () => {
    const appendMessage = vi.fn(async () => ({ messageId: 'aimail-m-x' }))
    const { inbox } = inboxFor([{ sessionId: 's-1', title: 'AIMail <root@x>' }], { appendMessage })

    expect(await inbox.deliver(mail)).toEqual({ ok: true, sessionId: 's-1', kind: 'append' })
    expect(appendMessage).toHaveBeenCalledWith({
      sessionId: 's-1',
      message: {
        id: 'aimail-m-x',
        role: 'user',
        content: 'hello',
        createdAt: '2026-11-24T03:04:05.678Z',
      },
    })
  })

  it('imports a session for a thread nobody has yet', async () => {
    const importFn = vi.fn(async () => ({ sessionId: 's-new', imported: true, skipped: false }))
    const { inbox } = inboxFor([], { import: importFn, appendMessage: async () => ({}) })

    expect(await inbox.deliver(mail)).toEqual({ ok: true, sessionId: 's-new', kind: 'import' })
    expect(importFn).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'AIMail <root@x>', externalId: 'aimail-m-x' }),
    )
  })

  it('never reports ok when the host refused the write', async () => {
    const { inbox, log } = inboxFor([{ sessionId: 's-1', title: 'AIMail <root@x>' }], {
      appendMessage: async () => ({ error: { code: 'PERMISSION_DENIED' } }),
    })
    expect(await inbox.deliver(mail)).toEqual({
      ok: false,
      reason: 'appendMessage-refused:PERMISSION_DENIED',
    })
    expect(log).toHaveBeenCalledOnce()
  })

  it('does not fork a thread when the session lookup fails', async () => {
    const port = createDesktopDelivery({ session: { import: async () => ({}) } }, { now: () => AT })
    const inbox = createDesktopInbox(
      {
        delivery: port,
        listSessions: async () => {
          throw new Error('session.read.own not granted')
        },
      },
      config,
    )
    expect(await inbox.deliver(mail)).toEqual({ ok: false, reason: 'session-lookup-failed' })
  })

  it('refuses mail it cannot route rather than inventing a session', async () => {
    const { inbox } = inboxFor([])
    expect(await inbox.deliver({ body: { body: '' } })).toEqual({
      ok: false,
      reason: 'mail-carries-no-text',
    })
    expect(await inbox.deliver({ body: { body: 'text' } })).toEqual({
      ok: false,
      reason: 'mail-carries-no-message-id',
    })
  })
})

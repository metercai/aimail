import { describe, expect, it, vi } from 'vitest'

import {
  createDesktopDelivery,
  DESKTOP_IMPORT_SOURCE,
  desktopCapabilities,
  destinationMessageId,
  importExternalId,
  sessionTitle,
  type DesktopHostApi,
} from '../../src/desktop/delivery.js'

/**
 * PI-Desktop delivery port (plan §4 `./desktop` face, contract §11.5).
 *
 * Two things are pinned here, because both were wrong in a first draft written against an
 * assumed API:
 *
 *   - the payloads the host actually validates: `appendMessage` takes one `message` object
 *     with an RFC3339 `createdAt`; `import` takes a single (unwrapped) session object with
 *     `source`/`externalId`/`title`/`createdAt`/`updatedAt`/`messages`.
 *   - the retry story: the delivered message id is derived from the mail, and the import
 *     `externalId` is per mail, so a replay lands on what the host already holds.
 *
 * Plus the rule that matters for the mailbox: every refusal is a typed `skip`. The caller
 * must never ack on one.
 */

const AT = new Date('2026-11-24T03:04:05.678Z')
const ISO = '2026-11-24T03:04:05.678Z'

function api(overrides: Partial<NonNullable<DesktopHostApi['session']>> = {}): DesktopHostApi {
  return { session: { ...overrides } }
}

const req = { sessionId: 's1', text: 'hello', messageId: '<abc123@mail.example.org>', threadRoot: 'root@x' }

function port(
  overrides: Partial<NonNullable<DesktopHostApi['session']>> = {},
) {
  return createDesktopDelivery(api(overrides), { now: () => AT })
}

describe('capability probing', () => {
  it('reports what the host actually offers', () => {
    expect(desktopCapabilities(api())).toEqual({ append: false, create: false, importCreate: false })
    expect(desktopCapabilities(api({ appendMessage: async () => ({}) }))).toEqual({
      append: true,
      create: false,
      importCreate: false,
    })
    expect(
      desktopCapabilities(
        api({ appendMessage: async () => ({}), create: async () => ({}), import: async () => ({}) }),
      ),
    ).toEqual({ append: true, create: true, importCreate: true })
  })
})

describe('deterministic anchors', () => {
  it('derives the delivered message id from the mail, inside the host bound', () => {
    const id = destinationMessageId(req)
    expect(id).toBe('aimail-abc123-mail.example.org')
    expect(id.length).toBeLessThanOrEqual(128)
  })

  it('is stable across calls, so a retry names the row the host already holds', () => {
    expect(destinationMessageId({ ...req })).toBe(destinationMessageId({ ...req }))
  })

  it('falls back to a content fingerprint when no Message-ID was seen', () => {
    const bare = { text: 'hello' }
    expect(destinationMessageId(bare)).toMatch(/^aimail-auto-[0-9a-f]{8}$/)
    expect(destinationMessageId({ text: 'hello' })).toBe(destinationMessageId({ text: 'hello ' }))
    expect(destinationMessageId({ text: 'other' })).not.toBe(destinationMessageId(bare))
  })

  it('keeps the import origin per mail and inside the host bound', () => {
    expect(importExternalId(req)).toBe('aimail-abc123-mail.example.org')
    const long = importExternalId({ text: 'x', messageId: 'a'.repeat(400) })
    expect(long.length).toBeLessThanOrEqual(256)
  })

  it('titles an imported session with the thread root, clipped to the host bound', () => {
    expect(sessionTitle(req)).toBe('AIMail root@x')
    expect(sessionTitle({ text: 'x' })).toBe('AIMail')
    expect([...sessionTitle({ text: 'x', threadRoot: 'r'.repeat(300) })].length).toBe(200)
  })
})

describe('append (direct reply / known thread)', () => {
  it('sends the one message object the host validates and reports its id', async () => {
    const appendMessage = vi.fn(async () => ({ messageId: 'aimail-abc123-mail.example.org' }))
    const res = await port({ appendMessage }).append(req)
    expect(res).toEqual({
      status: 'accepted',
      kind: 'append',
      sessionId: 's1',
      messageId: 'aimail-abc123-mail.example.org',
    })
    expect(appendMessage).toHaveBeenCalledExactlyOnceWith({
      sessionId: 's1',
      message: {
        id: 'aimail-abc123-mail.example.org',
        role: 'user',
        content: 'hello',
        createdAt: ISO,
      },
    })
  })

  it('prefers the mail receive time over the clock when one is given', async () => {
    const appendMessage = vi.fn(async () => ({}))
    await port({ appendMessage }).append({ ...req, receivedAt: '2026-11-20T00:00:00.000Z' })
    expect(appendMessage.mock.calls[0]?.[0]).toMatchObject({
      message: { createdAt: '2026-11-20T00:00:00.000Z' },
    })
  })

  it('skips (never acks) when the host lacks appendMessage', async () => {
    expect(await port({}).append(req)).toEqual({ status: 'skip', reason: 'host-lacks-appendMessage' })
  })

  it('skips when no target session is known', async () => {
    expect(await port({ appendMessage: async () => ({}) }).append({ ...req, sessionId: '' })).toEqual({
      status: 'skip',
      reason: 'no-target-session',
    })
  })

  it('refuses an empty body and an over-long one before the RPC', async () => {
    const appendMessage = vi.fn(async () => ({}))
    expect(await port({ appendMessage }).append({ ...req, text: '   ' })).toEqual({
      status: 'skip',
      reason: 'empty-text',
    })
    expect(await port({ appendMessage }).append({ ...req, text: 'x'.repeat(512 * 1024 + 1) })).toEqual({
      status: 'skip',
      reason: 'content-too-large',
    })
    expect(appendMessage).not.toHaveBeenCalled()
  })

  it('treats a void answer as accepted and both refusal envelopes as skips', async () => {
    expect((await port({ appendMessage: async () => undefined }).append(req)).status).toBe('accepted')
    expect(await port({ appendMessage: async () => ({ ok: false }) }).append(req)).toEqual({
      status: 'skip',
      reason: 'appendMessage-refused:HOST_REFUSED',
    })
    expect(
      await port({ appendMessage: async () => ({ error: { code: 'PERMISSION_DENIED' } }) }).append(req),
    ).toEqual({ status: 'skip', reason: 'appendMessage-refused:PERMISSION_DENIED' })
  })

  it('turns a thrown RPC error into a skip instead of letting it escape', async () => {
    const refusing = port({
      appendMessage: async () => {
        throw Object.assign(new Error('PERMISSION_DENIED: session.update.all is not granted'), {
          code: 'PERMISSION_DENIED',
        })
      },
    })
    expect(await refusing.append(req)).toEqual({
      status: 'skip',
      reason: 'appendMessage-refused:PERMISSION_DENIED',
    })
  })
})

describe('create (new thread)', () => {
  it('uses session.create when the host offers it, then appends under the mail id', async () => {
    const create = vi.fn(async () => ({ sessionId: 'new-1' }))
    const appendMessage = vi.fn(async () => ({}))
    const res = await port({ create, appendMessage }).create({ ...req, sessionId: '', projectId: '7' })
    expect(res).toEqual({
      status: 'accepted',
      kind: 'create+append',
      sessionId: 'new-1',
      messageId: 'aimail-abc123-mail.example.org',
    })
    expect(create).toHaveBeenCalledExactlyOnceWith({ projectId: 7, title: 'AIMail root@x' })
    expect(appendMessage).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'new-1', message: expect.objectContaining({ role: 'user' }) }),
    )
  })

  it('does not send a non-numeric project id to a host that wants a row id', async () => {
    const create = vi.fn(async () => ({ sessionId: 'new-1' }))
    await port({ create, appendMessage: async () => ({}) }).create({
      ...req,
      sessionId: '',
      projectId: 'proj-alpha',
    })
    expect(create.mock.calls[0]?.[0]).toEqual({ title: 'AIMail root@x' })
  })

  it('skips when neither create nor import exists', async () => {
    expect(await port({ appendMessage: async () => ({}) }).create({ ...req, sessionId: '' })).toEqual({
      status: 'skip',
      reason: 'host-lacks-session-create',
    })
  })

  it('does not report acceptance when the follow-up append is impossible', async () => {
    const create = vi.fn(async () => ({ sessionId: 'n-1' }))
    expect(await port({ create }).create({ ...req, sessionId: '' })).toEqual({
      status: 'skip',
      reason: 'host-lacks-appendMessage',
    })
  })

  it('skips when create answers without a session id', async () => {
    expect(
      await port({ create: async () => ({ ok: true }), appendMessage: async () => ({}) }).create({
        ...req,
        sessionId: '',
      }),
    ).toEqual({ status: 'skip', reason: 'create-returned-no-session' })
  })
})

describe('import (today\'s host: no session.create)', () => {
  it('sends the single session object the host validates, mail included', async () => {
    const importFn = vi.fn(async () => ({ sessionId: 'imported-1', imported: true, skipped: false }))
    const appendMessage = vi.fn(async () => ({}))
    const res = await port({ import: importFn, appendMessage }).create({
      ...req,
      sessionId: '',
      projectId: '7',
    })

    expect(res).toEqual({ status: 'accepted', kind: 'import', sessionId: 'imported-1' })
    // The import alone delivered the mail: a second write would duplicate it.
    expect(appendMessage).not.toHaveBeenCalled()
    expect(importFn).toHaveBeenCalledExactlyOnceWith({
      source: DESKTOP_IMPORT_SOURCE,
      sourceLabel: 'AIMail',
      externalId: 'aimail-abc123-mail.example.org',
      title: 'AIMail root@x',
      createdAt: ISO,
      updatedAt: ISO,
      projectId: 7,
      messages: [{ role: 'user', content: 'hello', createdAt: ISO }],
    })
  })

  it('treats skipped as a success: this exact mail was already imported', async () => {
    const importFn = vi.fn(async () => ({ sessionId: 'imported-1', imported: false, skipped: true }))
    const res = await port({ import: importFn, appendMessage: async () => ({}) }).create({
      ...req,
      sessionId: '',
    })
    expect(res).toEqual({ status: 'accepted', kind: 'import-existing', sessionId: 'imported-1' })
  })

  it('proves delivery by appending when an older host reports neither flag', async () => {
    const appendMessage = vi.fn(async () => ({}))
    const res = await port({
      import: async () => ({ sessionId: 'imported-1' }),
      appendMessage,
    }).create({ ...req, sessionId: '' })
    expect(res).toMatchObject({ status: 'accepted', kind: 'import', sessionId: 'imported-1' })
    expect(appendMessage).toHaveBeenCalledOnce()
  })

  it('drops a stale space binding rather than losing the mail', async () => {
    const importFn = vi
      .fn()
      .mockRejectedValueOnce(Object.assign(new Error('NOT_FOUND: project not found'), { code: 'NOT_FOUND' }))
      .mockResolvedValueOnce({ sessionId: 'imported-2', imported: true, skipped: false })
    const res = await port({ import: importFn, appendMessage: async () => ({}) }).create({
      ...req,
      sessionId: '',
      projectId: '9',
    })
    expect(res).toEqual({
      status: 'accepted',
      kind: 'import',
      sessionId: 'imported-2',
      projectDropped: true,
    })
    expect(importFn).toHaveBeenLastCalledWith(expect.not.objectContaining({ projectId: expect.anything() }))
  })

  it('skips (never acks) when the import is refused or names no session', async () => {
    expect(
      await port({ import: async () => ({ error: { code: 'PERMISSION_DENIED' } }), appendMessage: async () => ({}) }).create({
        ...req,
        sessionId: '',
      }),
    ).toEqual({ status: 'skip', reason: 'import-refused:PERMISSION_DENIED' })
    expect(
      await port({ import: async () => ({ imported: true }), appendMessage: async () => ({}) }).create({
        ...req,
        sessionId: '',
      }),
    ).toEqual({ status: 'skip', reason: 'import-returned-no-session' })
  })
})

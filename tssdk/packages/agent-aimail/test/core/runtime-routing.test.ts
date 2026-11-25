import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { decideInboundDelivery } from '../../src/runtime/routing.js'
import { localMetaPath } from '../../src/core/meta.js'

/**
 * Contract §9 — the three outcomes in order:
 *   direct (hard) → thread followup → new session.
 *
 * These cases pin the two ways a naive implementation goes wrong:
 *   - treating a thread record as "we sent this mail" (⇒ everything becomes direct)
 *   - reusing a session across spaces (⇒ mail lands in the wrong project)
 */

const EMAIL = 'agent@test.local'
const WS = { id: 'w1', path: '/work/project' }

let home: string
let prevHome: string | undefined

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-routing-'))
  prevHome = process.env.AIMAIL_HOME
  process.env.AIMAIL_HOME = home
})

afterEach(() => {
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  fs.rmSync(home, { recursive: true, force: true })
})

/** Write one local meta record — the index both routing paths read. */
async function seed(messageId: string, meta: Record<string, unknown>) {
  const file = localMetaPath(EMAIL, messageId)
  await fs.promises.mkdir(path.dirname(file), { recursive: true })
  await fs.promises.writeFile(
    file,
    JSON.stringify({
      message_id: messageId,
      references: [],
      thread_id: messageId,
      my_aimail_addr: EMAIL,
      at: new Date().toISOString(),
      direction: 'inbound',
      ...meta,
    }),
    'utf-8',
  )
}

describe('decideInboundDelivery', () => {
  it('routes a direct reply to the session that sent the parent (outbound meta)', async () => {
    await seed('out-1@aimail', { direction: 'outbound', session_id: 'session-1', workspace_path: WS.path })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: {
        message_id: 'reply@ext',
        in_reply_to: '<out-1@aimail>',
        references: ['<a@ext>', '<out-1@aimail>'],
      },
    })
    expect(route).toEqual({ kind: 'direct', sessionId: 'session-1', parent: 'out-1@aimail', workspace: WS })
  })

  it('uses the last reference when In-Reply-To is absent', async () => {
    await seed('out-2@aimail', { direction: 'outbound', session_id: 'session-2' })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: 'reply@ext', references: ['<a@ext>', '<out-2@aimail>'] },
    })
    expect(route.kind).toBe('direct')
    if (route.kind === 'direct') expect(route.sessionId).toBe('session-2')
  })

  it('does not treat an inbound record as "we sent it"', async () => {
    await seed('root@ext', { direction: 'inbound', session_id: 'session-3', workspace_path: WS.path })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: 'next@ext', references: ['<root@ext>', '<x@ext>'] },
    })
    expect(route).toEqual({ kind: 'followup', sessionId: 'session-3', workspace: WS, root: 'root@ext' })
  })

  it('starts a new session when the thread session lives in another space (§16 C16)', async () => {
    await seed('root@ext', { direction: 'inbound', session_id: 'session-4', workspace_path: '/work/other' })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: 'next@ext', references: ['<root@ext>'] },
    })
    expect(route).toEqual({
      kind: 'new',
      workspace: WS,
      root: 'root@ext',
      // The refusal is reported, not silent: ops must see WHY the thread session was skipped.
      anchorMoved: { sessionId: 'session-4', space: '/work/other', edge: 'thread' },
    })
  })

  it('never delivers a direct reply into a session of another anchor (§16 C16 / I2)', async () => {
    // The parent mail WAS ours, but the session that sent it lives in another workspace (the address
    // was re-bound elsewhere since). I2 (no cross-anchor delivery) outranks I1: E1 is abandoned.
    await seed('out-99@ext', {
      direction: 'outbound',
      session_id: 'session-foreign',
      workspace_path: '/work/other',
    })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: 'reply@ext', in_reply_to: '<out-99@ext>' },
    })
    expect(route.kind).toBe('new')
    expect('sessionId' in route).toBe(false) // nothing may receive it from the foreign anchor
    expect(route.anchorMoved).toEqual({
      sessionId: 'session-foreign',
      space: '/work/other',
      edge: 'direct',
    })
  })

  it('keeps the direct reply in its session when both anchors agree', async () => {
    await seed('out-100@ext', {
      direction: 'outbound',
      session_id: 'session-home',
      workspace_path: '/work/project',
    })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: 'reply2@ext', in_reply_to: '<out-100@ext>' },
    })
    expect(route).toEqual({ kind: 'direct', sessionId: 'session-home', parent: 'out-100@ext', workspace: WS })
  })

  it('starts a new session for a brand-new mail', async () => {
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: '<fresh@ext>' },
    })
    expect(route).toEqual({ kind: 'new', workspace: WS, root: 'fresh@ext' })
  })

  it('does not reuse a thread session when the thread root is unknown', async () => {
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: 'm@ext', references: ['<never-seen@ext>'] },
    })
    expect(route.kind).toBe('new')
  })
})

describe('session existence (§9.3 session-gone / C17)', () => {
  it('a recorded session the host no longer has becomes `gone`, not a silent delivery', async () => {
    await seed('out-gone@aimail', { direction: 'outbound', session_id: 'session-deleted' })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: '<reply@gone>', in_reply_to: '<out-gone@aimail>' },
      sessionExists: async (id) => id !== 'session-deleted',
    })
    expect(route).toMatchObject({
      kind: 'gone',
      previousSessionId: 'session-deleted',
      parent: 'out-gone@aimail',
    })
  })

  it('applies to thread records too (a deleted thread session does not absorb new mail)', async () => {
    await seed('root-gone@aimail', { direction: 'inbound', session_id: 'session-deleted' })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: '<m@x>', references: ['<root-gone@aimail>'] },
      sessionExists: async (id) => id !== 'session-deleted',
    })
    expect(route).toMatchObject({ kind: 'gone', previousSessionId: 'session-deleted' })
  })

  it('a live session still routes as before (the probe does not disturb the happy path)', async () => {
    await seed('out-live@aimail', { direction: 'outbound', session_id: 'session-live' })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: '<reply@live>', in_reply_to: '<out-live@aimail>' },
      sessionExists: async () => true,
    })
    expect(route).toMatchObject({ kind: 'direct', sessionId: 'session-live' })
  })

  it('a probe that throws counts as "alive" (a flaky probe must not fork a live thread)', async () => {
    await seed('out-flaky@aimail', { direction: 'outbound', session_id: 'session-live' })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: '<reply@flaky>', in_reply_to: '<out-flaky@aimail>' },
      sessionExists: async () => {
        throw new Error('registry unavailable')
      },
    })
    expect(route).toMatchObject({ kind: 'direct', sessionId: 'session-live' })
  })

  it('without a probe the decision is unchanged (hosts that cannot answer)', async () => {
    await seed('out-noprobe@aimail', { direction: 'outbound', session_id: 'session-x' })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: '<reply@np>', in_reply_to: '<out-noprobe@aimail>' },
    })
    expect(route).toMatchObject({ kind: 'direct', sessionId: 'session-x' })
  })
})


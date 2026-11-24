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

  it('starts a new session when the thread session lives in another space', async () => {
    await seed('root@ext', { direction: 'inbound', session_id: 'session-4', workspace_path: '/work/other' })
    const route = await decideInboundDelivery({
      email: EMAIL,
      workspace: WS,
      payload: { message_id: 'next@ext', references: ['<root@ext>'] },
    })
    expect(route).toEqual({ kind: 'new', workspace: WS, root: 'root@ext' })
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

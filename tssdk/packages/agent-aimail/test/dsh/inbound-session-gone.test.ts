/**
 * dsh inbound routing + the session-gone producer (§9 / §9.3 / C17).
 *
 * QA's two findings drove this file:
 *
 *  1. the producer was unreachable in the real flow: routing keyed off `cfg.session_id`, which is a
 *     transient tool-credential pointer (written before a turn, removed when idle), so `resume` never
 *     ran and `session-gone` never fired;
 *  2. nothing tested the branch that *was* there.
 *
 * Routing now follows the mail's own lineage through the local meta index (E1 direct reply → E2
 * thread → E3 fresh), and the delivery attempt itself is the existence probe: live ⇒ followup,
 * cold-but-resumable ⇒ resume, unresumable ⇒ fresh session + `session-gone`.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { deliverInbound, deliverPulled } from '../../src/dsh/inbound.js'
import { saveAgentConfig } from '../../src/core/config.js'
import { readLocalMeta, saveLocalMeta, stampSessionMeta } from '../../src/core/meta.js'
import { aimailLogPath } from '../../src/core/log.js'
import type { AgentConfig, InboundPayload } from '../../src/core/index.js'

const SID = 'system-dsh-s7'
const EMAIL = 'agent@dsh.local'
const ROOT = '<inbound-s7@remote>'
const PARENT = '<our-outbound@local>'

let home: string
let prevHome: string | undefined

beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-dsh-s7-'))
  prevHome = process.env.AIMAIL_HOME
  process.env.AIMAIL_HOME = home
  await saveAgentConfig(
    {
      email: EMAIL,
      gateway_url: 'http://127.0.0.1:1',
      domain: 'dsh.local',
      system_id: SID,
      agent_system_id: 'asid_test',
      api_key: 'k',
      agent_id: 'agent',
    } as AgentConfig,
    SID,
  )
})

afterEach(async () => {
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  await fs.rm(home, { recursive: true, force: true })
})

type Agents = {
  get: (id: unknown) => unknown
  resume: (opts: unknown) => Promise<unknown>
  create: (opts: { sessionId: string }) => Promise<unknown>
}

function agent(followUps: string[]) {
  return {
    followup: (m: unknown) => {
      followUps.push(String(m).slice(0, 120))
    },
    whenIdle: async () => undefined,
  }
}

function ctxWith(agents: Agents) {
  return { get: (k: string) => (k === 'agents' ? agents : undefined) } as never
}

const target = { cfg: { system_id: SID, email: EMAIL } as AgentConfig, agentAddr: EMAIL }

const mail: InboundPayload = {
  message_id: ROOT,
  from: 'owner@remote.local',
  to: EMAIL,
  subject: 're: change request',
  body: 'approved',
}

/** The thread's session as recorded by a previous delivery. */
async function seedThreadSession(sessionId: string): Promise<void> {
  await saveLocalMeta(EMAIL, ROOT, [], EMAIL, 'inbound')
  await stampSessionMeta(EMAIL, ROOT, { sessionId })
}

describe('deliverInbound — routing by lineage, not by cfg.session_id', () => {
  it('a thread whose session is gone gets a fresh one, stays acked, and names the redirect', async () => {
    await seedThreadSession('sess-old')
    const created: string[] = []
    const followUps: string[] = []
    const agents: Agents = {
      get: () => undefined, // not live in this process
      resume: async () => {
        throw new Error('no such session')
      },
      create: async ({ sessionId }) => {
        created.push(sessionId)
        return { agent: agent(followUps), dispose: async () => undefined }
      },
    }

    const out = await deliverInbound(ctxWith(agents), target, mail, {})

    expect(out.ok).toBe(true)
    expect(created).toHaveLength(1)
    expect(created[0]).not.toBe('sess-old')
    expect(out.status).toBe('session_gone_redirected')
    expect(out.detail).toContain('session-gone')
    expect(out.detail).toContain('sess-old')
    expect(followUps).toHaveLength(1)
    // §9.4: the thread pointer moves to the session that actually took the mail
    expect((await readLocalMeta(EMAIL, ROOT))?.session_id).toBe(created[0])
    // QA T2-4: the downgrade is readable from the dispatch log the s7 driver greps
    const log = await fs.readFile(aimailLogPath(EMAIL), 'utf-8')
    expect(log).toContain('"reason":"session-gone"')
    expect(log).toContain('session_gone_redirected')
  })

  it('a resumable thread session is resumed — no new session, no redirect marker', async () => {
    await seedThreadSession('sess-old')
    const created: string[] = []
    const followUps: string[] = []
    const agents: Agents = {
      get: () => undefined,
      resume: async () => ({ agent: agent(followUps) }),
      create: async ({ sessionId }) => {
        created.push(sessionId)
        return { agent: agent(followUps), dispose: async () => undefined }
      },
    }

    const out = await deliverInbound(ctxWith(agents), target, mail, {})

    expect(out).toMatchObject({ ok: true, status: 'resumed' })
    expect(created).toHaveLength(0)
    expect(followUps).toHaveLength(1)
  })

  it('a live thread session is used directly (no resume round-trip)', async () => {
    await seedThreadSession('sess-live')
    const followUps: string[] = []
    const created: string[] = []
    const agents: Agents = {
      get: () => agent(followUps),
      resume: async () => {
        throw new Error('must not resume a live session')
      },
      create: async ({ sessionId }) => {
        created.push(sessionId)
        return { agent: agent(followUps), dispose: async () => undefined }
      },
    }

    const out = await deliverInbound(ctxWith(agents), target, mail, {})

    expect(out).toMatchObject({ ok: true, status: 'delivered' })
    expect(created).toHaveLength(0)
  })

  it('a direct reply goes to the session that sent the parent, even with a different thread session', async () => {
    // Thread already has a session …
    await seedThreadSession('sess-thread')
    // … but this mail answers one WE sent from another session: E1 wins (§9.2 I1).
    await saveLocalMeta(EMAIL, PARENT, [], EMAIL, 'outbound')
    await stampSessionMeta(EMAIL, PARENT, { sessionId: 'sess-sender' })

    const used: string[] = []
    const agents: Agents = {
      get: (id) => {
        used.push(String(id))
        return agent([])
      },
      resume: async () => {
        throw new Error('not needed')
      },
      create: async ({ sessionId }) => {
        used.push(`created:${sessionId}`)
        return { agent: agent([]), dispose: async () => undefined }
      },
    }

    const out = await deliverInbound(
      ctxWith(agents),
      target,
      { ...mail, in_reply_to: PARENT, references: [ROOT, PARENT] },
      {},
    )

    expect(out).toMatchObject({ ok: true, status: 'delivered' })
    expect(used[0]).toBe('sess-sender')
  })

  it('a first-seen thread opens a fresh session (E3) and pins the thread to it', async () => {
    const created: string[] = []
    const agents: Agents = {
      get: () => undefined,
      resume: async () => {
        throw new Error('not needed')
      },
      create: async ({ sessionId }) => {
        created.push(sessionId)
        return { agent: agent([]), dispose: async () => undefined }
      },
    }

    const out = await deliverInbound(ctxWith(agents), target, { ...mail, message_id: '<brand-new@remote>' }, {})

    expect(out).toMatchObject({ ok: true, status: 'delivered' })
    expect(created).toHaveLength(1)
    expect((await readLocalMeta(EMAIL, '<brand-new@remote>'))?.session_id).toBe(created[0])
  })
})

describe('deliverInbound — refusal must not look like delivery (T2-6)', () => {
  it('a host that cannot create the session reports ok:false so the pull loop keeps the mail pending', async () => {
    const agents: Agents = {
      get: () => undefined,
      resume: async () => {
        throw new Error('not needed')
      },
      create: async () => {
        throw new Error('dsh refused to create a session')
      },
    }

    const out = await deliverInbound(ctxWith(agents), target, { ...mail, message_id: '<cannot-create@remote>' }, {})

    // ok:false ⇒ the caller (pull loop / push handler) must NOT ack: the mail stays pending instead of
    // disappearing into a session that was never created.
    expect(out.ok).toBe(false)
    expect(out.status).toBe('session_create_failed')
    expect(out.detail).toContain('refused to create')
  })
})

describe('deliverPulled — the REAL pull→adapter callback (QA question)', () => {
  it('writes an event:"dispatch" line with reason when a pulled mail is redirected', async () => {
    // This is the function `startInboundPull`'s onEmail calls (src/dsh/inbound.ts), so the
    // dispatch line asserted here is produced by the live path, not by a private branch.
    await seedThreadSession('sess-gone')
    const created: string[] = []
    const agents: Agents = {
      get: () => undefined,
      resume: async () => {
        throw new Error('no such session')
      },
      create: async ({ sessionId }) => {
        created.push(sessionId)
        return { agent: agent([]), dispose: async () => undefined }
      },
    }
    // The pull path resolves the binding through the mail service; one binding is enough.
    const mailService = {
      systemId: SID,
      // The pull path resolves the binding by recipient through the mail service.
      resolveByRecipient: async (addr: string) =>
        String(addr).toLowerCase() === EMAIL ? { system_id: SID, email: EMAIL } : undefined,
    }

    const out = await deliverPulled(
      ctxWith(agents),
      mailService as never,
      { system_id: SID, email: EMAIL } as AgentConfig,
      { id: 77, email: EMAIL, headers: {}, body: { message_id: ROOT, from: 'o@r', to: EMAIL, subject: 's', body: 'b' } },
    )

    expect(out).toMatchObject({ ok: true, status: 'session_gone_redirected' })
    const log = await fs.readFile(aimailLogPath(EMAIL), 'utf-8')
    expect(log).toContain('"event":"dispatch"')
    expect(log).toContain('"reason":"session-gone"')
  })
})


describe('§16 C16 — anchor isolation (never deliver across anchors)', () => {
  // `anchor` = the workspace the address is bound to. The routing layer compares the space recorded
  // on the meta index with the CURRENT one; a mismatch abandons that edge and re-ranks inside the
  // current anchor, recording `anchor-moved` in the dispatch line (s8 evidence greps it).
  const anchored = {
    cfg: { system_id: SID, email: EMAIL, workspace_path: '/work/current' } as AgentConfig,
    agentAddr: EMAIL,
  }

  function agentsCounting(created: string[], followUps: string[], live?: (id: string) => boolean): Agents {
    return {
      get: (id: unknown) => (live && live(String(id)) ? agent(followUps) : undefined),
      resume: async (opts: unknown) => {
        const id = String((opts as { resumeSessionId?: string }).resumeSessionId ?? '')
        if (!live || !live(id)) throw new Error('no such session')
        return { agent: agent(followUps) }
      },
      create: async ({ sessionId }) => {
        created.push(sessionId)
        return { agent: agent(followUps), dispose: async () => undefined }
      },
    }
  }

  it('a thread session in another anchor is not reused — fresh session + anchor-moved', async () => {
    await saveLocalMeta(EMAIL, ROOT, [], EMAIL, 'inbound')
    await stampSessionMeta(EMAIL, ROOT, { sessionId: 'sess-foreign', workspacePath: '/work/other' })
    const created: string[] = []
    const followUps: string[] = []
    // The foreign session IS live — so only the anchor check can stop the delivery.
    const agents = agentsCounting(created, followUps, (id) => id === 'sess-foreign')

    const out = await deliverInbound(ctxWith(agents), anchored, mail, {})

    expect(out.ok).toBe(true)
    expect(created).toHaveLength(1)
    expect(created[0]).not.toBe('sess-foreign')
    expect(followUps).toHaveLength(1)
    const log = await fs.readFile(aimailLogPath(EMAIL), 'utf-8')
    expect(log).toContain('"anchor":"anchor-moved"')
    expect(log).toContain('"anchorMovedEdge":"thread"')
    expect(log).toContain('"anchorMovedFrom":"/work/other"')
  })

  it('a direct reply whose parent was sent from another anchor is not delivered there', async () => {
    await saveLocalMeta(EMAIL, PARENT, [], EMAIL, 'outbound')
    await stampSessionMeta(EMAIL, PARENT, { sessionId: 'sess-foreign-out', workspacePath: '/work/other' })
    const created: string[] = []
    const followUps: string[] = []
    const agents = agentsCounting(created, followUps, (id) => id === 'sess-foreign-out')

    const out = await deliverInbound(
      ctxWith(agents),
      anchored,
      { ...mail, message_id: '<reply-c16@remote>', in_reply_to: PARENT } as InboundPayload,
      {},
    )

    expect(out.ok).toBe(true)
    expect(created).toHaveLength(1)
    expect(created[0]).not.toBe('sess-foreign-out')
    const log = await fs.readFile(aimailLogPath(EMAIL), 'utf-8')
    expect(log).toContain('"anchorMovedEdge":"direct"')
    expect(log).toContain('"anchorMovedFrom":"/work/other"')
  })

  it('a session in the same anchor still takes the mail (the check is not a blanket refusal)', async () => {
    await saveLocalMeta(EMAIL, PARENT, [], EMAIL, 'outbound')
    await stampSessionMeta(EMAIL, PARENT, { sessionId: 'sess-home', workspacePath: '/work/current' })
    const created: string[] = []
    const followUps: string[] = []
    const agents = agentsCounting(created, followUps, (id) => id === 'sess-home')

    const out = await deliverInbound(
      ctxWith(agents),
      anchored,
      { ...mail, message_id: '<reply-c16b@remote>', in_reply_to: PARENT } as InboundPayload,
      {},
    )

    expect(out).toMatchObject({ ok: true, status: 'delivered' })
    expect(created).toHaveLength(0)
    const log = await fs.readFile(aimailLogPath(EMAIL), 'utf-8')
    expect(log).not.toContain('anchor-moved')
  })
})

/**
 * startAgentLevel's default chain: the SDK runs the shared inbound pipeline and
 * hands the ENRICHED payload to the host's agent turn; frames the pipeline
 * consumes (ping/pong → null) never reach the turn.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

const processInboundMail = vi.fn()
vi.mock('../src/preprocess.js', () => ({
  processInboundMail: (...args: unknown[]) => processInboundMail(...args),
}))

import { startAgentLevel } from '../src/agent-level.js'

let tmp: string
let prevHome: string | undefined

beforeEach(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-turn-'))
  prevHome = process.env.AIMAIL_HOME
  process.env.AIMAIL_HOME = path.join(tmp, 'aimail')
  const dir = path.join(process.env.AIMAIL_HOME, 'systems', 'shared_addr_t', 'a_t.local')
  await fs.mkdir(dir, { recursive: true })
  await fs.writeFile(
    path.join(dir, 'agentmail.json'),
    JSON.stringify({ agent_id: 'a', system_id: 'shared_addr_t', email: 'a@t.local',
      gateway_url: 'http://127.0.0.1:1', api_key: 'k' }),
  )
  processInboundMail.mockReset()
})

afterEach(async () => {
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  await fs.rm(tmp, { recursive: true, force: true })
})

/** One delivery on the first round, then an empty queue. */
function fakeClient() {
  let round = 0
  return {
    request: async (method: string, path: string, body?: Record<string, unknown>) => {
      if (path === '/api/v1/admin/pending') {
        round += 1
        if (round > 1) return { status: 200, batches: [] }
        return {
          status: 200,
          batches: [{
            body: { subject: 's' },
            deliveries: [{ id: 1, email: 'a@t.local', headers: { h: '1' } }],
          }],
        }
      }
      if (path === '/api/v1/admin/pending/ack') {
        return { status: 200, acked: (body?.ids as number[] | undefined)?.length ?? 0 }
      }
      return { status: 404, error: 'unexpected ' + method + ' ' + path }
    },
  }
}

async function waitFor(pred: () => boolean, timeoutMs = 2000): Promise<void> {
  const end = Date.now() + timeoutMs
  while (Date.now() < end && !pred()) await new Promise((r) => setTimeout(r, 10))
}

describe('startAgentLevel default inbound chain', () => {
  it('hands the enriched payload to the agent turn', async () => {
    processInboundMail.mockResolvedValue({ message_id: 'm-1', subject: 's' })
    const turns: Array<Record<string, unknown>> = []
    const rt = await startAgentLevel({
      onAgent: (enriched) => { turns.push(enriched as Record<string, unknown>) },
      log: () => {},
      clientFor: () => fakeClient() as never,
    })
    await waitFor(() => turns.length > 0)
    expect(processInboundMail).toHaveBeenCalledTimes(1)
    expect(turns).toEqual([{ message_id: 'm-1', subject: 's' }])
    expect(rt.handles).toHaveLength(1)
    for (const h of rt.handles) h.stop()
  })

  it('never calls the agent turn when the pipeline consumed the frame', async () => {
    processInboundMail.mockResolvedValue(null) // ping/pong
    let called = 0
    const rt = await startAgentLevel({
      onAgent: () => { called += 1 },
      log: () => {},
      clientFor: () => fakeClient() as never,
    })
    await waitFor(() => processInboundMail.mock.calls.length > 0)
    expect(called).toBe(0)
    for (const h of rt.handles) h.stop()
  })

  it('stores an inbound snapshot so the local search tool sees inbound mail', async () => {
    processInboundMail.mockResolvedValue({
      message_id: '<m-2@x>', subject: 'hello searchable', body: 'needle-token',
    })
    let turned = 0
    const rt = await startAgentLevel({
      onAgent: () => { turned += 1 },
      log: () => {},
      clientFor: () => fakeClient() as never,
    })
    // snapshot is written before the turn is handed over
    await waitFor(() => turned > 0)
    const { searchLocalMail } = await import('../src/search.js')
    const r = await searchLocalMail('a@t.local', { query: 'needle-token' })
    expect(r.success).toBe(true)
    expect(r.count ?? 0).toBeGreaterThanOrEqual(1)
    for (const h of rt.handles) h.stop()
  })

  it('onEmail replaces the chain entirely (no preprocessing)', async () => {
    let raw = 0
    const rt = await startAgentLevel({
      onEmail: () => { raw += 1 },
      log: () => {},
      clientFor: () => fakeClient() as never,
    })
    await waitFor(() => raw > 0)
    expect(raw).toBe(1)
    expect(processInboundMail).not.toHaveBeenCalled()
    for (const h of rt.handles) h.stop()
  })
})

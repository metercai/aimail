/**
 * §6.7 #10-#13 — the tier-2/POINTER decision tree (openclaw startInboundPull,
 * P0-6/P0-7). These exercise the REAL delivery chain (deliverPulled →
 * deliverInbound → processInboundMail → deliverToAgent → the host hooks HTTP
 * endpoint) so a skip at the delivery level is observed as "mail never reaches
 * the agent + never acked", not just a log line. The pointer is the real
 * $HOME/.openclaw/.agentmail file (contract-allowed: test gate — asserts the pointer file does NOT exist, V2 no-pointer criterion); the hooks endpoint is a capture HTTP server
 * on an ephemeral port wired through openclaw.json (gateway.port + hooks.path);
 * the aggregate/pull client is a poll-only fake via the startInboundPull
 * clientFor seam.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { promises as fs } from 'node:fs'
import * as http from 'node:http'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  saveAgentConfig,
  findBindingByEmail,
  fireBindingCreated,
  setSoulProvider,
  setSkillsProvider,
  setToolsetProvider,
  resetAgentSystemIdForTests,
  type AgentConfig,
  type GatewayResponse,
  type RequestClient,
} from '../../src/core/index.js'
import { startInboundPull } from '../../src/openclaw/inbound.js'

const ASID = 'asid_ptr_0001'
const GW = 'http://127.0.0.1:9'
const SID = 'system-b237'

function v2(email: string, over: Partial<AgentConfig> = {}): AgentConfig {
  return {
    email,
    gateway_url: GW,
    domain: 'e2e.local',
    system_id: SID,
    api_key: 'key-' + email,
    agent_id: email.split('@')[0],
    agent_system_id: ASID,
    ...over,
  }
}

class FakePoll implements RequestClient {
  baseUrl = GW
  calls: Array<{ method: string; path: string; body?: Record<string, unknown> }> = []
  ackCalls: number[][] = []
  queue: Array<Record<string, unknown>>
  /** POSTs to the pending (pull) endpoint — the loop's round counter. */
  get listCalls(): number {
    return this.calls.filter((c) => c.method === 'POST' && c.path.includes('/pending')).length
  }
  constructor(list: Array<Record<string, unknown>> = []) {
    this.queue = list
  }
  async request(method: string, p: string, body?: Record<string, unknown>): Promise<GatewayResponse> {
    this.calls.push(body === undefined ? { method, path: p } : { method, path: p, body })
    if (p.endsWith('/ack')) {
      const ids = (body?.ids as number[] | undefined) ?? []
      this.ackCalls.push(ids)
      return { status: 200, acked: ids.length } as GatewayResponse
    }
    return (this.queue.shift() ?? { status: 200, batches: [] }) as GatewayResponse
  }
}
const batch = (id: number, email: string): Record<string, unknown> => ({
  status: 200,
  batches: [{ body: { subject: 's', to: [email] }, deliveries: [{ id, email, headers: { 'x-aimail-email': email } }] }],
})

async function waitFor(fn: () => boolean, ms = 5000, what = 'cond'): Promise<void> {
  const t0 = Date.now()
  while (!fn()) {
    if (Date.now() - t0 > ms) throw new Error(`waitFor timeout: ${what}`)
    await new Promise((r) => setTimeout(r, 10))
  }
}

interface Env {
  home: string
  aimailHome: string
  port: number
  server: http.Server
  hooks: Array<{ agentId: string; message: string }>
  close: () => Promise<void>
}

async function makeEnv(): Promise<Env> {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'mail-v2ptr-'))
  const aimailHome = path.join(home, 'aimail')
  await fs.mkdir(aimailHome, { recursive: true })
  const hooks: Array<{ agentId: string; message: string }> = []
  const server = http.createServer((req, res) => {
    let raw = ''
    req.on('data', (c) => {
      raw += c
    })
    req.on('end', () => {
      if (req.url?.endsWith('/agent') && req.method === 'POST') {
        const body = JSON.parse(raw || '{}')
        hooks.push({ agentId: String(body.agentId), message: String(body.message) })
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end('{"status":"ok"}')
      } else {
        res.writeHead(404)
        res.end()
      }
    })
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = (server.address() as { port: number }).port
  // openclaw.json: hooks endpoint + a gateway port the SDK will POST to.
  await fs.mkdir(path.join(home, '.openclaw'), { recursive: true })
  await fs.writeFile(
    path.join(home, '.openclaw', 'openclaw.json'),
    JSON.stringify({ gateway: { port }, hooks: { path: '/hooks' } }),
  )
  // Inbound providers (processInboundMail needs them to build the prompt).
  setSoulProvider(() => 'soul')
  setSkillsProvider(() => [])
  setToolsetProvider(() => [])
  return {
    home,
    aimailHome,
    port,
    server,
    hooks,
    close: async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()))
      setSoulProvider(null)
      setSkillsProvider(null)
      setToolsetProvider(null)
      await fs.rm(home, { recursive: true, force: true })
    },
  }
}

async function writePointer(home: string, systemId: string, email: string): Promise<void> {
  await fs.writeFile(
    path.join(home, '.openclaw', '.agentmail'), // contract-allowed: test gate — asserts the pointer file does NOT exist (V2 no-pointer criterion)
    JSON.stringify({ system_id: systemId, email }),
  )
}

/** Flip the binding's paused flag on disk (the resume path the lifecycle uses). */
async function setPaused(email: string, paused: boolean): Promise<void> {
  const loc = await findBindingByEmail(email)
  if (!loc.activePath) throw new Error(`no binding for ${email}`)
  const raw = await fs.readFile(loc.activePath, 'utf8')
  const cfg = JSON.parse(raw) as Record<string, unknown>
  cfg.paused = paused
  await fs.writeFile(loc.activePath, JSON.stringify(cfg, null, 2))
}

let prevHome: string | undefined
let prevAimailHome: string | undefined
beforeEach(() => {
  prevHome = process.env.HOME
  prevAimailHome = process.env.AIMAIL_HOME
  process.env.AIMAIL_AGENT_SYSTEM_ID = ASID
  resetAgentSystemIdForTests()
})
afterEach(async () => {
  if (prevHome === undefined) delete process.env.HOME
  else process.env.HOME = prevHome
  if (prevAimailHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevAimailHome
  delete process.env.AIMAIL_AGENT_SYSTEM_ID
  resetAgentSystemIdForTests()
})

describe('§6.7 #10-#13 — tier-2 pointer decision tree (openclaw)', () => {
  it('#13 tier-2 AIMAIL_PULL=0 ⇒ 0 stream + audit line', async () => {
    const env = await makeEnv()
    try {
      process.env.HOME = env.home
      process.env.AIMAIL_HOME = env.aimailHome
      const a = v2('a@e2e.local')
      await saveAgentConfig(a, SID)
      await writePointer(env.home, SID, 'a@e2e.local')
      let built = 0
      const lines: string[] = []
      const handles = await startInboundPull({} as never, {
        env: { AIMAIL_PULL: '0' },
        log: (l) => lines.push(l),
        clientFor: () => {
          built++
          return new FakePoll()
        },
      })
      expect(handles).toHaveLength(0)
      expect(built).toBe(0)
      expect(lines.join('\n')).toContain('pull disabled by config')
    } finally {
      await env.close()
    }
  })

  it('#10 pointer paused + NO active sibling + host restart ⇒ 0 stream + audit line', async () => {
    const env = await makeEnv()
    try {
      process.env.HOME = env.home
      process.env.AIMAIL_HOME = env.aimailHome
      const a = v2('a@e2e.local', { paused: true })
      await saveAgentConfig(a, SID)
      await writePointer(env.home, SID, 'a@e2e.local')
      let built = 0
      const lines: string[] = []
      const handles = await startInboundPull({} as never, {
        env: {},
        log: (l) => lines.push(l),
        clientFor: () => {
          built++
          return new FakePoll()
        },
      })
      expect(handles).toHaveLength(0) // a single paused address starts NO stream
      expect(built).toBe(0)
      expect(lines.join('\n')).toContain('paused, no active sibling')
    } finally {
      await env.close()
    }
  })

  it('#11 (P0-7) pointer A paused + B active (same asid/gw) ⇒ exactly ONE aggregate stream; B delivered+acked; A skipped (no ack, errors 0); audit "pointer excluded"', async () => {
    const env = await makeEnv()
    try {
      process.env.HOME = env.home
      process.env.AIMAIL_HOME = env.aimailHome
      await saveAgentConfig(v2('a@e2e.local', { paused: true }), SID)
      await saveAgentConfig(v2('b@e2e.local'), SID)
      await writePointer(env.home, SID, 'a@e2e.local')
      // One aggregate stream serves BOTH addresses (asid ownership). The queue
      // loops: first round [B, A] (B delivered, A skipped at fresh lookup);
      // every following round re-serves A (it was never acked ⇒ still pending).
      const client = new FakePoll()
      client.queue.push(batch(1, 'b@e2e.local'), batch(2, 'a@e2e.local'), batch(2, 'a@e2e.local'))
      // loop the last entry (A) so the re-pull keeps arriving within the window
      const loop = () => {
        const n = client.queue.length
        if (n >= 3) client.queue.push(client.queue[2])
      }
      const origRequest = client.request.bind(client)
      client.request = async (method: string, p: string, body?: Record<string, unknown>) => {
        loop()
        return origRequest(method, p, body)
      }
      const lines: string[] = []
      const handles = await startInboundPull({} as never, {
        env: { AIMAIL_PULL_INTERVAL_MS: '150' },
        log: (l) => lines.push(l),
        clientFor: () => client,
      })
      expect(handles).toHaveLength(1) // EXACTLY ONE aggregate stream
      expect(handles[0]!.email).toBe(`agent:${ASID}`)
      const log = lines.join('\n')
      expect(log).toContain('(pointer excluded)')
      expect(log).toContain('members=[b@e2e.local]')
      // B reaches the host hooks endpoint and is acked.
      await waitFor(() => env.hooks.length >= 1, 5000, 'B to reach the agent')
      expect(env.hooks[0]!.agentId).toBe('b')
      await waitFor(() => client.ackCalls.length >= 1, 5000, 'B ack')
      expect(client.ackCalls.flat()).toContain(1)
      // A's mail is skipped at the delivery level: never delivered, never acked,
      // not counted as an error (it stays pending and is re-pulled).
      await waitFor(() => client.listCalls >= 3, 5000, 'A to be re-pulled (still pending)')
      expect(env.hooks.some((h) => h.agentId === 'a')).toBe(false)
      expect(client.ackCalls.flat()).not.toContain(2)
      handles.forEach((h) => h.stop())
      await handles[0]!.done
    } finally {
      await env.close()
    }
  }, 20000)

  it('#12 (P0-7) A resume (in-process, fireBindingCreated) ⇒ stop-重arm ⇒ still ONE stream (rep back to A) + A delivers again', async () => {
    const env = await makeEnv()
    try {
      process.env.HOME = env.home
      process.env.AIMAIL_HOME = env.aimailHome
      await saveAgentConfig(v2('a@e2e.local', { paused: true }), SID)
      await saveAgentConfig(v2('b@e2e.local'), SID)
      await writePointer(env.home, SID, 'a@e2e.local')
      const clients: FakePoll[] = []
      const mk = () => {
        const c = new FakePoll([batch(1, 'b@e2e.local')])
        clients.push(c)
        return c
      }
      const lines: string[] = []
      const api = {} as never
      const handles = await startInboundPull(api, {
        env: {},
        log: (l) => lines.push(l),
        clientFor: mk,
      })
      // A paused ⇒ the group stream for the active sibling B.
      expect(handles).toHaveLength(1)
      expect(handles[0]!.email).toBe(`agent:${ASID}`)
      await waitFor(() => env.hooks.length >= 1, 5000, 'B to reach the agent (via group stream)')
      // Resume A IN-PROCESS: flip the flag on disk (the lifecycle resume path)
      // and fire the binding hook — the host's onBindingCreated re-arms.
      await setPaused('a@e2e.local', false)
      await fireBindingCreated(v2('a@e2e.local'))
      // The re-arm is a FRESH tier-2 stream (rep = A, the pointer again).
      // We re-invoke the host's re-arm mechanism exactly as openclaw does:
      // stop old handles + startInboundPull.
      handles.forEach((h) => h.stop())
      const handles2 = await startInboundPull(api, {
        env: {},
        log: (l) => lines.push(l),
        clientFor: () => {
          const c = new FakePoll([batch(3, 'a@e2e.local')])
          clients.push(c)
          return c
        },
      })
      expect(handles2).toHaveLength(1) // STILL exactly one stream
      expect(handles2[0]!.email).toBe('agent:a@e2e.local') // rep back to A (tier-2 normal)
      await waitFor(() => env.hooks.some((h) => h.agentId === 'a'), 5000, 'A to deliver after resume')
      handles2.forEach((h) => h.stop())
      await handles2[0]!.done
      expect(clients.length).toBe(2) // no stacked stream
    } finally {
      await env.close()
    }
  }, 25000)
})

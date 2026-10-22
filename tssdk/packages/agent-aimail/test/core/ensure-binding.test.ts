import { describe, expect, test, beforeAll, beforeEach, afterAll } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  ensureAgentBinding,
  AIMAIL_HOME,
  listSystemDirs,
  loadConfigByAgentId,
} from '../../src/core/index.js'
import { BINDING_FILE, INBOUND_PATH } from '../../src/core/contract.js'
import type { AdminClientLike } from '../../src/core/auto-bind.js'

let iso: string
let prevHome: string | undefined

/** Hermetic gateway transport: register (addresses?generate_code) then activate. */
function registerTransport(calls: string[] = []): AdminClientLike {
  return {
    request: async (method, p) => {
      calls.push(`${method} ${p}`)
      if (p.includes('/addresses')) {
        return { status: 201, activation_code: 'CODE123' }
      }
      if (p.includes('/activate-address')) {
        return { status: 200, raw_key: 'a'.repeat(64) }
      }
      return { status: 200 }
    },
  }
}

function seedSystem(sid: string, home: string, extra: Record<string, unknown> = {}): void {
  const dir = path.join(AIMAIL_HOME(), 'systems', sid)
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(
    path.join(dir, 'aimail_gateway.json'),
    JSON.stringify({
      gateway_url: 'https://gw.invalid',
      admin_key: 'ak-test',
      domain: 'example.com',
      system_name: '',
      system_home: home,
      ...extra,
    }),
  )
}

beforeAll(() => {
  prevHome = process.env.AIMAIL_HOME
})
beforeEach(() => {
  if (iso) fs.rmSync(iso, { recursive: true, force: true })
  iso = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-eb-'))
  process.env.AIMAIL_HOME = iso
})
afterAll(() => {
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  if (iso) fs.rmSync(iso, { recursive: true, force: true })
})

describe('ensureAgentBinding (single-source host default-agent binding)', () => {
  test('empty home → not ok', async () => {
    const r = await ensureAgentBinding({ home: '   ' })
    expect(r.ok).toBe(false)
  })

  test('no owning system + reverse-call fails → not ok (no network)', async () => {
    const home = path.join(iso, 'host')
    fs.mkdirSync(home, { recursive: true })
    const exec = async () => ({ code: 1, stdout: '' })
    const r = await ensureAgentBinding({ home, exec, transport: registerTransport() })
    expect(r.ok).toBe(false)
    expect(r.error).toBeTruthy()
  })

  test('owning system, no binding → registers + writes binding, exists=false, agent_id persisted', async () => {
    const home = path.join(iso, 'host')
    const sid = 'sys-a'
    seedSystem(sid, home)
    const calls: string[] = []
    const r = await ensureAgentBinding({
      home,
      agentId: 'agent',
      webhookUrl: `http://127.0.0.1:9099${INBOUND_PATH}`,
      transport: registerTransport(calls),
    })
    expect(r.ok).toBe(true)
    expect(r.exists).toBe(false)
    expect(r.email).toBe('agent@example.com')
    expect(r.systemId).toBe(sid)
    expect(calls.some(c => c.includes('/addresses'))).toBe(true)
    // binding file landed under the system dir with agent_id
    const byAgent = await loadConfigByAgentId(sid, 'agent')
    expect(byAgent?.email).toBe('agent@example.com')
    expect(byAgent?.api_key).toBeTruthy()
    expect(fs.existsSync(
      path.join(AIMAIL_HOME(), 'systems', sid, 'agent_example.com', BINDING_FILE),
    )).toBe(true)
  })

  test('shared-domain address carries the system_name segment', async () => {
    const home = path.join(iso, 'host2')
    const sid = 'sys-b'
    seedSystem(sid, home, { system_name: 'xianlin' })
    const r = await ensureAgentBinding({
      home,
      agentId: 'agent',
      transport: registerTransport(),
    })
    expect(r.ok).toBe(true)
    expect(r.email).toBe('agent.xianlin@example.com')
  })

  test('idempotent: a second call adopts the existing binding, NO register call', async () => {
    const home = path.join(iso, 'host3')
    const sid = 'sys-c'
    seedSystem(sid, home)
    await ensureAgentBinding({ home, agentId: 'agent', transport: registerTransport() })

    const calls2: string[] = []
    const r2 = await ensureAgentBinding({ home, agentId: 'agent', transport: registerTransport(calls2) })
    expect(r2.ok).toBe(true)
    expect(r2.exists).toBe(true)
    expect(r2.email).toBe('agent@example.com')
    // adopt branch is by agent_id — no /addresses call this round
    expect(calls2.some(c => c.includes('/addresses'))).toBe(false)
  })

  test('no system domain in config → not ok', async () => {
    const home = path.join(iso, 'host4')
    const sid = 'sys-nodomain'
    const dir = path.join(AIMAIL_HOME(), 'systems', sid)
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(
      path.join(dir, 'aimail_gateway.json'),
      JSON.stringify({ gateway_url: 'https://gw.invalid', admin_key: 'ak', domain: '', system_home: home }),
    )
    const r = await ensureAgentBinding({ home, transport: registerTransport() })
    expect(r.ok).toBe(false)
    expect(r.error).toMatch(/domain/)
  })

  test('stale webhook_url on adopt → reconciled to the declared endpoint (binding + cloud re-pair)', async () => {
    const home = path.join(iso, 'host-stale')
    const sid = 'sys-stale'
    seedSystem(sid, home)
    // 1st call: binding created with port 9099 (simulates the install-path process).
    await ensureAgentBinding({
      home,
      agentId: 'agent',
      webhookUrl: `http://127.0.0.1:9099${INBOUND_PATH}`,
      transport: registerTransport(),
    })

    // 2nd call: real gateway now declares 9100 (config set gateway.port ran).
    // transport answers the reconcile's GET /domains with the address row.
    const calls: string[] = []
    const reconcileTransport: AdminClientLike = {
      request: async (method, p) => {
        calls.push(`${method} ${p}`)
        if (p.includes('/domains')) {
          return { status: 200, data: [{ id: 7, domain: 'agent@example.com' }] }
        }
        if (p.includes('/system-domains/')) {
          return { status: 200 }
        }
        return { status: 200 }
      },
    }
    const r = await ensureAgentBinding({
      home,
      agentId: 'agent',
      webhookUrl: `http://127.0.0.1:9100${INBOUND_PATH}`,
      transport: reconcileTransport,
    })
    expect(r.ok).toBe(true)
    expect(r.exists).toBe(true)
    expect(r.config?.webhook_url).toBe(`http://127.0.0.1:9100${INBOUND_PATH}`)
    const byAgent = await loadConfigByAgentId(sid, 'agent')
    expect(byAgent?.webhook_url).toBe(`http://127.0.0.1:9100${INBOUND_PATH}`)
    expect(byAgent?.api_key).toBeTruthy() // api_key survived the reconcile
    // no re-registration; cloud re-pair = PUT system-domains
    expect(calls.some(c => c.includes('/addresses'))).toBe(false)
    expect(calls.some(c => c.startsWith('PUT'))).toBe(true)
  })

  test('adopt without a declared endpoint → pure adopt, stored webhook_url untouched, no network', async () => {
    const home = path.join(iso, 'host-nodecl')
    const sid = 'sys-nodecl'
    seedSystem(sid, home)
    await ensureAgentBinding({
      home,
      agentId: 'agent',
      webhookUrl: `http://127.0.0.1:9099${INBOUND_PATH}`,
      transport: registerTransport(),
    })
    const calls: string[] = []
    const r = await ensureAgentBinding({ home, agentId: 'agent', transport: registerTransport(calls) })
    expect(r.ok).toBe(true)
    expect(r.exists).toBe(true)
    expect(r.config?.webhook_url).toBe(`http://127.0.0.1:9099${INBOUND_PATH}`)
    expect(calls).toEqual([])
  })

  test('binding missing webhook_url key + caller declares → key provisioned on adopt', async () => {
    const home = path.join(iso, 'host-missing')
    const sid = 'sys-missing'
    seedSystem(sid, home)
    // 1st call with empty webhookUrl ⇒ binding has no webhook_url key.
    await ensureAgentBinding({ home, agentId: 'agent', webhookUrl: '', transport: registerTransport() })
    const calls: string[] = []
    const r = await ensureAgentBinding({
      home,
      agentId: 'agent',
      webhookUrl: `http://127.0.0.1:9100${INBOUND_PATH}`,
      transport: registerTransport(calls),
    })
    expect(r.ok).toBe(true)
    const byAgent = await loadConfigByAgentId(sid, 'agent')
    expect(byAgent?.webhook_url).toBe(`http://127.0.0.1:9100${INBOUND_PATH}`)
  })
})

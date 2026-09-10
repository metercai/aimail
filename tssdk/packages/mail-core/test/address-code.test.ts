/**
 * Address-code consumption chain (open-application plan §2.4/§2.5, slice 7).
 *
 * MockClient pins:
 * 1. activateAddressCode — lowercases the email, maps failure shapes,
 *    surfaces raw_key/system_id/expires_at on success.
 * 2. activateAddressCodePersist — writes a register-isomorphic
 *    agentmail.json (saveBinding field set + expires_at), the
 *    create-if-absent connection file, and the discovery pointer;
 *    never clobbers an existing admin_key.
 * 3. pullList/pullAck — agent-scope pull + ack contract.
 * 4. startPolling — dedup by delivery id, on_email failure stays
 *    un-acked, pull failure counts an error and continues.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  activateAddressCode,
  activateAddressCodePersist,
  pullList,
  pullAck,
  startPolling,
  type RequestClient,
} from '../src/address-code.js'
import type { GatewayResponse } from '../src/types.js'

class MockClient implements RequestClient {
  calls: Array<{ method: string; path: string; body?: Record<string, unknown> }> = []
  /** Queued responses for the pull (list) endpoint, consumed in order. */
  responses: Array<Record<string, unknown>> = []
  /** Queued responses for the ack endpoint; default = acked:ids.length. */
  ackResponses: Array<Record<string, unknown>> = []

  request(
    method: string,
    path: string,
    body?: Record<string, unknown>,
  ): Promise<GatewayResponse> {
    this.calls.push({ method, path, body })
    if (path.endsWith('/ack')) {
      const scripted = this.ackResponses.shift()
      if (scripted) return Promise.resolve(scripted as GatewayResponse)
      const ids = (body?.ids as number[] | undefined) ?? []
      return Promise.resolve({ status: 200, acked: ids.length } as GatewayResponse)
    }
    const next = this.responses.shift() ?? { status: 200 }
    return Promise.resolve(next as GatewayResponse)
  }
}

let home: string
let prevHome: string | undefined

beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'mail-core-addr-'))
  prevHome = process.env.AIMAIL_HOME
  process.env.AIMAIL_HOME = home
})

afterEach(async () => {
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  await fs.rm(home, { recursive: true, force: true })
})

describe('activateAddressCode', () => {
  it('lowercases the email and returns the activation result', async () => {
    const c = new MockClient()
    c.responses.push({
      status: 200,
      raw_key: 'rk-1',
      system_id: 'shared_addr_e2e',
      email_address: 'e2e-agent@e2e.local',
      expires_at: '2026-09-13T00:00:00Z',
    })
    const r = await activateAddressCode(c, 'shared_a-x', 'E2E-Agent@E2E.local')
    expect(c.calls[0].path).toBe('/api/v1/activate-address-code')
    expect(c.calls[0].body?.email_address).toBe('e2e-agent@e2e.local')
    expect(r.success).toBe(true)
    expect(r.raw_key).toBe('rk-1')
    expect(r.system_id).toBe('shared_addr_e2e')
    expect(r.expires_at).toBe('2026-09-13T00:00:00Z')
  })

  it('maps a failed activation (no raw_key) to success:false + status', async () => {
    const c = new MockClient()
    c.responses.push({ status: 403, error: 'pre-bound address mismatch' })
    const r = await activateAddressCode(c, 'bad', 'a@b.test')
    expect(r.success).toBe(false)
    expect(r.status).toBe(403)
    expect(r.error).toContain('pre-bound')
  })
})

describe('activateAddressCodePersist', () => {
  it('persists a register-isomorphic binding + connection file + pointer', async () => {
    const c = new MockClient()
    c.responses.push({
      status: 200,
      raw_key: 'rk-2',
      system_id: 'shared_addr_e2e',
      email_address: 'e2e-agent2@e2e.local',
      expires_at: '2026-09-13T00:00:00Z',
    })
    const platformHome = await fs.mkdtemp(path.join(os.tmpdir(), 'plat-'))
    const r = await activateAddressCodePersist(c, 'shared_a-y', 'e2e-agent2@e2e.local', {
      gatewayUrl: 'http://127.0.0.1:38080',
      platformHome,
    })
    expect(r.success).toBe(true)
    expect(r.pointer_written).toBe(true)

    const cfgPath = path.join(
      home, 'systems', 'shared_addr_e2e', 'e2e-agent2_e2e.local', 'agentmail.json',
    )
    expect(r.config_path).toBe(cfgPath)
    const cfg = JSON.parse(await fs.readFile(cfgPath, 'utf8')) as Record<string, unknown>
    // register-isomorphic field set (saveBinding) + address-flow extras
    for (const k of ['email', 'gateway_url', 'domain', 'system_id', 'api_key']) {
      expect(cfg[k], `missing ${k}`).toBeTruthy()
    }
    expect(cfg.agent_id).toBe('e2e-agent2')
    expect(cfg.domain).toBe('e2e.local')
    expect(cfg.expires_at).toBe('2026-09-13T00:00:00Z')
    const mode = (await fs.stat(cfgPath)).mode & 0o777
    expect(mode.toString(8)).toBe('600')

    const gw = JSON.parse(
      await fs.readFile(path.join(home, 'systems', 'shared_addr_e2e', 'aimail_gateway.json'), 'utf8'),
    ) as Record<string, unknown>
    expect(gw.gateway_url).toBe('http://127.0.0.1:38080')
    expect(gw.admin_key).toBe('rk-2')

    const ptr = JSON.parse(
      await fs.readFile(path.join(platformHome, '.agentmail'), 'utf8'),
    ) as Record<string, unknown>
    expect(ptr.system_id).toBe('shared_addr_e2e')
    expect(ptr.email).toBe('e2e-agent2@e2e.local')
  })

  it('does not clobber an existing connection file (admin_key preserved)', async () => {
    const gwDir = path.join(home, 'systems', 'shared_addr_e2e')
    await fs.mkdir(gwDir, { recursive: true })
    await fs.writeFile(
      path.join(gwDir, 'aimail_gateway.json'),
      JSON.stringify({ gateway_url: 'http://old', admin_key: 'keep-me' }),
    )
    const c = new MockClient()
    c.responses.push({
      status: 200,
      raw_key: 'rk-3',
      system_id: 'shared_addr_e2e',
      email_address: 'x@e2e.local',
    })
    await activateAddressCodePersist(c, 'code', 'x@e2e.local', { gatewayUrl: 'http://new' })
    const gw = JSON.parse(
      await fs.readFile(path.join(gwDir, 'aimail_gateway.json'), 'utf8'),
    ) as Record<string, unknown>
    expect(gw.admin_key).toBe('keep-me')
  })
})

describe('pullList / pullAck', () => {
  it('clamps limit to 1..200 and returns batches', async () => {
    const c = new MockClient()
    c.responses.push({ status: 200, batches: [{ body: {}, deliveries: [] }] })
    await pullList(c, 9999)
    expect(c.calls[0].body?.limit).toBe(200)
    const r = await (async () => {
      c.responses.push({ status: 200, batches: [] })
      return pullList(c, -5)
    })()
    expect(c.calls[1].body?.limit).toBe(1)
    expect(r.success).toBe(true)
  })

  it('maps a non-200 to success:false', async () => {
    const c = new MockClient()
    c.responses.push({ status: 401, error: 'Invalid X-Api-Signature' })
    const r = await pullList(c)
    expect(r.success).toBe(false)
    expect(r.error).toContain('Invalid')
  })

  it('ack reports the acked count', async () => {
    const c = new MockClient()
    c.responses.push({ status: 200, acked: 2 })
    const r = await pullAck(c, [1, 2])
    expect(r.success).toBe(true)
    expect(r.acked).toBe(2)
    expect(c.calls[0].path).toBe('/api/v1/admin/pending/ack')
  })
})

describe('startPolling', () => {
  const batch = (id: number) => ({
    body: { subject: 'hi' },
    deliveries: [{ id, email: 'a@x', headers: {} }],
  })

  it('dedups by delivery id — same id twice is delivered once', async () => {
    const c = new MockClient()
    c.responses.push({ status: 200, batches: [batch(7)] })
    c.responses.push({ status: 200, batches: [batch(7)] })
    c.responses.push({ status: 200, batches: [] })
    const got: number[] = []
    const stats = await startPolling(c, (m) => got.push(m.id), {
      intervalMs: 0,
      maxRounds: 3,
    })
    expect(got).toEqual([7])
    expect(stats.pulled).toBe(1)
    expect(stats.acked).toBe(1)
  })

  it('leaves the id un-acked when onEmail throws', async () => {
    const c = new MockClient()
    c.responses.push({ status: 200, batches: [batch(9)] })
    c.responses.push({ status: 200, batches: [] })
    const stats = await startPolling(
      c,
      () => {
        throw new Error('handler down')
      },
      { intervalMs: 0, maxRounds: 2 },
    )
    expect(stats.errors).toBe(1)
    expect(stats.acked).toBe(0)
    // no ack call at all (nothing delivered)
    expect(c.calls.filter((x) => x.path.endsWith('/ack'))).toHaveLength(0)
  })

  it('counts a failed pull round and keeps going', async () => {
    const c = new MockClient()
    c.responses.push({ status: 500, error: 'boom' })
    c.responses.push({ status: 500, error: 'boom' })
    const got: unknown[] = []
    const stats = await startPolling(c, (m) => got.push(m), { intervalMs: 0, maxRounds: 2 })
    expect(stats.errors).toBe(2)
    expect(got).toHaveLength(0)
  })

  it('parses a JSON-string batch body (wire shape)', async () => {
    const c = new MockClient()
    c.responses.push({
      status: 200,
      batches: [{ body: '{"subject":"wire"}', deliveries: [{ id: 3, email: 'a@x', headers: {} }] }],
    })
    const got: Array<{ body: unknown }> = []
    await startPolling(c, (m) => got.push(m), { intervalMs: 0, maxRounds: 1 })
    expect(got[0].body).toEqual({ subject: 'wire' })
  })
})

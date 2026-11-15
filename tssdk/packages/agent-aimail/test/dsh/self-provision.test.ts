/**
 * dsh self-provisioning closed loop — fake gateway + fake ctx.
 *
 * Covers: system code preflight/activate, per-domain keys, default worker on
 * the DEFAULT workspace, B1 local address, B2 shared address, the two
 * mismatch guards, and pause/resume. No CLI, no webhook.
 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest'
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { provisionSystem } from '../../src/dsh/system-core.js'
import { runAddressTool } from '../../src/dsh/address.js'
import { readSystemGatewayConfig } from '../../src/core/provision.js'
import { loadAgentConfig } from '../../src/core/index.js'

interface FakeCall { method: string; url: string; body: string; identity: string }
const calls: FakeCall[] = []
let server: ReturnType<typeof createServer>
let gateway = ''
let home = ''
let dsh = ''

function handler(req: IncomingMessage, res: ServerResponse): void {
  const chunks: Buffer[] = []
  req.on('data', (c) => chunks.push(c))
  req.on('end', () => {
    const body = Buffer.concat(chunks).toString('utf-8')
    const url = req.url ?? ''
    calls.push({ method: req.method ?? '', url, body, identity: String(req.headers['x-api-identity'] ?? '') })
    const send = (obj: unknown): void => {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(obj))
    }
    if (url === '/api/v1/public/activation-code/preflight') {
      const code = String((JSON.parse(body || '{}') as { code?: string }).code ?? '')
      if (code.startsWith('sys-')) {
        send({ valid: true, code_type: 'product', scope: 'system', domain: 'a.com', system_name: '', claimed: false })
      } else if (code.startsWith('shared-')) {
        send({ valid: true, code_type: 'address', scope: 'shared', system_id: 'shared_addr_x', domain: 'shared.test', email_address: 'me@shared.test', claimed: false })
      } else {
        send({ valid: true, code_type: 'address', scope: 'system', system_id: 'sys-1', domain: 'a.com', email_address: 'user1@a.com', claimed: false })
      }
      return
    }
    if (url === '/api/v1/activate-system') {
      send({ raw_key: 'SYS_RAW', system_id: 'sys-1', domain: 'a.com', domains: ['a.com', 'b.com'], system_name: '', manager_address: 'mgr@a.com', kind: 'new' })
      return
    }
    if (url === '/api/v1/activate-address-code') {
      const parsed = JSON.parse(body || '{}') as { email_address?: string }
      send({ raw_key: `AGENT_${parsed.email_address}`, system_id: parsed.email_address?.endsWith('@shared.test') ? 'shared_addr_x' : 'sys-1', email_address: parsed.email_address, kind: 'new' })
      return
    }
    if (url.startsWith('/api/v1/admin/systems/sys-1/domains')) {
      send({ data: [{ domain: 'a.com' }, { domain: 'b.com' }] })
      return
    }
    if (url === '/api/v1/admin/systems/sys-1/addresses') {
      send({ status: 200 })
      return
    }
    if (url === '/api/v1/admin/api-keys') {
      const parsed = JSON.parse(body || '{}') as { email_address?: string; category?: string }
      send({ raw_key: `KEY_${parsed.category}_${parsed.email_address}` })
      return
    }
    send({ status: 404, error: `unexpected ${url}` })
  })
}

function fakeCtx(): { get(k: string): unknown; provide(k: string, v: unknown): void } {
  const values = new Map<string, unknown>()
  const registry = {
    list: () => [{ id: 'w-default', path: path.join(dsh, 'default-workspace'), title: 'default-workspace' }],
    create: async (p: string) => ({ id: 'w-created', path: p, title: path.basename(p) }),
  }
  const bindings: Array<{ id: string; path: string }> = [{ id: 'w-default', path: path.join(dsh, 'default-workspace') }]
  values.set('workspaceRegistry', registry)
  values.set('sessions', { get: (id: string) => (id === 'sess-1' ? { header: { cwd: path.join(dsh, 'proj') } } : undefined) })
  values.set('agents', { get: () => undefined, resume: async () => { throw new Error('no resume') }, create: async () => ({ agent: { followup() {} }, dispose: async () => {} }) })
  values.set('agentDefaultModel', { currentSelection: () => ({ provider: 'p', model: 'm' }) })
  values.set('mail', {
    resolveByRecipient: async (email: string) => {
      for (const sid of ['sys-1', 'shared_addr_x']) {
        const cfg = await loadAgentConfig(sid, email)
        if (cfg) return cfg
      }
      return undefined
    },
    resolveCtx: async (id: string) => ({ systemId: 'sys-1', email: 'user1@a.com', sessionId: id }),
  })
  return {
    get: (k: string) => values.get(k),
    provide: (k: string, v: unknown) => { values.set(k, v) },
  }
}

beforeEach(async () => {
  calls.length = 0
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-home-'))
  dsh = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-home-'))
  process.env.AIMAIL_HOME = home
  process.env.DSH_HOME = dsh
  await fs.mkdir(path.join(dsh, 'storages'), { recursive: true })
  await fs.mkdir(path.join(dsh, 'default-workspace'), { recursive: true })
  await fs.mkdir(path.join(dsh, 'proj'), { recursive: true })
  await fs.writeFile(path.join(dsh, 'storages', 'workspace.json'), JSON.stringify({
    global: { defaultWorkspaceId: 'w-default' },
    tables: { workspaces: { 'w-default': { path: path.join(dsh, 'default-workspace'), title: 'default-workspace' } } },
  }))
  server = createServer(handler)
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()))
  const addr = server.address()
  gateway = `http://127.0.0.1:${typeof addr === 'object' && addr ? addr.port : 0}`
})

afterEach(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()))
  delete process.env.AIMAIL_HOME
  delete process.env.DSH_HOME
  await fs.rm(home, { recursive: true, force: true })
  await fs.rm(dsh, { recursive: true, force: true })
})

describe('system onboarding', () => {
  test('activates, writes per-domain keys, and binds the default worker to the DEFAULT workspace', async () => {
    const ctx = fakeCtx() as never
    const r = await provisionSystem(ctx, { action: 'bind', code: 'sys-code', gateway_url: gateway, domain: 'a.com', manager: 'boss@a.com' })
    expect(r.success).toBe(true)
    expect(r.scope).toBe('system')
    expect(r.system_id).toBe('sys-1')
    expect(r.default_email).toBe('agent@a.com')
    expect((r.workspace as { path: string }).path).toBe(await fs.realpath(path.join(dsh, 'default-workspace')))

    const cfg = await readSystemGatewayConfig('sys-1')
    expect(cfg?.admin_key).toBe('KEY_domain_a.com')
    expect(cfg?.domain_keys).toEqual({ 'a.com': 'KEY_domain_a.com', 'b.com': 'KEY_domain_b.com' })
    expect(cfg?.webhook_host).toBeUndefined()
    await expect(fs.access(path.join(home, 'systems', 'sys-1', '.system_raw_key.key'))).rejects.toThrow()

    const binding = await loadAgentConfig('sys-1', 'agent@a.com')
    expect(binding?.workspace_path).toBe(await fs.realpath(path.join(dsh, 'default-workspace')))
    expect(binding?.api_key).toMatch(/^KEY_agent_agent@a\.com$/)
    expect(binding?.webhook_url).toBeUndefined()
    expect(JSON.parse(await fs.readFile(path.join(dsh, '.agentmail'), 'utf-8')).system_id).toBe('sys-1')
    // manager override reached the activation request
    const activate = calls.find((c) => c.url === '/api/v1/activate-system')
    expect(JSON.parse(activate!.body).manager).toBe('boss@a.com')
  })

  test('a system code in the address tool is rejected', async () => {
    const ctx = fakeCtx() as never
    const r = await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'bind', email: 'x@a.com', code: 'sys-code', gateway_url: gateway })
    expect(r.success).toBe(false)
    expect(r.error_code).toBe('wrong_tool')
  })
})

describe('address binding', () => {
  test('B1 registers a local address with the selected domain key', async () => {
    const ctx = fakeCtx() as never
    await provisionSystem(ctx, { action: 'bind', code: 'sys-code', gateway_url: gateway, domain: 'a.com' })
    const r = await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'bind', email: 'user1@a.com' })
    expect(r.success).toBe(true)
    expect(r.mode).toBe('register')
    expect((r.workspace as { path: string }).path).toBe(await fs.realpath(path.join(dsh, 'proj')))
    const binding = await loadAgentConfig('sys-1', 'user1@a.com')
    expect(binding?.api_key).toMatch(/^KEY_agent_user1@a\.com$/)
    const reg = calls.find((c) => c.url === '/api/v1/admin/systems/sys-1/addresses')
    expect(JSON.parse(reg!.body).webhook_url).toBeUndefined()
    expect(reg!.identity).toBe('sys-1')
  })

  test('B2 binds a shared address with its code, without a local system', async () => {
    const ctx = fakeCtx() as never
    const r = await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'bind', email: 'me@shared.test', code: 'shared-code', gateway_url: gateway })
    expect(r.success).toBe(true)
    expect(r.mode).toBe('code')
    const binding = await loadAgentConfig('shared_addr_x', 'me@shared.test')
    expect(binding?.api_key).toBe('AGENT_me@shared.test')
    expect(binding?.workspace_path).toBe(await fs.realpath(path.join(dsh, 'proj')))
  })

  test('B2 without a system and a non-shared code is rejected before activation', async () => {
    const ctx = fakeCtx() as never
    const r = await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'bind', email: 'user1@a.com', code: 'addr-code', gateway_url: gateway })
    expect(r.success).toBe(false)
    expect(r.error_code).toBe('needs_system')
    expect(calls.some((c) => c.url === '/api/v1/activate-address-code')).toBe(false)
  })

  test('system onboarded + shared code is rejected before activation', async () => {
    const ctx = fakeCtx() as never
    await provisionSystem(ctx, { action: 'bind', code: 'sys-code', gateway_url: gateway, domain: 'a.com' })
    const r = await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'bind', email: 'me@shared.test', code: 'shared-code', gateway_url: gateway })
    expect(r.success).toBe(false)
    expect(r.error_code).toBe('system_mismatch')
  })

  test('B1 in a second domain uses that domain\'s key', async () => {
    const ctx = fakeCtx() as never
    await provisionSystem(ctx, { action: 'bind', code: 'sys-code', gateway_url: gateway, domain: 'a.com' })
    const r = await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'bind', email: 'user2@b.com' })
    expect(r.success).toBe(true)
    const binding = await loadAgentConfig('sys-1', 'user2@b.com')
    expect(binding?.api_key).toBe('KEY_agent_user2@b.com')
    const sys = await readSystemGatewayConfig('sys-1')
    expect(sys?.domain_keys?.['b.com']).toBe('KEY_domain_b.com')
  })

  test('unbind and manager are rejected', async () => {
    const ctx = fakeCtx() as never
    const unbind = await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'unbind', email: 'user1@a.com' })
    expect(unbind.success).toBe(false)
    expect(unbind.error_code).toBe('bad_args')
    const manager = await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'bind', email: 'user1@a.com', manager: 'x@y' })
    expect(manager.success).toBe(false)
    expect(manager.error_code).toBe('bad_args')
  })

  test('code bound to another email is rejected before activation', async () => {
    const ctx = fakeCtx() as never
    await provisionSystem(ctx, { action: 'bind', code: 'sys-code', gateway_url: gateway, domain: 'a.com' })
    const before = calls.length
    const r = await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'bind', email: 'other@a.com', code: 'addr-code', gateway_url: gateway })
    expect(r.success).toBe(false)
    expect(r.error_code).toBe('activation_invalid')
    expect(calls.slice(before).some((c) => c.url === '/api/v1/activate-address-code')).toBe(false)
  })

  test('pause and resume are local and idempotent', async () => {
    const ctx = fakeCtx() as never
    await provisionSystem(ctx, { action: 'bind', code: 'sys-code', gateway_url: gateway, domain: 'a.com' })
    await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'bind', email: 'user1@a.com' })
    const p = await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'pause', email: 'user1@a.com' })
    expect(p.success).toBe(true)
    expect(p.paused).toBe(true)
    expect((await loadAgentConfig('sys-1', 'user1@a.com'))?.paused).toBe(true)
    const r = await runAddressTool(ctx, { agent: { id: 'sess-1' } }, { action: 'resume', email: 'user1@a.com' })
    expect(r.success).toBe(true)
    expect((await loadAgentConfig('sys-1', 'user1@a.com'))?.paused).toBe(false)
  })
})

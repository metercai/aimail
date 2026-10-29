/**
 * sdk-ops regression — the node-side three-op (assemble / update / teardown).
 *
 * These are the ops the CLI drives on a node transport (dsh / pi / openclaw) via
 * `node <register-cli.js> --op <op> --args <json>`; the python door
 * (`python3 -m aimail.sdk_ops <op>`) carries the SAME semantics. This test pins
 * the payload keys the CLI reads (see cli/src/core/register.rs, repair.rs,
 * uninstall.rs) so a rename of a key is a build/test failure, not a silent red
 * in the docker L2.
 *
 * Gateway is stubbed with `fetch`; no real network. Mirrors auto-bind.test.ts.
 */
import { describe, expect, test, beforeAll, afterAll, vi } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { AIMAIL_HOME, opAssemble, opUpdate, opTeardown, UsageError } from '../../src/core/index.js'
import { BINDING_FILE } from '../../src/core/contract.js'
import { cleanAddr } from '../../src/core/config.js'

let tmpHome: string

function bodyText(raw: unknown): string {
  if (typeof raw === 'string') return raw
  if (raw instanceof Uint8Array) return Buffer.from(raw).toString('utf-8')
  if (raw instanceof ArrayBuffer) return Buffer.from(new Uint8Array(raw)).toString('utf-8')
  return ''
}

function seedSystem(sid: string, domain: string, sysName: string): void {
  const dir = path.join(tmpHome, 'systems', sid)
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(
    path.join(dir, 'aimail_gateway.json'),
    JSON.stringify({
      gateway_url: 'https://gw.invalid',
      admin_key: 'ak-test',
      domain,
      system_name: sysName,
      manager_address: 'mgr@ext.test',
    }),
  )
}

/** A fetch stub that answers the admin chain: address register → activate, plus
 *  the rename / deregister / whitelist lookups. Returns the recorded calls. */
function stubGateway(
  handler?: (url: string, init?: { method?: string; body?: unknown }) => Response,
): Array<{ url: string; method: string; body: string }> {
  const calls: Array<{ url: string; method: string; body: string }> = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: unknown, init?: { method?: string; body?: unknown }) => {
      const u = String(url)
      calls.push({ url: u, method: String(init?.method ?? 'GET'), body: bodyText(init?.body) })
      if (handler) return handler(u, init)
      // default: register → activation_code; activate → raw_key; else ok
      if (u.includes('/addresses?generate_code=true')) {
        return new Response(JSON.stringify({ status: 'created', activation_code: 'ACT123' }), { status: 200 })
      }
      if (u.includes('/activate-address')) {
        return new Response(JSON.stringify({ raw_key: 'AK-NEW' }), { status: 200 })
      }
      return new Response(JSON.stringify({ status: 'ok' }), { status: 200 })
    }),
  )
  return calls
}

beforeAll(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-ops-'))
  process.env.AIMAIL_HOME = tmpHome
})

afterAll(() => {
  vi.unstubAllGlobals()
  fs.rmSync(tmpHome, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

describe('three-op node door', () => {
  test('assemble: main-agent naming lands on the contract address (J2 root)', async () => {
    seedSystem('sys-j2', 'shared-e2e.local', 'j93482')
    const calls = stubGateway()
    const r = await opAssemble({
      system_id: 'sys-j2',
      domain: 'shared-e2e.local',
      system_name: 'j93482',
      manager_address: 'mgr@ext.test',
      home: tmpHome,
    })
    // The contract address for the main agent on a shared domain is
    //   agent.<system_name>@domain  — NOT the platform's own base.
    expect(r['email']).toBe('agent.j93482@shared-e2e.local')
    expect(r['ok']).toBe(true)
    const plan = r['plan'] as Record<string, unknown>
    expect(plan['target_email']).toBe('agent.j93482@shared-e2e.local')
    expect(plan['needs_rename']).toBe(false)
    // a registration POST really went out for the contract address
    const reg = calls.find((c) => c.url.includes('/addresses?generate_code=true'))
    expect(reg, 'register call missing').toBeTruthy()
    expect(JSON.parse(reg!.body)).toMatchObject({ email: 'agent.j93482@shared-e2e.local' })
    // a binding was written for the contract address
    const bindDir = path.join(tmpHome, 'systems', 'sys-j2', cleanAddr('agent.j93482@shared-e2e.local'))
    expect(fs.existsSync(path.join(bindDir, BINDING_FILE))).toBe(true)
    const bind = JSON.parse(fs.readFileSync(path.join(bindDir, BINDING_FILE), 'utf-8')) as Record<string, unknown>
    expect(bind['email']).toBe('agent.j93482@shared-e2e.local')
    expect(bind['api_key']).toBe('AK-NEW')
    expect(bind['manager_address']).toBe('mgr@ext.test')
    // Envelope did not carry preset ⇒ binding must not carry that field (G1b counter-example)
    expect(bind['preset']).toBeUndefined()
    vi.unstubAllGlobals()
  })

  test('assemble: preset from the CLI envelope lands on the binding top level (G1b)', async () => {
    seedSystem('sys-ps', 'example.com', 'sysx')
    stubGateway()
    const r = await opAssemble({
      system_id: 'sys-ps',
      domain: 'example.com',
      system_name: 'sysx',
      manager_address: 'mgr@ext.test',
      preset: 'mail',
      home: tmpHome,
    })
    expect(r['ok']).toBe(true)
    const bindDir = path.join(tmpHome, 'systems', 'sys-ps', cleanAddr('agent.sysx@example.com'))
    const bind = JSON.parse(
      fs.readFileSync(path.join(bindDir, BINDING_FILE), 'utf-8'),
    ) as Record<string, unknown>
    // check session dimension criteria only recognizes top-level preset in binding (adapters.rs)
    expect(bind['preset']).toBe('mail')
    vi.unstubAllGlobals()
  })

  test('assemble: a requested (non-main) name registers itself directly (no rename)', async () => {
    seedSystem('sys-nm', 'example.com', 'sysx')
    stubGateway()
    const r = await opAssemble({
      system_id: 'sys-nm',
      domain: 'example.com',
      system_name: 'sysx',
      manager_address: 'mgr@ext.test',
      requested_name: 'billing',
      home: tmpHome,
    })
    expect(r['email']).toBe('billing.sysx@example.com')
    const plan = r['plan'] as Record<string, unknown>
    expect(plan['target_name']).toBe('billing')
    expect(plan['reg_as']).toBe('billing')
    expect(plan['needs_rename']).toBe(false)
    vi.unstubAllGlobals()
  })

  test('assemble: explicit email wins (repair cloud re-pair path)', async () => {
    seedSystem('sys-em', 'example.com', 'sysx')
    stubGateway()
    const r = await opAssemble({
      system_id: 'sys-em',
      domain: 'example.com',
      system_name: 'sysx',
      manager_address: 'mgr@ext.test',
      email: 'override.sysx@example.com',
      home: tmpHome,
    })
    expect(r['email']).toBe('override.sysx@example.com')
    vi.unstubAllGlobals()
  })

  test('assemble: a bad requested name is a UsageError (atext-no-dot)', async () => {
    seedSystem('sys-bad', 'example.com', 'sysx')
    stubGateway()
    await expect(
      opAssemble({
        system_id: 'sys-bad',
        domain: 'example.com',
        system_name: 'sysx',
        manager_address: 'mgr@ext.test',
        requested_name: 'has.dot',
        home: tmpHome,
      }),
    ).rejects.toBeInstanceOf(UsageError)
    vi.unstubAllGlobals()
  })

  test('assemble: no manager anywhere ⇒ loud UsageError (no empty manager)', async () => {
    const dir = path.join(tmpHome, 'systems', 'sys-nomgr')
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(
      path.join(dir, 'aimail_gateway.json'),
      JSON.stringify({ gateway_url: 'https://gw.invalid', admin_key: 'ak', domain: 'example.com', system_name: 'x' }),
    )
    stubGateway()
    await expect(
      opAssemble({ system_id: 'sys-nomgr', domain: 'example.com', system_name: 'x', home: tmpHome }),
    ).rejects.toBeInstanceOf(UsageError)
    vi.unstubAllGlobals()
  })

  test('assemble: syscfg manager_address is used when env + explicit are empty (P1 py/ts parity)', async () => {
    // seedSystem writes aimail_gateway.json carrying manager_address: 'mgr@ext.test'.
    seedSystem('sys-p1', 'example.com', 'sysx')
    // env empty: drop the single manager contract env so the syscfg value is the
    // only legitimate source (resolveManager: explicit → env → syscfg).
    const prev = process.env.AIMAIL_MANAGER_ADDRESS
    delete process.env.AIMAIL_MANAGER_ADDRESS
    const calls = stubGateway()
    try {
      // NO manager_address in args (explicit empty) ⇒ must fall back to the system config.
      const r = await opAssemble({
        system_id: 'sys-p1',
        domain: 'example.com',
        system_name: 'sysx',
        home: tmpHome,
      })
      expect(r['ok']).toBe(true)
      const reg = calls.find((c) => c.url.includes('/addresses?generate_code=true'))
      expect(reg, 'register call missing').toBeTruthy()
      // The registration POST really carried the syscfg manager (not an empty value).
      expect(JSON.parse(reg!.body)['manager_address']).toBe('mgr@ext.test')
    } finally {
      if (prev === undefined) delete process.env.AIMAIL_MANAGER_ADDRESS
      else process.env.AIMAIL_MANAGER_ADDRESS = prev
    }
    vi.unstubAllGlobals()
  })

  test('update action=set-manager: rewrites manager + persists (binding_path returned)', async () => {
    seedSystem('sys-sm', 'example.com', 'sysx')
    // a binding to update
    const bindDir = path.join(tmpHome, 'systems', 'sys-sm', cleanAddr('agent.sysx@example.com'))
    fs.mkdirSync(bindDir, { recursive: true })
    const bindPath = path.join(bindDir, BINDING_FILE)
    fs.writeFileSync(bindPath, JSON.stringify({ email: 'agent.sysx@example.com', api_key: 'AK1', manager_address: 'old@x' }))
    stubGateway()
    const r = await opUpdate({
      system_id: 'sys-sm',
      action: 'set-manager',
      email: 'agent.sysx@example.com',
      manager_address: 'new@ext.test',
    })
    expect(r['ok']).toBe(true)
    expect(typeof r['binding_path']).toBe('string')
    const bind = JSON.parse(fs.readFileSync(bindPath, 'utf-8')) as Record<string, unknown>
    expect(bind['manager_address']).toBe('new@ext.test')
    vi.unstubAllGlobals()
  })

  test('update action=prompt: rule name validated, rules persisted', async () => {
    seedSystem('sys-pr', 'example.com', 'sysx')
    const bindDir = path.join(tmpHome, 'systems', 'sys-pr', cleanAddr('agent.sysx@example.com'))
    fs.mkdirSync(bindDir, { recursive: true })
    const bindPath = path.join(bindDir, BINDING_FILE)
    fs.writeFileSync(bindPath, JSON.stringify({ email: 'agent.sysx@example.com', api_key: 'AK1' }))
    stubGateway()
    const r = await opUpdate({
      system_id: 'sys-pr',
      action: 'prompt',
      email: 'agent.sysx@example.com',
      prompt_rules: [{ name: '20_j5matrix', file: 'j5matrix', subject: ['hello'] }],
    })
    expect(r['ok']).toBe(true)
    const bind = JSON.parse(fs.readFileSync(bindPath, 'utf-8')) as Record<string, unknown>
    expect(bind['prompt_rules']).toEqual([{ name: '20_j5matrix', file: 'j5matrix', subject: ['hello'] }])
    // a bad rule name is a UsageError
    await expect(
      opUpdate({
        system_id: 'sys-pr',
        action: 'prompt',
        email: 'agent.sysx@example.com',
        prompt_rules: [{ name: 'not_a_rule', file: 'x', subject: ['a'] }],
      }),
    ).rejects.toBeInstanceOf(UsageError)
    vi.unstubAllGlobals()
  })

  test('update action=repair (system-level): fills missing fields from system cfg, idempotent', async () => {
    seedSystem('sys-rp', 'example.com', 'sysx')
    const bindDir = path.join(tmpHome, 'systems', 'sys-rp', cleanAddr('agent.sysx@example.com'))
    fs.mkdirSync(bindDir, { recursive: true })
    const bindPath = path.join(bindDir, BINDING_FILE)
    // binding missing domain/system_name/manager ⇒ repair fills them
    fs.writeFileSync(bindPath, JSON.stringify({ email: 'agent.sysx@example.com', api_key: 'AK1' }))
    stubGateway()
    const r = await opUpdate({ system_id: 'sys-rp', action: 'repair' })
    expect(r['ok']).toBe(true)
    // system-level repair prefixes each filled field with "<email>:" (python _op_update same shape)
    const filled = r['filled'] as string[]
    expect(filled).toContain('agent.sysx@example.com:domain')
    expect(filled).toContain('agent.sysx@example.com:system_name')
    expect(filled).toContain('agent.sysx@example.com:gateway_url')
    const bind = JSON.parse(fs.readFileSync(bindPath, 'utf-8')) as Record<string, unknown>
    expect(bind['domain']).toBe('example.com')
    expect(bind['system_name']).toBe('sysx')
    vi.unstubAllGlobals()
  })

  test('teardown: all modes off ⇒ idempotent no-op (no network, no target needed)', async () => {
    const r = await opTeardown({ system_id: '', mode: { unregister: false, whitelist: false, backfill: false } })
    expect(r['ok']).toBe(true)
    expect(r['actions']).toEqual([])
  })

  test('teardown: unregister + whitelist ⇒ the two actions with the python payload keys', async () => {
    seedSystem('sys-td', 'example.com', 'sysx')
    const calls = stubGateway((u, init) => {
      // api-key lookup by email → an id; domain list → a row; whitelist list → a row
      if (u.includes('/api/v1/admin/api-keys?email=')) {
        return new Response(JSON.stringify({ entries: [{ id: 55, email: 'a@x' }] }), { status: 200 })
      }
      if (u.includes('/api-keys/55')) {
        return new Response(JSON.stringify({ status: 'deleted' }), { status: 200 })
      }
      if (u.includes('/domains')) {
        return new Response(JSON.stringify({ data: [{ id: 9, domain: 'a@example.com' }] }), { status: 200 })
      }
      if (u.includes('/whitelists?domain=')) {
        return new Response(JSON.stringify({ data: [{ id: 11, domain_addr: 'a@example.com' }] }), { status: 200 })
      }
      return new Response(JSON.stringify({ status: 'ok' }), { status: 200 })
    })
    const r = await opTeardown({
      system_id: 'sys-td',
      email: 'a@example.com',
      mode: { unregister: true, whitelist: true, backfill: false },
    })
    expect(r['ok']).toBe(true)
    const actions = r['actions'] as Array<Record<string, unknown>>
    expect(actions.some((a) => 'deregister_agent_email' in a)).toBe(true)
    expect(actions.some((a) => 'cleanup_system_whitelists' in a)).toBe(true)
    // api key was actually deleted on the gateway
    expect(calls.some((c) => c.url.includes('/api-keys/55') && c.method === 'DELETE')).toBe(true)
    vi.unstubAllGlobals()
  })

  test('teardown: a mode wants a target but none given ⇒ UsageError', async () => {
    seedSystem('sys-td2', 'example.com', 'sysx')
    stubGateway()
    await expect(opTeardown({ system_id: 'sys-td2', mode: { unregister: true } })).rejects.toBeInstanceOf(UsageError)
    vi.unstubAllGlobals()
  })

  /** Stub the exists-repair chain: register → "already exists" → domains list → PUT refresh.
   *  The PUT body is what the gateway stores and the host verifies against. */
  function stubExistsRepair(email: string): void {
    stubGateway((u, init) => {
      if (u.includes('/addresses?generate_code=true')) {
        // real gateway shape: HTTP 409 + error body (client drops body's `status` key)
        return new Response(JSON.stringify({ error: 'address already exists' }), { status: 409 })
      }
      if (u.includes('/domains')) {
        return new Response(JSON.stringify({ data: [{ id: 9, domain: email }] }), { status: 200 })
      }
      if (u.includes('/system-domains/9')) {
        return new Response(JSON.stringify({ status: 'ok' }), { status: 200 })
      }
      return new Response(JSON.stringify({ status: 'ok' }), { status: 200 })
    })
  }

  test('assemble (exists): local binding secret is the single source — never minted, synced to gateway (J4e)', async () => {
    seedSystem('sys-j4e', 'example.com', 'sysx')
    const email = 'agent.sysx@example.com'
    const bindDir = path.join(tmpHome, 'systems', 'sys-j4e', cleanAddr(email))
    fs.mkdirSync(bindDir, { recursive: true })
    const bindPath = path.join(bindDir, BINDING_FILE)
    const LOCAL_SECRET = 'a'.repeat(64)
    fs.writeFileSync(bindPath, JSON.stringify({ email, api_key: 'AK1', webhook_secret: LOCAL_SECRET }))
    stubExistsRepair(email)
    const r = await opAssemble({
      system_id: 'sys-j4e',
      domain: 'example.com',
      system_name: 'sysx',
      manager_address: 'mgr@ext.test',
      email,
      local_webhook_url: 'http://127.0.0.1:9099/wh',
      webhook_secret: LOCAL_SECRET,
      home: tmpHome,
    })
    expect(r['ok']).toBe(true)
    // the gateway PUT carried the LOCAL secret (not a freshly minted one)
    const bind = JSON.parse(fs.readFileSync(bindPath, 'utf-8')) as Record<string, unknown>
    expect(bind['webhook_secret']).toBe(LOCAL_SECRET)
    vi.unstubAllGlobals()
  })

  test('assemble (exists): local secret missing + caller value ⇒ backfilled into the binding (J4e backfill)', async () => {
    seedSystem('sys-j4e2', 'example.com', 'sysx')
    const email = 'agent.sysx@example.com'
    const bindDir = path.join(tmpHome, 'systems', 'sys-j4e2', cleanAddr(email))
    fs.mkdirSync(bindDir, { recursive: true })
    const bindPath = path.join(bindDir, BINDING_FILE)
    const CALLER_SECRET = 'b'.repeat(64)
    // binding exists (has api_key) but NO webhook_secret — the J4e split state
    fs.writeFileSync(bindPath, JSON.stringify({ email, api_key: 'AK1' }))
    stubExistsRepair(email)
    const r = await opAssemble({
      system_id: 'sys-j4e2',
      domain: 'example.com',
      system_name: 'sysx',
      manager_address: 'mgr@ext.test',
      email,
      local_webhook_url: 'http://127.0.0.1:9099/wh',
      webhook_secret: CALLER_SECRET,
      home: tmpHome,
    })
    expect(r['ok']).toBe(true)
    const bind = JSON.parse(fs.readFileSync(bindPath, 'utf-8')) as Record<string, unknown>
    // the caller's secret was carried back into the binding (single source restored)
    expect(bind['webhook_secret']).toBe(CALLER_SECRET)
    vi.unstubAllGlobals()
  })

  test('update action=webhook-secret: value-less call ⇒ ensure (reuse local / mint + write back) (J4e CLI contract)', async () => {
    seedSystem('sys-j4e3', 'example.com', 'sysx')
    const email = 'agent.sysx@example.com'
    const bindDir = path.join(tmpHome, 'systems', 'sys-j4e3', cleanAddr(email))
    fs.mkdirSync(bindDir, { recursive: true })
    const bindPath = path.join(bindDir, BINDING_FILE)
    fs.writeFileSync(bindPath, JSON.stringify({ email, api_key: 'AK1' }))
    stubGateway()
    // no webhook_secret in the args — the CLI repair.rs:872 contract (ensure semantics)
    const r = await opUpdate({
      system_id: 'sys-j4e3',
      action: 'webhook-secret',
      email,
    })
    expect(r['ok']).toBe(true)
    const bind = JSON.parse(fs.readFileSync(bindPath, 'utf-8')) as Record<string, unknown>
    // a fresh 64-hex secret was provisioned and written back
    expect(String(bind['webhook_secret'] ?? '')).toMatch(/^[0-9a-f]{64}$/)
    vi.unstubAllGlobals()
  })
})

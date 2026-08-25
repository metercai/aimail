/**
 * Task 4 contract tests: X-AIMail-Agent header, whitelist `whitelisted`
 * field, contact search `results` + ambiguous semantics.
 * Fetch is stubbed to return the REAL gateway response shapes
 * (verified against aimail-gateway src/core/api/http.rs):
 *   - GET /whitelists/check  → {whitelisted, domain_addr, value, direction}
 *   - GET /contacts?name=    → {results: [{address, profile}]}
 *   - GET /contacts/:addr    → {address, profile} (404 if missing)
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { sendMail, manageContacts, contactProfile, setAgentIdentity } from '../src/tools.js'
import { GatewayClient } from '../src/gateway.js'
import { cleanAddr } from '../src/config.js'

const SYSTEM_ID = 'system-test'
const EMAIL = 'agent1@token.tm'
const FAKE_GATEWAY = 'http://127.0.0.1:9'

let home: string

async function writeConfig(extra: Record<string, unknown> = {}): Promise<void> {
  const cfg = {
    email: EMAIL, gateway_url: FAKE_GATEWAY, domain: 'token.tm',
    system_id: SYSTEM_ID, api_key: 'deadbeef', session_id: 'session-1',
    manager_address: 'boss@token.tm',
    ...extra,
  }
  const dir = path.join(home, 'systems', SYSTEM_ID, cleanAddr(EMAIL))
  await fs.mkdir(dir, { recursive: true, mode: 0o700 })
  await fs.writeFile(path.join(dir, 'agentmail.json'), JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 })
}

type Route = string
function stubFetch(routes: Record<Route, [number, Record<string, unknown>]>): {
  calls: Array<{ url: string; body?: Record<string, unknown> }>
  restore: () => void
} {
  const calls: Array<{ url: string; body?: Record<string, unknown> }> = []
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url)
    calls.push({
      url: u,
      body: init?.body ? JSON.parse(new TextDecoder().decode(init.body as ArrayBuffer)) : undefined,
    })
    for (const [route, [status, payload]] of Object.entries(routes)) {
      if (u.includes(route)) {
        return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
      }
    }
    return new Response('{}', { status: 200 })
  })
  vi.stubGlobal('fetch', mock)
  return { calls, restore: () => vi.unstubAllGlobals() }
}

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'amail-t4-'))
  process.env.AIMAIL_HOME = home
})

beforeEach(async () => {
  await fs.rm(home, { recursive: true, force: true })
  await fs.mkdir(home, { recursive: true })
  process.env.AIMAIL_HOME = home
  await writeConfig()
  setAgentIdentity('dsh/9.9.9')
})

afterAll(async () => {
  vi.unstubAllGlobals()
  await fs.rm(home, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

const ctx = { systemId: SYSTEM_ID, email: EMAIL }

describe('D4: X-AIMail-Agent header (new name)', () => {
  it('sendMail sends X-AIMail-Agent (not X-Agentmail-Agent)', async () => {
    const { calls, restore } = stubFetch({
      '/api/v1/send': [200, { email_id: 'e1', status: 200 }],
    })
    await sendMail(ctx, { to: 'a@x', subject: 'hi', body: 'yo' })
    restore()
    const send = calls.find(c => c.url.includes('/api/v1/send'))!
    const hdrs = send!.body!.headers as Record<string, string>
    expect(hdrs['X-AIMail-Agent']).toBe('dsh/9.9.9')
    expect(hdrs['X-Agentmail-Agent']).toBeUndefined()
  })
})

describe('D6: manage_contacts check reads gateway `whitelisted` field', () => {
  it('whitelisted=true → in_contacts=true with entry direction', async () => {
    const { restore } = stubFetch({
      '/api/v1/whitelists/check': [200, {
        whitelisted: true, domain_addr: EMAIL, value: 'a@x', direction: 'from',
      }],
    })
    const r = await manageContacts(ctx, { action: 'check', address: 'a@x', direction: 'from' })
    restore()
    expect(r.success).toBe(true)
    expect(r.in_contacts).toBe(true)
    expect(r.direction).toBe('from')
  })

  it('whitelisted=false → in_contacts=false, requested direction echoed', async () => {
    const { restore } = stubFetch({
      '/api/v1/whitelists/check': [200, {
        whitelisted: false, domain_addr: EMAIL, value: 'b@x', direction: 'to',
      }],
    })
    const r = await manageContacts(ctx, { action: 'check', address: 'b@x', direction: 'to' })
    restore()
    expect(r.success).toBe(true)
    expect(r.in_contacts).toBe(false)
    expect(r.direction).toBe('to')
  })

  it('non-200 → in_contacts=false (no info leakage)', async () => {
    const { restore } = stubFetch({
      '/api/v1/whitelists/check': [403, { error: 'forbidden' }],
    })
    const r = await manageContacts(ctx, { action: 'check', address: 'c@x' })
    restore()
    expect(r.success).toBe(true)
    expect(r.in_contacts).toBe(false)
  })
})

describe('D7: contact_profile name search reads `results` + ambiguous', () => {
  it('single match → address + profile', async () => {
    const { restore } = stubFetch({
      '/api/v1/contacts?': [200, {
        results: [{ address: 'alice@x', profile: 'Alice — engineering lead' }],
      }],
    })
    const r = await contactProfile(ctx, { name: 'Alice' })
    restore()
    expect(r.success).toBe(true)
    expect(r.address).toBe('alice@x')
    expect(r.profile).toBe('Alice — engineering lead')
  })

  it('multiple matches → ambiguous + candidates', async () => {
    const { restore } = stubFetch({
      '/api/v1/contacts?': [200, {
        results: [
          { address: 'a1@x', profile: 'p1' },
          { address: 'a2@x', profile: 'p2' },
        ],
      }],
    })
    const r = await contactProfile(ctx, { name: 'Alice' })
    restore()
    expect(r.success).toBe(true)
    expect(r.ambiguous).toBe(true)
    expect(r.candidates).toEqual(['a1@x', 'a2@x'])
  })

  it('no match → profile null + searched_name', async () => {
    const { restore } = stubFetch({
      '/api/v1/contacts?': [200, { results: [] }],
    })
    const r = await contactProfile(ctx, { name: 'Nobody' })
    restore()
    expect(r.success).toBe(true)
    expect(r.profile).toBeNull()
    expect(r.searched_name).toBe('Nobody')
  })

  it('address lookup 404 → profile null (not an error)', async () => {
    const { restore } = stubFetch({
      '/api/v1/contacts/': [404, { error: 'contact not found' }],
    })
    const r = await contactProfile(ctx, { address: 'ghost@x' })
    restore()
    expect(r.success).toBe(true)
    expect(r.profile).toBeNull()
  })

  it('address lookup 200 → profile value', async () => {
    const { restore } = stubFetch({
      '/api/v1/contacts/': [200, { address: 'bob@x', profile: 'Bob — ops' }],
    })
    const r = await contactProfile(ctx, { address: 'bob@x' })
    restore()
    expect(r.success).toBe(true)
    expect(r.address).toBe('bob@x')
    expect(r.profile).toBe('Bob — ops')
  })
})

describe('B1: getContactProfiles batch endpoint', () => {
  const client = new GatewayClient(FAKE_GATEWAY, 'deadbeef')

  it('sends comma-joined addresses; maps the real gateway response shapes', async () => {
    const { calls, restore } = stubFetch({
      '/api/v1/contacts?': [200, {
        my_profile: { address: EMAIL, profile: 'Agent One — persona' },
        sender_profile: { 'a@x': 'A — sender' },
        recipients_profile: { 'b@x': 'B — recipient' },
        results: [],
      }],
    })
    const p = await client.getContactProfiles(['a@x', 'b@x'])
    restore()
    expect(p.my_profile).toEqual({ address: EMAIL, profile: 'Agent One — persona' })
    expect(p.sender_profile).toEqual({ 'a@x': 'A — sender' })
    expect(p.recipients_profile).toEqual({ 'b@x': 'B — recipient' })
    const call = calls.find(c => c.url.includes('/api/v1/contacts?'))
    expect(call?.url).toContain('addresses=a%40x%2Cb%40x')
  })

  it('empty address list → empty shapes, no request', async () => {
    const { calls, restore } = stubFetch({})
    const p = await client.getContactProfiles(['   ', '', '  '])
    restore()
    expect(p).toEqual({ my_profile: null, sender_profile: {}, recipients_profile: {} })
    expect(calls.length).toBe(0)
  })

  it('non-200 → empty shapes (caller treats as no profiles available)', async () => {
    const { restore } = stubFetch({
      '/api/v1/contacts?': [500, { error: 'internal' }],
    })
    const p = await client.getContactProfiles(['a@x'])
    restore()
    expect(p).toEqual({ my_profile: null, sender_profile: {}, recipients_profile: {} })
  })

  it('null my_profile → null (no approved persona yet)', async () => {
    const { restore } = stubFetch({
      '/api/v1/contacts?': [200, {
        my_profile: null,
        sender_profile: {},
        recipients_profile: {},
        results: [],
      }],
    })
    const p = await client.getContactProfiles(['a@x'])
    restore()
    expect(p.my_profile).toBeNull()
  })
})

import { describe, expect, test, beforeAll, afterAll } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  activateSystem,
  createAgentAdminKey,
  detectSystemForHome,
  installSystem,
  saveSystemConfig,
  readSystemConfig,
  listSystemDirs,
} from '../src/index.js'
import type { AdminClientLike } from '../src/index.js'

let tmpHome: string

beforeAll(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-inst-'))
  process.env.AIMAIL_HOME = tmpHome
})

afterAll(() => {
  fs.rmSync(tmpHome, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

const cfgPath = (sid: string) =>
  path.join(tmpHome, 'systems', sid, 'aimail_gateway.json')

/** Route mock: activate + api-keys endpoints with scripted bodies. */
function mockTransport(
  activateBody: Record<string, unknown>,
  keyBody: Record<string, unknown> = { raw_key: 'agent-admin-1' },
): AdminClientLike {
  const calls: Array<{ method: string; path: string; body?: unknown }> = []
  const transport: AdminClientLike = {
    request: async (method, p, body) => {
      calls.push({ method, path: p, body })
      if (p.endsWith('/activate-system')) {
        return { status: 200, ...activateBody }
      }
      if (p.endsWith('/admin/api-keys')) {
        return { status: 200, ...keyBody }
      }
      return { status: 404, error: `unexpected ${method} ${p}` }
    },
  }
  return Object.assign(transport, { _calls: calls })
}

describe('install core (parity with pysdk install_core)', () => {
  test('saveSystemConfig: writes canonical file with python field set', async () => {
    const p = await saveSystemConfig({
      gateway_url: 'https://gw.invalid',
      admin_key: 'ak-1',
      system_id: 'sys-save',
      domain: 'example.com',
      system_home: '/home/x/.hermes',
    })
    expect(p).toBe(cfgPath('sys-save'))
    const cfg = JSON.parse(fs.readFileSync(p, 'utf-8'))
    expect(cfg).toEqual({
      gateway_url: 'https://gw.invalid',
      admin_key: 'ak-1',
      system_id: 'sys-save',
      system_name: '',
      save_raw_snapshots: true, // default true — python parity
      domain: 'example.com',
      system_home: '/home/x/.hermes',
    })
    expect((fs.statSync(p).mode & 0o777)).toBe(0o600)
  })

  test('activateSystem: raw_key presence is the authoritative gate', async () => {
    const ok = mockTransport({
      raw_key: 'sk-act',
      system_id: 'sys-act',
      domain: 'example.com',
      system_name: 'act',
    })
    const r = await activateSystem({
      gatewayUrl: 'https://gw.invalid',
      code: 'prod-1',
      transport: ok,
    })
    expect(r.success).toBe(true)
    expect(r.raw_key).toBe('sk-act')
    expect(r.system_id).toBe('sys-act')

    // server has NO success field and no raw_key → fail (python lesson)
    const bad = mockTransport({ system_id: 'sys-act' } as Record<string, unknown>)
    const r2 = await activateSystem({
      gatewayUrl: 'https://gw.invalid',
      code: 'prod-1',
      transport: bad,
    })
    expect(r2.success).toBe(false)
    expect(r2.error).toMatch(/Activation failed/)
  })

  test('installSystem path B: activate → config write → agent_admin downgrade', async () => {
    const t = mockTransport({
      raw_key: 'sys-key-1',
      system_id: 'sys-b',
      domain: 'example.com',
      system_name: 'beta',
    })
    const r = await installSystem({
      gatewayUrl: 'https://gw.invalid',
      productCode: 'prod-b',
      managerAddress: 'm@example.com',
      systemHome: '/home/u/.pi',
      transport: t,
    })
    expect(r.success).toBe(true)
    expect(r.path).toBe('activation')
    expect(r.system_id).toBe('sys-b')
    expect(r.admin_key).toBe('agent-admin-1') // downgraded
    const cfg = JSON.parse(fs.readFileSync(cfgPath('sys-b'), 'utf-8'))
    expect(cfg.admin_key).toBe('agent-admin-1') // config holds the agent key
    expect(cfg.domain).toBe('example.com')
    expect(cfg.system_home).toBe('/home/u/.pi')
    expect(cfg.manager_address).toBe('m@example.com')
    const calls = (t as unknown as { _calls: Array<{ path: string }> })._calls
    expect(calls.some(c => c.path.endsWith('/admin/api-keys'))).toBe(true)
  })

  test('installSystem path B: activate failure surfaces the server error', async () => {
    const t = mockTransport({ error: 'code already claimed', status: 409 } as Record<string, unknown>)
    const r = await installSystem({
      gatewayUrl: 'https://gw.invalid',
      productCode: 'prod-old',
      transport: t,
    })
    expect(r.success).toBe(false)
    expect(r.error).toMatch(/already claimed/)
  })

  test('installSystem path A: reset keeps business fields and prev-only keys', async () => {
    // Pre-existing config with business + prev-only fields.
    const sid = 'sys-a'
    fs.mkdirSync(path.join(tmpHome, 'systems', sid), { recursive: true })
    fs.writeFileSync(
      cfgPath(sid),
      JSON.stringify({
        gateway_url: 'https://old.invalid',
        admin_key: 'old-key',
        system_id: sid,
        domain: 'custom.example.com',
        system_name: 'alpha',
        manager_address: 'm@example.com',
        bridge_port: 38081, // prev-only business field
        default_agent_name: 'agent',
      }),
    )
    const t = mockTransport({}, { raw_key: 'agent-admin-a' })
    const r = await installSystem({
      gatewayUrl: 'https://new.invalid',
      systemId: sid,
      adminKey: 'sys-key-a',
      transport: t,
    })
    expect(r.success).toBe(true)
    expect(r.path).toBe('admin_key')
    const cfg = JSON.parse(fs.readFileSync(cfgPath(sid), 'utf-8'))
    expect(cfg.gateway_url).toBe('https://new.invalid')
    expect(cfg.admin_key).toBe('agent-admin-a')
    expect(cfg.domain).toBe('custom.example.com') // inherited
    expect(cfg.system_name).toBe('alpha') // inherited
    expect(cfg.bridge_port).toBe(38081) // prev-only preserved back
    expect(cfg.default_agent_name).toBe('agent')
  })

  test('installSystem: missing both credentials → clear error', async () => {
    const r = await installSystem({ gatewayUrl: 'https://gw.invalid' })
    expect(r.success).toBe(false)
    expect(r.error).toBe('Either admin_key or product_code is required')
  })

  test('createAgentAdminKey: failure keeps the original system key', async () => {
    const sid = 'sys-keep'
    fs.mkdirSync(path.join(tmpHome, 'systems', sid), { recursive: true })
    fs.writeFileSync(cfgPath(sid), JSON.stringify({ admin_key: 'sys-keep-key' }))
    const t = mockTransport({ error: 'denied' } as Record<string, unknown>, { error: 'denied' })
    const key = await createAgentAdminKey({
      gatewayUrl: 'https://gw.invalid',
      systemAdminKey: 'sys-keep-key',
      systemId: sid,
      transport: t,
    })
    expect(key).toBe('sys-keep-key') // original kept
    const cfg = JSON.parse(fs.readFileSync(cfgPath(sid), 'utf-8'))
    expect(cfg.admin_key).toBe('sys-keep-key') // untouched
  })

  test('detectSystemForHome: unique owner returns sid, ambiguous returns empty', async () => {
    const mk = (sid: string, systemHome: string) => {
      fs.mkdirSync(path.join(tmpHome, 'systems', sid), { recursive: true })
      fs.writeFileSync(cfgPath(sid), JSON.stringify({ system_home: systemHome }))
    }
    mk('sys-hermes', '/home/u/.hermes')
    mk('sys-dsh', '/home/u/.dsh')
    expect(await detectSystemForHome('/home/u/.dsh')).toBe('sys-dsh')
    expect(await detectSystemForHome('/home/u/.hermes')).toBe('sys-hermes')
    expect(await detectSystemForHome('/home/u/.openclaw')).toBe('') // none
    // ambiguity: second system owning the same home
    mk('sys-dsh2', '/home/u/.dsh')
    expect(await detectSystemForHome('/home/u/.dsh')).toBe('')
    // listSystemDirs still enumerates all (no double-registration)
    expect((await listSystemDirs()).sort()).toEqual(
      ['sys-save', 'sys-b', 'sys-a', 'sys-keep', 'sys-hermes', 'sys-dsh', 'sys-dsh2'].sort(),
    )
    // readSystemConfig works on the canonical file name
    expect((await readSystemConfig('sys-hermes')).system_home).toBe('/home/u/.hermes')
  })
})

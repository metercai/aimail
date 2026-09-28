import { describe, expect, test, beforeAll, afterAll, vi } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { AIMAIL_HOME, listSystemDirs, readSystemConfig, emailForAgent, autoBind, ensureBindingWebhookSecret } from '../src/index.js'
import { BINDING_FILE, INBOUND_PATH } from '../src/contract.js'

let tmpHome: string

/** Bodies arrive as Uint8Array from the real GatewayClient (fetch accepts bytes). */
function bodyText(raw: unknown): string {
  if (typeof raw === 'string') return raw
  if (raw instanceof Uint8Array) return Buffer.from(raw).toString('utf-8')
  if (raw instanceof ArrayBuffer) return Buffer.from(new Uint8Array(raw)).toString('utf-8')
  return ''
}

beforeAll(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-ab-'))
  process.env.AIMAIL_HOME = tmpHome
})

afterAll(() => {
  fs.rmSync(tmpHome, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

describe('auto-bind helpers', () => {
  test('emailForAgent mirrors python: default alias → agent, shared-domain suffix', () => {
    expect(emailForAgent('default', 'example.com')).toBe('agent@example.com')
    expect(emailForAgent('main', 'example.com', '', ['main'])).toBe('agent@example.com')
    expect(emailForAgent('pi', 'example.com', 'xianlin')).toBe('pi.xianlin@example.com')
    expect(emailForAgent('weird name', 'example.com')).toMatch(/^weird_name@example\.com$/)
  })

  test('listSystemDirs: empty home → [], after seeding → the sid', async () => {
    expect(await listSystemDirs()).toEqual([])
    const sidDir = path.join(tmpHome, 'systems', 'sys-x')
    fs.mkdirSync(sidDir, { recursive: true })
    fs.writeFileSync(
      path.join(sidDir, 'aimail_gateway.json'),
      JSON.stringify({
        gateway_url: 'https://gw.invalid',
        admin_key: 'ak-test',
        domain: 'example.com',
        system_name: 'x',
        manager_address: 'm@example.com',
      }),
    )
    expect(await listSystemDirs()).toEqual(['sys-x'])
    const cfg = await readSystemConfig('sys-x')
    expect(cfg.domain).toBe('example.com')
  })

  test('autoBind exists-guard: local secret is bound and synced to the registration (J4 401 root cause)', async () => {
    const email = 'agent@example.com'
    const dirName = email.replace(/[^\w.-]/g, '_') // cleanAddr: @ → _
    const bindingDir = path.join(tmpHome, 'systems', 'sys-x', dirName)
    fs.mkdirSync(bindingDir, { recursive: true })
    const bindingPath = path.join(bindingDir, BINDING_FILE)
    // 真实绑定文件恒带本地 webhook_url(桥路由目标) —— 但**没有** secret
    // (升级前落的绑定形态, 正是 J4 的 401 现场: 插件按 '' 验签 ⇒ bad_signature)。
    fs.writeFileSync(
      bindingPath,
      JSON.stringify({
        email,
        api_key: 'have-key',
        system_id: 'sys-x',
        webhook_url: 'http://127.0.0.1:18789' + INBOUND_PATH,
      }),
    )
    const calls: Array<{ url: string; method: string; body: string }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: unknown, init?: { method?: string; body?: unknown }) => {
        const u = String(url)
        calls.push({ url: u, method: String(init?.method ?? 'GET'), body: bodyText(init?.body) })
        if (u.includes('/domains')) {
          return new Response(JSON.stringify({ data: [{ id: 7, domain: email }] }), { status: 200 })
        }
        return new Response('{"status":"ok"}', { status: 200 })
      }),
    )
    const r = await autoBind({
      systemId: 'sys-x',
      email,
      webhookUrl: 'http://127.0.0.1:18789' + INBOUND_PATH,
    })
    expect(r.exists).toBe(true)
    expect(r.api_key).toBe('have-key') // returns the existing binding's key

    // ① the binding now carries a self-provisioned 64-hex secret, file stays 0600
    const written = JSON.parse(fs.readFileSync(bindingPath, 'utf-8')) as Record<string, unknown>
    const secret = String(written['webhook_secret'] ?? '')
    expect(secret).toMatch(/^[0-9a-f]{64}$/)
    expect(r.secret_provisioned).toBe(true)
    expect((fs.statSync(bindingPath).mode & 0o777).toString(8)).toBe('600')

    // ② the SAME secret was pushed to the cloud registration ⇒ the gateway signs with
    //    exactly what the plugin verifies with (no more bad_signature 401).
    const put = calls.find(c => c.url.includes('/api/v1/admin/system-domains/7'))
    expect(put?.method).toBe('PUT')
    expect(JSON.parse(put?.body ?? '{}')).toEqual({
      webhook_url: 'http://127.0.0.1:18789' + INBOUND_PATH,
      webhook_secret: secret,
    })
    expect(r.secret_synced).toBe(true)

    // ③ 铁律(2026-08-18): the bridge route is still re-upserted.
    expect(calls.some(c => c.url.endsWith('/api/v1/routes'))).toBe(true)
    vi.unstubAllGlobals()
  })

  test('autoBind exists-guard: an existing secret is reused, never overwritten', async () => {
    const email = 'keep@example.com'
    const bindingDir = path.join(tmpHome, 'systems', 'sys-x', email.replace(/[^\w.-]/g, '_'))
    fs.mkdirSync(bindingDir, { recursive: true })
    const bindingPath = path.join(bindingDir, BINDING_FILE)
    const before = JSON.stringify({
      email,
      api_key: 'k3',
      webhook_secret: 'a'.repeat(64),
      system_id: 'sys-x',
      webhook_url: 'http://127.0.0.1:9101' + INBOUND_PATH,
    })
    fs.writeFileSync(bindingPath, before)
    const puts: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: unknown, init?: { method?: string; body?: unknown }) => {
        if (String(url).includes('/domains')) {
          return new Response(JSON.stringify({ data: [{ id: 9, domain: email }] }), { status: 200 })
        }
        puts.push(bodyText(init?.body))
        return new Response('{"status":"ok"}', { status: 200 })
      }),
    )
    const r = await autoBind({ systemId: 'sys-x', email })
    expect(r.exists).toBe(true)
    expect(r.secret_provisioned).toBe(false)
    expect(fs.readFileSync(bindingPath, 'utf-8')).toBe(before) // byte-identical: no clobber
    // the reused secret (not a fresh one) is what gets registered
    expect(puts.some(b => b.includes('a'.repeat(64)))).toBe(true)
    vi.unstubAllGlobals()
  })

  test('autoBind exists-guard: no local webhook anywhere → secret kept local, no route/registration write', async () => {
    const email = 'nohttp@example.com'
    const bindingDir = path.join(tmpHome, 'systems', 'sys-x', email.replace(/[^\w.-]/g, '_'))
    fs.mkdirSync(bindingDir, { recursive: true })
    const bindingPath = path.join(bindingDir, BINDING_FILE)
    fs.writeFileSync(
      bindingPath,
      JSON.stringify({ email, api_key: 'k2', system_id: 'sys-x' }),
    )
    const spy = vi.fn(async () => new Response('{"status":"ok"}', { status: 200 }))
    vi.stubGlobal('fetch', spy)
    const r = await autoBind({ systemId: 'sys-x', email })
    expect(r.exists).toBe(true)
    expect(spy).not.toHaveBeenCalled() // 无本地端点 ⇒ 无可信路由目标, 不做无意义写入
    // 但 secret 仍就地自供(本地真源), 并且**不静默**: 结果里给出未同步的原因
    expect(String((JSON.parse(fs.readFileSync(bindingPath, 'utf-8')) as Record<string, unknown>)['webhook_secret'])).toMatch(
      /^[0-9a-f]{64}$/,
    )
    expect(r.secret_synced).toBe(false)
    expect(r.secret_detail).toBeTruthy()
    vi.unstubAllGlobals()
  })

  test('ensureBindingWebhookSecret: idempotent, api_key-adjacent, never overwrites', async () => {
    const p = path.join(tmpHome, 'unit', BINDING_FILE)
    const r1 = await ensureBindingWebhookSecret({ email: 'u@x', api_key: 'AK', domain: 'x' }, p)
    expect(r1.provisioned).toBe(true)
    expect(r1.secret).toMatch(/^[0-9a-f]{64}$/)
    const txt = fs.readFileSync(p, 'utf-8')
    expect(txt.endsWith('\n')).toBe(true)
    const d = JSON.parse(txt) as Record<string, unknown>
    expect(d['webhook_secret']).toBe(r1.secret)
    expect(Object.keys(d).indexOf('webhook_secret')).toBe(Object.keys(d).indexOf('api_key') + 1)
    expect((fs.statSync(p).mode & 0o777).toString(8)).toBe('600')
    const r2 = await ensureBindingWebhookSecret(d, p)
    expect(r2).toEqual({ secret: r1.secret, provisioned: false })
    expect(fs.readFileSync(p, 'utf-8')).toBe(txt)
  })

  test('autoBind on an empty machine with no system config fails with guidance', async () => {
    await expect(
      autoBind({ systemId: 'sys-none', email: 'agent@example.com', webhookUrl: 'http://127.0.0.1:9/x', webhookSecret: 's' }),
    ).rejects.toThrow(/init|install|环境/)
  })
})

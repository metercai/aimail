import { describe, expect, test, beforeAll, afterAll, vi } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { AIMAIL_HOME, listSystemDirs, readSystemConfig, emailForAgent, autoBind } from '../src/index.js'

let tmpHome: string

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

  test('autoBind exists-guard: binding reused AND bridge route re-upserted (铁律)', async () => {
    const email = 'agent@example.com'
    const dirName = email.replace(/[^\w.-]/g, '_') // cleanAddr: @ → _
    const bindingDir = path.join(tmpHome, 'systems', 'sys-x', dirName)
    fs.mkdirSync(bindingDir, { recursive: true })
    // 真实 agentmail.json 恒带本地 webhook_url(桥路由目标)
    fs.writeFileSync(
      path.join(bindingDir, 'agentmail.json'),
      JSON.stringify({
        email,
        api_key: 'have-key',
        system_id: 'sys-x',
        webhook_url: 'http://127.0.0.1:9101/aimail/inbound',
      }),
    )
    const calls: Array<{ url: string; body: string }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: unknown, init?: { body?: unknown }) => {
        calls.push({ url: String(url), body: String(init?.body ?? '') })
        return new Response('{"status":"ok"}', { status: 200 })
      }),
    )
    const r = await autoBind({
      systemId: 'sys-x',
      email,
      webhookUrl: 'http://127.0.0.1:9101/aimail/inbound',
      webhookSecret: 's',
    })
    expect(r.exists).toBe(true)
    expect(r.api_key).toBe('have-key') // returns the existing binding's key
    // 铁律(2026-08-18): 有 bridge 时路由必须存在 —— 桥的健康检查会在目标连续不可达
    // (30s×6)后删路由, 故 exists 短路分支**也必须**幂等 upsert(否则删后永久断链)。
    expect(calls).toHaveLength(1)
    expect(calls[0]!.url).toContain('/api/v1/routes')
    expect(JSON.parse(calls[0]!.body)).toEqual({
      email,
      host: 'http://127.0.0.1:9101/aimail/inbound',
      port: 80,
    })
    vi.unstubAllGlobals()
  })

  test('autoBind exists-guard: no local webhook anywhere → no route call at all', async () => {
    const email = 'nohttp@example.com'
    const bindingDir = path.join(tmpHome, 'systems', 'sys-x', email.replace(/[^\w.-]/g, '_'))
    fs.mkdirSync(bindingDir, { recursive: true })
    fs.writeFileSync(
      path.join(bindingDir, 'agentmail.json'),
      JSON.stringify({ email, api_key: 'k2', system_id: 'sys-x' }),
    )
    const spy = vi.fn(async () => new Response('{"status":"ok"}', { status: 200 }))
    vi.stubGlobal('fetch', spy)
    const r = await autoBind({ systemId: 'sys-x', email })
    expect(r.exists).toBe(true)
    expect(spy).not.toHaveBeenCalled() // 无本地端点 ⇒ 无可信路由目标, 不做无意义写入
    vi.unstubAllGlobals()
  })

  test('autoBind on an empty machine with no system config fails with guidance', async () => {
    await expect(
      autoBind({ systemId: 'sys-none', email: 'agent@example.com', webhookUrl: 'http://127.0.0.1:9/x', webhookSecret: 's' }),
    ).rejects.toThrow(/init|install|环境/)
  })
})

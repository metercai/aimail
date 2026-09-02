import { describe, expect, test, beforeAll, afterAll } from 'vitest'
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
      path.join(sidDir, 'agentmail_gateway.json'),
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

  test('autoBind exists-guard: existing binding short-circuits with zero network', async () => {
    const email = 'agent@example.com'
    const dirName = email.replace(/[^\w.-]/g, '_') // cleanAddr: @ → _
    const bindingDir = path.join(tmpHome, 'systems', 'sys-x', dirName)
    fs.mkdirSync(bindingDir, { recursive: true })
    fs.writeFileSync(
      path.join(bindingDir, 'agentmail.json'),
      JSON.stringify({ email, api_key: 'have-key', system_id: 'sys-x' }),
    )
    const r = await autoBind({ systemId: 'sys-x', email, webhookUrl: 'http://127.0.0.1:9/aimail/inbound', webhookSecret: 's' })
    expect(r.exists).toBe(true)
    expect(r.api_key).toBe('have-key') // returns the existing binding's key
  })

  test('autoBind on an empty machine with no system config fails with guidance', async () => {
    await expect(
      autoBind({ systemId: 'sys-none', email: 'agent@example.com', webhookUrl: 'http://127.0.0.1:9/x', webhookSecret: 's' }),
    ).rejects.toThrow(/init|install|环境/)
  })
})

/**
 * B3 regression (QA findings B3, M2 2026-11-21): the plugin's register() path
 * must wire the host's /hooks/agent endpoint (hooks.token + hooks.enabled=true)
 * for a pure-SSE host — the CLI `aimail register`/`status` commands do this,
 * but the SSE profile registers NO CLI commands, so without the register()
 * call a pure-SSE host's inbound dispatch 404s (endpoint absent: the host's
 * resolveHooksConfig returns null when hooks.enabled !== true ⇒ fall-through)
 * and pulled mail is never acked.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

describe('openclaw-aimail B3 hooks wiring in register()', () => {
  let tmpHome: string
  let configPath: string
  const prevHome = process.env.HOME
  const prevTransport = process.env.AIMAIL_TRANSPORT

  beforeEach(() => {
    tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-b3-'))
    fs.mkdirSync(path.join(tmpHome, '.openclaw'), { recursive: true })
    configPath = path.join(tmpHome, '.openclaw', 'openclaw.json')
    process.env.HOME = tmpHome
    process.env.AIMAIL_TRANSPORT = 'sse'
  })
  afterEach(() => {
    if (prevHome === undefined) delete process.env.HOME
    else process.env.HOME = prevHome
    if (prevTransport === undefined) delete process.env.AIMAIL_TRANSPORT
    else process.env.AIMAIL_TRANSPORT = prevTransport
    fs.rmSync(tmpHome, { recursive: true, force: true })
  })

  const readCfg = () => JSON.parse(fs.readFileSync(configPath, 'utf8')) as Record<string, any>

  it('SSE register() creates hooks.token + hooks.enabled=true on a fresh host config', async () => {
    fs.writeFileSync(configPath, JSON.stringify({
      gateway: { mode: 'local', bind: 'loopback', port: 18789 },
      agents: { defaults: { sessionStore: { agentId: 'main' } } },
    }), { mode: 0o600 })

    const mod = (await import('../../src/openclaw/index.js')) as {
      default?: { register?: (api: unknown) => void }
    }
    const api = {
      registerTool() { return 1 },
      registerHttpRoute() {},
      registerCommand() {},
    }
    mod.default!.register!(api)
    // settle async register effects
    for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 10))

    const cfg = readCfg()
    expect(cfg.hooks?.enabled).toBe(true)
    expect(typeof cfg.hooks?.token).toBe('string')
    expect(String(cfg.hooks?.token).length).toBeGreaterThanOrEqual(32)
  })

  it('register() is idempotent: keeps an existing token, never clobbers enabled=false', async () => {
    // (a) existing token kept
    fs.writeFileSync(configPath, JSON.stringify({
      gateway: { mode: 'local', bind: 'loopback', port: 18789 },
      hooks: { enabled: true, token: 'existing-' + 'x'.repeat(40) },
      agents: { defaults: { sessionStore: { agentId: 'main' } } },
    }), { mode: 0o600 })
    let mod = (await import('../../src/openclaw/index.js')) as {
      default?: { register?: (api: unknown) => void }
    }
    mod.default!.register!({ registerTool: () => 1, registerHttpRoute() {}, registerCommand() {} })
    for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 10))
    expect(readCfg().hooks?.token).toBe('existing-' + 'x'.repeat(40))
    expect(readCfg().hooks?.enabled).toBe(true)

    // (b) admin explicitly disabled ⇒ SDK must NOT override
    fs.writeFileSync(configPath, JSON.stringify({
      gateway: { mode: 'local', bind: 'loopback', port: 18789 },
      hooks: { enabled: false, token: 'admin-' + 'y'.repeat(40) },
      agents: { defaults: { sessionStore: { agentId: 'main' } } },
    }), { mode: 0o600 })
    mod = (await import('../../src/openclaw/index.js')) as {
      default?: { register?: (api: unknown) => void }
    }
    mod.default!.register!({ registerTool: () => 1, registerHttpRoute() {}, registerCommand() {} })
    for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 10))
    expect(readCfg().hooks?.enabled).toBe(false)
    expect(readCfg().hooks?.token).toBe('admin-' + 'y'.repeat(40))
  })
})

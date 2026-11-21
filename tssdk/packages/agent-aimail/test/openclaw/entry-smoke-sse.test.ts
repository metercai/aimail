// SSE profile must not register the legacy HTTP inbound route or CLI commands.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

describe('openclaw-aimail SSE entry smoke', () => {
  let tmpHome: string
  const prevHome = process.env.HOME
  beforeEach(() => {
    // Isolate HOME: register() writes hooks wiring / board / skills under
    // $HOME/.openclaw — never let the test touch the real user config.
    tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-sse-'))
    fs.mkdirSync(path.join(tmpHome, '.openclaw'), { recursive: true })
    fs.writeFileSync(
      path.join(tmpHome, '.openclaw', 'openclaw.json'),
      JSON.stringify({ gateway: { mode: 'local', bind: 'loopback', port: 18789 }, agents: { defaults: { sessionStore: { agentId: 'main' } } } }),
      { mode: 0o600 },
    )
    process.env.HOME = tmpHome
  })
  afterEach(() => {
    if (prevHome === undefined) delete process.env.HOME
    else process.env.HOME = prevHome
    fs.rmSync(tmpHome, { recursive: true, force: true })
  })

  it('registers tools without route/commands', async () => {
    const prev = process.env.AIMAIL_TRANSPORT
    process.env.AIMAIL_TRANSPORT = 'sse'
    vi.resetModules()
    try {
      const mod = (await import('../../src/openclaw/index.js')) as {
        default?: { register?: (api: unknown) => void }
      }
      let tools = 0
      let routes = 0
      let commands = 0
      const api = {
        registerTool(fn: (ctx: unknown) => unknown[] | unknown) {
          const list = fn({ agentId: 'main' })
          tools += Array.isArray(list) ? list.length : 1
        },
        registerHttpRoute() { routes++ },
        registerCommand() { commands++ },
      }
      mod.default!.register!(api)
      expect(tools).toBe(15)
      expect(routes).toBe(0)
      expect(commands).toBe(0)
    } finally {
      if (prev === undefined) delete process.env.AIMAIL_TRANSPORT
      else process.env.AIMAIL_TRANSPORT = prev
      vi.resetModules()
    }
  })
})

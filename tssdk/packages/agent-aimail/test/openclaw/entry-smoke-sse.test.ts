// SSE profile must not register the legacy HTTP inbound route or CLI commands.
import { describe, it, expect, vi } from 'vitest'

describe('openclaw-aimail SSE entry smoke', () => {
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

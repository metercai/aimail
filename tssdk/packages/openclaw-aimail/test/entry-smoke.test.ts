// Smoke test: load the openclaw-aimail entry the way the openclaw plugin
// loader does (import a .ts module via a TS-capable loader — Node 24 native
// type stripping here, jiti in openclaw) and verify it registers
// 12 tools + 1 http route + 1 command.
import { describe, it, expect } from 'vitest'

describe('openclaw-aimail entry smoke', () => {
  it('registers 13 tools, 1 route, 1 command', async () => {
    const mod = (await import('../src/index.ts')) as {
      default?: { register?: (api: unknown) => void }
    }
    const entry = mod.default
    expect(entry).toBeDefined()
    expect(typeof entry?.register).toBe('function')

    let tools = 0
    let routes = 0
    let commands = 0
    const api = {
      registerTool(fn: (ctx: unknown) => unknown[] | unknown) {
        const list = fn({ agentId: 'main' })
        tools += Array.isArray(list) ? list.length : 1
      },
      registerHttpRoute() {
        routes++
      },
      registerCommand() {
        commands++
      },
    }
    entry!.register!(api)
    expect(tools).toBe(13)
    expect(routes).toBe(1)
    expect(commands).toBe(1)
  })
})

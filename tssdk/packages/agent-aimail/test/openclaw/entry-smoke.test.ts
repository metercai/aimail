// Smoke test: load the openclaw-aimail entry the way the openclaw plugin
// loader does (import a .ts module via a TS-capable loader — Node 24 native
// type stripping here, jiti in openclaw) and verify it registers
// 15 tools + 1 http route + 1 command.
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

describe('openclaw-aimail entry smoke', () => {
  let tmpHome: string
  const prevHome = process.env.HOME
  beforeEach(() => {
    // Isolate HOME so register()'s filesystem effects (hooks wiring, board
    // release, skill copy, pull entry) never touch the real user config.
    tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-smoke-'))
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

  it('registers the mail tools and no inbound route (push retired 2026-11-25)', async () => {
    // AIMAIL_TRANSPORT=push used to add the in-gateway HTTP route AND the legacy CLI commands. Both
    // are gone: the flag now only produces a warning line. Asserting 0/0 is what keeps the endpoint
    // and the mailbox-creating command surface from creeping back in.
    process.env.AIMAIL_TRANSPORT = 'push'
      const mod = (await import('../../src/openclaw/index.js')) as {
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
    expect(tools).toBe(15)
    expect(routes).toBe(0)
    expect(commands).toBe(0)
  })
})

// pi-aimail inbound port source-of-truth regression test.
//
// Owner ruling (port is configurable; the ACTUAL bound port is the source of
// truth): the binding's webhook_url must carry the port the listener ACTUALLY
// bound, not the default. The 2026-10-26 QA hold: the eager default-agent
// bind ran BEFORE the listener bound, so the binding captured the 9099
// default even when `listenWithRetry` re-selected a different port on
// collision. Fix (this change): listen first → set the confirmed endpoint →
// bind, so the binding carries the real port on its first write.
//
// This drives the REAL piAimail() entry (like openclaw's entry-smoke) and
// asserts the binder receives the real bound port. Under the old bind-first
// ordering, `inboundWebhookUrl()` returned the 9099 fallback (endpoint not
// set yet) and this assertion fails — so it pins the reorder.
import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

// a non-default port — simulates a collision re-selection (the QA fixture)
const REAL_PORT = 39200

let capturedWebhook: string | undefined
let prevHome: string | undefined
let home: string

vi.mock('../../src/core/index.js', async (importOriginal) => {
  const actual = (await importOriginal<
    typeof import('../../src/core/index.js')
  >()) as typeof import('../../src/core/index.js')
  return {
    ...actual,
    // collision walk: the OS hands back a port different from the preferred one
    listenWithRetry: async () => REAL_PORT,
    // capture the webhook_url the single-source binder is asked to register
    ensureAgentBinding: async (opts: { webhookUrl?: string }) => {
      capturedWebhook = opts.webhookUrl
      return {
        ok: true,
        email: 'agent@test.local',
        systemId: 'sys-t',
        exists: false,
        config: {
          email: 'agent@test.local',
          system_id: 'sys-t',
          api_key: 'k',
          gateway_url: 'http://127.0.0.1:9',
          domain: 'test.local',
        },
      }
    },
    // no CLI / no pull / no resource release in this unit test
    notifyInboundForSystem: async () => [],
    startAgentPullEntries: async () => [],
    releaseAllSystems: () => {},
    packageResourcesRoot: () => os.tmpdir(),
  }
})
vi.mock('../../src/pi/tools.js', () => ({ buildPiTools: () => [] }))
vi.mock('../../src/mail/index.js', () => ({
  resolveByRecipient: async () => {
    throw new Error('unbound')
  },
}))
vi.mock('../../src/hosts/activate.js', () => ({ activateHost: () => {} }))

beforeAll(() => {
  // hermetic: the pointer write + skill release land in $HOME — point it at a
  // tmpdir so the test never touches the real home.
  prevHome = process.env.HOME
  home = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-inbound-'))
  process.env.HOME = home
  process.env.AIMAIL_INBOUND_URL = ''
})

afterAll(() => {
  if (prevHome !== undefined) process.env.HOME = prevHome
  else delete process.env.HOME
  fs.rmSync(home, { recursive: true, force: true })
})

describe('pi-aimail: binding carries the REAL bound port, not the default', () => {
  it('webhook_url == the port the listener actually bound', async () => {
    capturedWebhook = undefined
    const mod = (await import('../../src/pi/index.js')) as {
      default: (pi: unknown, opts?: { inboundPort?: number }) => void
    }
    const api = {
      registerTool() {},
      getActiveTools: () => [],
      sendUserMessage() {},
      on() {},
    }
    // preferred = the 9099 default; the listener re-selects REAL_PORT
    mod.default(api, { inboundPort: 9099 })
    // the doListen chain is async (void) — wait for the binder to be invoked
    await vi.waitFor(() => expect(capturedWebhook).toBeDefined(), {
      timeout: 2000,
    })
    expect(capturedWebhook).toContain(`:${REAL_PORT}`)
    expect(capturedWebhook).not.toContain(':9099')
  })
})

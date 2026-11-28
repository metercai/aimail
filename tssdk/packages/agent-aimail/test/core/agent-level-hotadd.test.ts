/**
 * Hot-add: the runtime starts with zero bindings (the normal state before
 * activation) and starts polling the mailbox the moment the binding is
 * persisted — no host restart, no second reload.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

const processInboundMail = vi.fn(async () => null)
vi.mock('../../src/core/preprocess.js', () => ({
  processInboundMail: (...args: unknown[]) => processInboundMail(...args),
}))

import { fireBindingCreated } from '../../src/core/binding-hooks.js'
import { saveAgentConfig } from '../../src/core/config.js'
import { startAgentLevel } from '../../src/core/agent-level.js'
import { resetAgentSystemIdForTests } from '../../src/core/agent-system-id.js'
import type { AgentConfig } from '../../src/core/types.js'

let tmp: string
let prevHome: string | undefined

beforeEach(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-hotadd-'))
  prevHome = process.env.AIMAIL_HOME
  process.env.AIMAIL_HOME = path.join(tmp, 'aimail')
  process.env.AIMAIL_AGENT_SYSTEM_ID = 'as-e2e'
  await fs.mkdir(process.env.AIMAIL_HOME, { recursive: true })
})

afterEach(async () => {
  delete process.env.AIMAIL_AGENT_SYSTEM_ID
  resetAgentSystemIdForTests()
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  await fs.rm(tmp, { recursive: true, force: true })
})

/** A client whose loop parks until aborted (we only care about lifecycle). */
function idleClient() {
  return {
    started: 0,
    request: async (method: string, p: string) => {
      if (p === '/api/v1/agent-system/pending') return { status: 200, batches: [] }
      if (p === '/api/v1/agent-system/pending/ack') return { status: 200, acked: 0 }
      return { status: 200 }
    },
    // startPolling() in address-code.ts uses this shape check
    get startedCount() {
      return this.started
    },
  }
}

const cfg = {
  agent_id: 'hotadd',
  system_id: 'shared_addr_hot',
  email: 'hotadd@h.local',
  gateway_url: 'http://127.0.0.1:1',
  api_key: 'k',
  domain: 'h.local',
  // V2: only an AgentSystemId group is armable now (per-address pull retired).
  agent_system_id: 'as-e2e',
  workspace_path: '/work/hot',
  binding_source: 'activate-address-code',
  activated_at: '2026-11-25T10:00:00.000Z',
} as unknown as AgentConfig

describe('agent-level hot-add', () => {
  it('starts with no binding and polls the one that activation persists', async () => {
    const client = idleClient()
    const rt = await startAgentLevel({
      onAgent: () => undefined,
      clientFor: () => client as never,
      log: () => undefined,
    })

    expect(rt.handles).toEqual([])

    // The re-arm scans the DISK, so the activation's persistence step comes first.
    await saveAgentConfig(cfg, cfg.system_id)
    await fireBindingCreated(cfg)
    expect(rt.handles.length).toBe(1)
    expect(rt.handles[0]?.email).toBe('agent:as-e2e')

    // idempotent: the same binding must not start a second loop
    await fireBindingCreated(cfg)
    expect(rt.handles.length).toBe(1)

    rt.stop()
    expect(rt.handles).toEqual([])
  })
})

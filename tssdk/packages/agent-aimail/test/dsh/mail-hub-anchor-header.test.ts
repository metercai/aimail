/**
 * dsh's OWN stream must carry the anchor claims too (L2 defect 1, 2026-11-25).
 *
 * dsh runs its own SSE hub instead of the shared pull entry, and it used to build the gateway client
 * by hand — no `X-Aimail-Anchor`. The gateway refuses any address that has a recorded anchor
 * (403 `anchor_mismatch`), so dsh pulled zero mail and said nothing: the refusal is silent on the
 * gateway side and the loop treated it as a dead link.
 *
 * Pinned here:
 *   1. the stream request carries `X-Aimail-Anchor: <email>;<sha256(workspace)>` for the whole group;
 *   2. a 403 is reported in one visible line (and in `/status.lastError`), not swallowed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { MailHub } from '../../src/dsh/mail-hub.js'
import { anchorId } from '../../src/core/anchor-id.js'
import { BINDING_FILE } from '../../src/core/contract.js'
import { resetAgentSystemIdForTests } from '../../src/core/agent-system-id.js'

const ASID = 'as-dsh-hub'

let home = ''
let dsh = ''
let prevHome: string | undefined
let prevAimail: string | undefined
let prevAsid: string | undefined

function ctx(): never {
  const values = new Map<string, unknown>()
  values.set('agents', { get: () => undefined, resume: async () => undefined, create: async () => ({ agent: {}, dispose: async () => {} }) })
  values.set('workspaceRegistry', { list: () => [], create: async (p: string) => ({ id: 'w', path: p }) })
  values.set('agentDefaultModel', { currentSelection: () => ({ provider: 'p', model: 'm' }) })
  values.set('mail', { resolveByRecipient: async () => undefined })
  return { get: (k: string) => values.get(k), provide: () => {} } as never
}

async function seedBinding(email: string, workspace: string): Promise<void> {
  const file = path.join(process.env.AIMAIL_HOME!, 'systems', 'system-1', email.replace('@', '_'), BINDING_FILE)
  await fs.mkdir(path.dirname(file), { recursive: true })
  await fs.writeFile(file, JSON.stringify({
    email,
    system_id: 'system-1',
    gateway_url: 'https://gw.example',
    api_key: 'k-1',
    agent_system_id: ASID,
    agent_id: 'agent',
    domain: 'test.local',
    workspace_path: workspace,
  }), { mode: 0o600 })
}

beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-hub-anchor-'))
  dsh = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-hub-anchor-'))
  prevHome = process.env.HOME
  prevAimail = process.env.AIMAIL_HOME
  prevAsid = process.env.AIMAIL_AGENT_SYSTEM_ID
  process.env.HOME = home
  process.env.AIMAIL_HOME = path.join(home, 'aimail-home')
  process.env.AIMAIL_AGENT_SYSTEM_ID = ASID
  resetAgentSystemIdForTests()
  await seedBinding('a@test.local', '/work/a')
})

afterEach(async () => {
  if (prevHome === undefined) delete process.env.HOME
  else process.env.HOME = prevHome
  if (prevAimail === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevAimail
  if (prevAsid === undefined) delete process.env.AIMAIL_AGENT_SYSTEM_ID
  else process.env.AIMAIL_AGENT_SYSTEM_ID = prevAsid
  resetAgentSystemIdForTests()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  await fs.rm(home, { recursive: true, force: true })
  await fs.rm(dsh, { recursive: true, force: true })
})

describe('dsh stream carries the anchor claims and reports a 403', () => {
  it('sends the claim and surfaces the refusal', async () => {
    const fetchImpl = vi.fn(async () => ({
      status: 403,
      headers: { get: () => 'application/json' },
      text: async () => JSON.stringify({ error: 'anchor_mismatch', detail: 'the anchor registered at activation does not match' }),
      body: null,
    }))
    vi.stubGlobal('fetch', fetchImpl)
    const errors: string[] = []
    vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
      errors.push(args.map(String).join(' '))
    })

    const hub = new MailHub(ctx())
    await hub.start()
    try {
      await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled())
      const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, { headers: Record<string, string> }]
      expect(url).toBe('https://gw.example/api/v1/agent-system/pending/stream')
      expect(init.headers['X-Agent-System-Id']).toBe(ASID)
      expect(init.headers['X-Aimail-Anchor']).toBe(
        `a@test.local;${anchorId('a@test.local', '/work/a')}`,
      )

      await vi.waitFor(() => expect(errors.some((line) => line.includes('anchor refused'))).toBe(true))
      const status = await hub.status()
      expect(String(status.lastError)).toContain('anchor refused')
    } finally {
      hub.dispose()
    }
  })
})

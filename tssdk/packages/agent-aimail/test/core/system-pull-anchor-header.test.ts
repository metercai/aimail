/**
 * The aggregate client MUST carry the anchor claims — on every host, not just the ones that happen
 * to call `buildAgentSystemClient` themselves.
 *
 * L2 defect 1 (2026-11-25): dsh built its own SSE client and dropped `X-Aimail-Anchor`; the gateway
 * answers 403 `anchor_mismatch` for any address with a recorded anchor, so the host pulled ZERO mail
 * and logged nothing. pi and openclaw were on the same broken path (they build through
 * `startSystemPull`) and only escaped because they were not registering an anchor at all.
 *
 * These cases pin the constructor and the resolution that replaced the hand-built clients:
 *   1. `buildAgentSystemClient` sends `X-Aimail-Anchor: email;sha256` for every member;
 *   2. an empty member set sends NO header (nothing to claim);
 *   3. `startSystemPull` resolves the group's members itself when the caller passes none;
 *   4. `startSystemPull` keeps caller-supplied members verbatim.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import {
  buildAgentSystemClient,
  resolveAgentSystemMembers,
} from '../../src/core/agent-system-client.js'
import { startSystemPull } from '../../src/core/system-pull.js'
import { anchorId } from '../../src/core/anchor-id.js'
import { BINDING_FILE } from '../../src/core/contract.js'

const GW = 'https://gw.example'
const ASID = 'as-abc'

let home = ''
let prevHome: string | undefined
let prevAimail: string | undefined

function reply(status: number, body: unknown): { status: number; text: () => Promise<string> } {
  return { status, text: async () => JSON.stringify(body) }
}

function seedBinding(sid: string, email: string, extra: Record<string, unknown> = {}): void {
  const file = path.join(process.env.AIMAIL_HOME!, 'systems', sid, email.replace('@', '_'), BINDING_FILE)
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(
    file,
    JSON.stringify({
      email,
      system_id: sid,
      gateway_url: GW,
      api_key: 'k-1',
      agent_system_id: ASID,
      workspace_path: `/work/${email.split('@')[0]}`,
      ...extra,
    }),
    'utf-8',
  )
}

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-anchor-hdr-'))
  prevHome = process.env.HOME
  prevAimail = process.env.AIMAIL_HOME
  process.env.HOME = home
  process.env.AIMAIL_HOME = path.join(home, 'aimail-home')
})

afterEach(() => {
  if (prevHome === undefined) delete process.env.HOME
  else process.env.HOME = prevHome
  if (prevAimail === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevAimail
  fs.rmSync(home, { recursive: true, force: true })
})

describe('the aggregate client carries the anchor claims (§7.1)', () => {
  it('sends email;sha256 for every member', async () => {
    const fetchImpl = vi.fn(async () => reply(200, { status: 'ok', batches: [] }))
    const members = [
      { email: 'a@test.local', workspace_path: '/work/a' },
      { email: 'b@test.local', workspace_path: '/work/b' },
    ]
    const client = buildAgentSystemClient(GW, 'k-1', ASID, 'a@test.local', members, { fetchImpl })

    await client.request('POST', '/api/v1/agent-system/pending', { limit: 5 })

    const [, init] = fetchImpl.mock.calls[0]!
    expect(init.headers['X-Agent-System-Id']).toBe(ASID)
    expect(init.headers['X-Aimail-Anchor']).toBe(
      `a@test.local;${anchorId('a@test.local', '/work/a')},b@test.local;${anchorId('b@test.local', '/work/b')}`,
    )
  })

  it('omits the header when there is nothing to claim', async () => {
    const fetchImpl = vi.fn(async () => reply(200, { status: 'ok', batches: [] }))
    const client = buildAgentSystemClient(GW, 'k-1', ASID, 'a@test.local', [], { fetchImpl })

    await client.request('POST', '/api/v1/agent-system/pending', { limit: 5 })

    const [, init] = fetchImpl.mock.calls[0]!
    expect(init.headers['X-Agent-System-Id']).toBe(ASID)
    expect(init.headers['X-Aimail-Anchor']).toBeUndefined()
  })

  it('resolves the group members from the binding store when the caller passes none', async () => {
    seedBinding('system-1', 'a@test.local')
    seedBinding('system-1', 'b@test.local')
    // Same address, other AgentSystemId: must not contribute a claim.
    seedBinding('system-2', 'c@test.local', { agent_system_id: 'as-other' })
    // Paused: no stream member, no claim.
    seedBinding('system-1', 'p@test.local', { paused: true })

    const members = await resolveAgentSystemMembers(ASID, `${GW}/`)
    expect(members.map((m) => m.email).sort()).toEqual(['a@test.local', 'b@test.local'])

    const fetchImpl = vi.fn(async () => reply(200, { status: 'ok', batches: [] }))
    const handle = await startSystemPull({
      systemId: 'a@test.local',
      agentSystemId: ASID,
      gatewayUrl: GW,
      apiKey: 'k-1',
      mode: 'poll',
      intervalMs: 10,
      fetchImpl,
      onEmail: () => undefined,
    })
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled())
    handle.stop()

    const [, init] = fetchImpl.mock.calls[0]!
    const claim = String(init.headers['X-Aimail-Anchor'] ?? '')
    expect(claim).toContain(`a@test.local;${anchorId('a@test.local', '/work/a')}`)
    expect(claim).toContain(`b@test.local;${anchorId('b@test.local', '/work/b')}`)
    expect(claim).not.toContain('c@test.local')
    expect(claim).not.toContain('p@test.local')
  })

  it('keeps caller-supplied members verbatim (the dsh/reconciled pool)', async () => {
    const fetchImpl = vi.fn(async () => reply(200, { status: 'ok', batches: [] }))
    const handle = await startSystemPull({
      systemId: 'x@test.local',
      agentSystemId: ASID,
      gatewayUrl: GW,
      apiKey: 'k-1',
      mode: 'poll',
      intervalMs: 10,
      fetchImpl,
      members: [{ email: 'x@test.local', workspace_path: '/explicit/space' }],
      onEmail: () => undefined,
    })
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled())
    handle.stop()

    const [, init] = fetchImpl.mock.calls[0]!
    expect(init.headers['X-Aimail-Anchor']).toBe(
      `x@test.local;${anchorId('x@test.local', '/explicit/space')}`,
    )
  })
})

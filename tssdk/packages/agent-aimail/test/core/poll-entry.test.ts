/**
 * poll-entry.ts — agent-scope pull wiring (the address-code inbound path).
 *
 * Pinned contracts (L1, no container, no network):
 * 1. **Enablement** (`isAgentScopeBinding` / `resolveAgentPullSettings`): only an
 *    address-code binding (host system id `shared_addr_*`, module constant) polls.
 *    A plain system id ⇒ `not-agent-scope`; a missing binding ⇒ `no-binding`;
 *    `AIMAIL_PULL=0|false|off|no` on an agent-scope binding ⇒ `disabled-by-config`.
 * 2. **Interval / batch**: defaults, env override, lower+upper clamp, and
 *    explicit-override precedence over env.
 * 3. **Failed delivery is never acked**: an `onEmail` that rejects leaves the
 *    delivery un-acked and the next round re-pulls the same id (nothing lost).
 * 4. **AbortSignal stops the loop** (the sleep is interrupted, not waited out).
 * 5. `startAgentPullEntries` starts exactly one real loop per agent-scope
 *    binding (host-scoped, stoppable, idempotent stop) and starts NONE for
 *    push-side bindings.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { BINDING_FILE } from '../../src/core/contract.js'
import {
  AGENT_SCOPE_SYSTEM_PREFIX,
  DEFAULT_PULL_INTERVAL_MS,
  DEFAULT_PULL_LIMIT,
  isAgentScopeBinding,
  resolveAgentPullSettings,
  startAgentPullEntries,
  stopAgentPullEntries,
  resetAgentSystemIdForTests,
  saveAgentConfig,
  type AgentConfig,
  type GatewayResponse,
  type RequestClient,
} from '../../src/core/index.js'

/** An address-code binding (the only kind that polls). */
const AGENT_SID = `${AGENT_SCOPE_SYSTEM_PREFIX}e2e`
/** A platform/bridge system (push-served; must never arm a loop). */
const PUSH_SID = 'system-abc12345'

function binding(over: Partial<AgentConfig> = {}): AgentConfig {
  return {
    email: 'agent@e2e.local',
    gateway_url: 'http://127.0.0.1:1',
    domain: 'e2e.local',
    system_id: AGENT_SID,
    api_key: 'deadbeef',
    agent_id: 'agent',
    // Provenance stamp: fixtures model what activateAddressCodePersist writes (see
    // core/binding-provenance.ts — an unstamped binding never arms).
    binding_source: 'activate-address-code',
    activated_at: '2026-11-25T10:00:00.000Z',
    ...over,
  }
}

/** Fake gateway: scripted list responses + ack call log (the assertion surface). */
class FakeGateway implements RequestClient {
  baseUrl = 'http://127.0.0.1:38080'
  calls: Array<{ method: string; path: string; body?: Record<string, unknown> }> = []
  ackCalls: number[][] = []
  private queue: Array<Record<string, unknown>>

  constructor(list: Array<Record<string, unknown>> = []) {
    this.queue = list
  }

  async request(
    method: string,
    path: string,
    body?: Record<string, unknown>,
  ): Promise<GatewayResponse> {
    this.calls.push(body === undefined ? { method, path } : { method, path, body })
    if (path.endsWith('/ack')) {
      const ids = (body?.ids as number[] | undefined) ?? []
      this.ackCalls.push(ids)
      return { status: 200, acked: ids.length }
    }
    return (this.queue.shift() ?? { status: 200, batches: [] }) as GatewayResponse
  }

  get listCalls(): number {
    return this.calls.filter((c) => !c.path.endsWith('/ack')).length
  }
}

function batch(id: number, body: unknown = { subject: 'pulled' }): Record<string, unknown> {
  return {
    status: 200,
    batches: [{ body, deliveries: [{ id, email: 'agent@e2e.local', headers: {} }] }],
  }
}

async function waitFor(fn: () => boolean, ms = 3000, what = 'condition'): Promise<void> {
  const t0 = Date.now()
  while (!fn()) {
    if (Date.now() - t0 > ms) throw new Error(`waitFor timeout: ${what}`)
    await new Promise((r) => setTimeout(r, 10))
  }
}

let home: string
let prevHome: string | undefined

beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'mail-core-poll-'))
  prevHome = process.env.AIMAIL_HOME
  process.env.AIMAIL_HOME = home
  // The aggregator only arms bindings whose agent_system_id is THIS process' id.
  process.env.AIMAIL_AGENT_SYSTEM_ID = 'as-e2e'
  resetAgentSystemIdForTests()
})

afterEach(async () => {
  delete process.env.AIMAIL_AGENT_SYSTEM_ID
  resetAgentSystemIdForTests()
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  await fs.rm(home, { recursive: true, force: true })
})

describe('isAgentScopeBinding — address-code artifact is the pull side', () => {
  it('accepts a shared_addr_* binding (address-code activation product)', () => {
    expect(isAgentScopeBinding(binding())).toBe(true)
  })

  it('rejects a plain system id, a shared-domain SYSTEM id, and an unsigned binding', () => {
    expect(isAgentScopeBinding(binding({ system_id: PUSH_SID }))).toBe(false)
    // `shared-<name>-xxxx` is the SYSTEM-level (push/bridge served) id shape —
    // only the address-level `shared_addr_*` prefix may arm polling.
    expect(isAgentScopeBinding(binding({ system_id: 'shared-public-a1b2c3d4' }))).toBe(false)
    expect(isAgentScopeBinding(binding({ api_key: '' }))).toBe(false)
    expect(isAgentScopeBinding(undefined)).toBe(false)
    expect(isAgentScopeBinding(null)).toBe(false)
  })
})

describe('resolveAgentPullSettings — enablement judgment / interval / batch', () => {
  it('no binding ⇒ no-binding (never enabled)', () => {
    const s = resolveAgentPullSettings(undefined, {})
    expect(s.enabled).toBe(false)
    expect(s.reason).toBe('no-binding')
  })

  it('a push-side binding ⇒ not-agent-scope (push path unchanged)', () => {
    const s = resolveAgentPullSettings(binding({ system_id: PUSH_SID }), {})
    expect(s.enabled).toBe(false)
    expect(s.reason).toBe('not-agent-scope')
  })

  it('address-code binding ⇒ enabled with defaults (30s / 20)', () => {
    const s = resolveAgentPullSettings(binding(), {})
    expect(s.enabled).toBe(true)
    expect(s.reason).toBe('enabled')
    expect(s.intervalMs).toBe(DEFAULT_PULL_INTERVAL_MS)
    expect(s.intervalMs).toBe(30_000)
    expect(s.limit).toBe(DEFAULT_PULL_LIMIT)
    expect(s.limit).toBe(20)
  })

  it('env overrides interval/limit, unparsable falls back to defaults', () => {
    const env = { AIMAIL_PULL_INTERVAL_MS: '1500', AIMAIL_PULL_LIMIT: '5' }
    const s = resolveAgentPullSettings(binding(), env)
    expect(s.intervalMs).toBe(1500)
    expect(s.limit).toBe(5)
    const junk = resolveAgentPullSettings(binding(), {
      AIMAIL_PULL_INTERVAL_MS: 'soonish',
      AIMAIL_PULL_LIMIT: 'lots',
    })
    expect(junk.intervalMs).toBe(DEFAULT_PULL_INTERVAL_MS)
    expect(junk.limit).toBe(DEFAULT_PULL_LIMIT)
  })

  it('clamps the interval to 1s (lower) / 24h (upper) and the limit to 1..200', () => {
    expect(resolveAgentPullSettings(binding(), { AIMAIL_PULL_INTERVAL_MS: '10' }).intervalMs)
      .toBe(1000)
    expect(resolveAgentPullSettings(binding(), { AIMAIL_PULL_INTERVAL_MS: '0' }).intervalMs)
      .toBe(DEFAULT_PULL_INTERVAL_MS)
    expect(
      resolveAgentPullSettings(binding(), { AIMAIL_PULL_INTERVAL_MS: String(48 * 3600_000) })
        .intervalMs,
    ).toBe(24 * 3600_000)
    expect(resolveAgentPullSettings(binding(), { AIMAIL_PULL_LIMIT: '0' }).limit)
      .toBe(DEFAULT_PULL_LIMIT)
    expect(resolveAgentPullSettings(binding(), { AIMAIL_PULL_LIMIT: '9999' }).limit).toBe(200)
  })

  it('AIMAIL_PULL=0|false|off|no disables an address-code binding (case-insensitive)', () => {
    for (const raw of ['0', 'false', 'OFF', 'No', ' false ']) {
      const s = resolveAgentPullSettings(binding(), { AIMAIL_PULL: raw })
      expect(s.enabled, `AIMAIL_PULL=${raw}`).toBe(false)
      expect(s.reason, `AIMAIL_PULL=${raw}`).toBe('disabled-by-config')
    }
    for (const raw of ['1', 'true', 'yes', '']) {
      expect(resolveAgentPullSettings(binding(), { AIMAIL_PULL: raw }).enabled)
        .toBe(true)
    }
  })

  it('explicit overrides beat env (and cannot enable a push-side binding)', () => {
    const s = resolveAgentPullSettings(
      binding(),
      { AIMAIL_PULL: '1', AIMAIL_PULL_INTERVAL_MS: '60000', AIMAIL_PULL_LIMIT: '50' },
      { enabled: false, intervalMs: 2000, limit: 3 },
    )
    expect(s.enabled).toBe(false)
    expect(s.reason).toBe('disabled-by-config')
    expect(s.intervalMs).toBe(2000)
    expect(s.limit).toBe(3)
  })

  it('the config switch is checked AFTER the binding judgement (push stays push)', () => {
    const s = resolveAgentPullSettings(binding({ system_id: PUSH_SID }), { AIMAIL_PULL: '0' })
    expect(s.reason).toBe('not-agent-scope')
  })
})

describe('startAgentPullEntries — one aggregate stream per (AgentSystemId, Gateway)', () => {
  // V2 fixture: the ONLY armable shape now that per-address pull is retired.
  const v2b = (over: Partial<AgentConfig> = {}): AgentConfig =>
    binding({
      agent_system_id: 'as-e2e',
      workspace_path: '/work/e2e',
      ...over,
    })

  it('① no binding on disk ⇒ no stream and no fetch', async () => {
    const client = new FakeGateway([])
    const lines: string[] = []
    const handles = await startAgentPullEntries({
      env: {},
      log: (l) => lines.push(l),
      onEmail: () => {},
      clientFor: () => client,
    })
    expect(handles).toHaveLength(0)
    expect(client.calls).toHaveLength(0)
    expect(lines.join('\n')).toContain('no agent-scope binding')
  })

  it('② one V2 binding ⇒ one aggregate stream: pulls, acks, stop() ends the loop', async () => {
    await saveAgentConfig(v2b(), AGENT_SID)
    const client = new FakeGateway([batch(11)])
    const got: Array<{ id: number; email: string }> = []
    const handles = await startAgentPullEntries({
      env: {},
      log: () => {},
      clientFor: () => client,
      onEmail: (_cfg, mail) => {
        got.push({ id: mail.id, email: mail.email })
      },
    })
    expect(handles).toHaveLength(1)
    const h = handles[0]!
    expect(h.email).toBe('agent:as-e2e') // the GROUP, not the address
    expect(h.started).toBe(true)
    await waitFor(() => client.ackCalls.length === 1, 3000, 'ack of the pulled delivery')
    expect(got).toEqual([{ id: 11, email: 'agent@e2e.local' }])
    expect(client.ackCalls[0]).toEqual([11])
    const atStop = client.listCalls
    h.stop()
    const stats = await h.done
    expect(stats).toEqual({ pulled: 1, acked: 1, errors: 0 })
    await new Promise((r) => setTimeout(r, 50))
    expect(client.listCalls).toBe(atStop) // aborted ⇒ the loop really ended
  })

  it('③ onEmail throws ⇒ no ack, the mail is re-pulled', async () => {
    await saveAgentConfig(v2b(), AGENT_SID)
    const client = new FakeGateway([batch(21), batch(21)])
    let attempts = 0
    const handles = await startAgentPullEntries({
      env: { AIMAIL_PULL_INTERVAL_MS: '1000' },
      log: () => {},
      clientFor: () => client,
      onEmail: () => {
        attempts += 1
        if (attempts === 1) throw new Error('delivery failed')
      },
    })
    const h = handles[0]!
    await waitFor(() => attempts >= 2, 5000, 'the re-pull after a failed delivery')
    expect(client.ackCalls.flat()).toEqual([21]) // only the successful round acked
    h.stop()
    await h.done
  })

  it('④ interval is clamped to the legal minimum', async () => {
    await saveAgentConfig(v2b(), AGENT_SID)
    const handles = await startAgentPullEntries({
      env: { AIMAIL_PULL_INTERVAL_MS: '1' }, // below the floor
      log: () => {},
      onEmail: () => {},
      clientFor: () => new FakeGateway([]),
    })
    expect(handles[0]!.intervalMs).toBeGreaterThanOrEqual(1000)
    handles.forEach((h) => h.stop())
  })

  it('⑤ two addresses of one group ⇒ exactly ONE stream; a V1 binding is refused', async () => {
    await saveAgentConfig(v2b({ email: 'a@e2e.local' }), AGENT_SID)
    await saveAgentConfig(v2b({ email: 'b@e2e.local' }), AGENT_SID)
    await saveAgentConfig(binding({ email: 'v1@e2e.local' }), AGENT_SID) // no agent_system_id
    const lines: string[] = []
    let built = 0
    const handles = await startAgentPullEntries({
      env: {},
      log: (l) => lines.push(l),
      onEmail: () => {},
      clientFor: () => {
        built += 1
        return new FakeGateway([])
      },
    })
    expect(handles).toHaveLength(1)
    expect(built).toBe(1)
    expect(lines.join('\n')).toContain('per-address pull is retired')
    handles.forEach((h) => h.stop())
  })

  it('⑥ onlyEmail selects the group that contains that address (case-insensitive)', async () => {
    await saveAgentConfig(v2b({ email: 'a@e2e.local' }), AGENT_SID)
    // Matching (case-insensitively) ⇒ the group stream is armed once.
    for (const onlyEmail of ['A@E2E.LOCAL', 'a@e2e.local']) {
      const handles = await startAgentPullEntries({
        env: {},
        onlyEmail,
        log: () => {},
        onEmail: () => {},
        clientFor: () => new FakeGateway([]),
      })
      expect(handles).toHaveLength(1)
      expect(handles[0]!.email).toBe('agent:as-e2e')
      handles.forEach((h) => h.stop())
    }
    // No address matches ⇒ nothing is armed (the mail stays pending).
    const none = await startAgentPullEntries({
      env: {},
      onlyEmail: 'nobody@e2e.local',
      log: () => {},
      onEmail: () => {},
      clientFor: () => new FakeGateway([]),
    })
    expect(none).toHaveLength(0)
  })

  it('⑦ the same address in two binding files ⇒ still ONE stream', async () => {
    await saveAgentConfig(v2b(), AGENT_SID)
    await saveAgentConfig(v2b(), `${AGENT_SCOPE_SYSTEM_PREFIX}dup`)
    const handles = await startAgentPullEntries({
      env: {},
      log: () => {},
      onEmail: () => {},
      clientFor: () => new FakeGateway([]),
    })
    expect(handles).toHaveLength(1)
    handles.forEach((h) => h.stop())
  })
})

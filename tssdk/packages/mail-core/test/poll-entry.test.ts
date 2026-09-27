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
import {
  AGENT_SCOPE_SYSTEM_PREFIX,
  DEFAULT_PULL_INTERVAL_MS,
  DEFAULT_PULL_LIMIT,
  isAgentScopeBinding,
  resolveAgentPullSettings,
  startAgentPullEntries,
  stopAgentPullEntries,
  saveAgentConfig,
  type AgentConfig,
  type GatewayResponse,
  type RequestClient,
} from '../src/index.js'

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
    ...over,
  }
}

/** Fake gateway: scripted list responses + ack call log (the assertion surface). */
class FakeGateway implements RequestClient {
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
})

afterEach(async () => {
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  await fs.rm(home, { recursive: true, force: true })
})

describe('isAgentScopeBinding — address-code产物才是 pull 侧', () => {
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

describe('resolveAgentPullSettings — 启用判定 / 间隔 / 批量', () => {
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

describe('startAgentPullEntries — 真循环按绑定起停', () => {
  it('① 未激活(无 agent 级绑定)⇒ 不起轮询, 一条 pending 都不取', async () => {
    await saveAgentConfig(binding({ system_id: PUSH_SID }), PUSH_SID)
    const client = new FakeGateway([batch(1), batch(2)])
    let clientBuilt = 0
    let delivered = 0
    const lines: string[] = []
    const handles = await startAgentPullEntries({
      env: {},
      log: (l) => lines.push(l),
      onEmail: () => {
        delivered += 1
      },
      clientFor: (cfg) => {
        clientBuilt += 1
        expect(cfg.system_id).toBe(AGENT_SID) // never reached for a push binding
        return client
      },
    })
    expect(handles).toHaveLength(0)
    expect(clientBuilt).toBe(0)
    expect(delivered).toBe(0)
    expect(client.calls).toHaveLength(0)
    const log = lines.join('\n')
    expect(log).toContain('not-agent-scope')
    expect(log).toContain('no agent-scope binding')
  })

  it('② 激活后 ⇒ 自取 pending 并 ack, handle.stop() 结束循环', async () => {
    await saveAgentConfig(binding(), AGENT_SID)
    const client = new FakeGateway([batch(11, { subject: 'self-pull' })])
    const got: Array<{ id: number; email: string; body: unknown }> = []
    const lines: string[] = []
    const handles = await startAgentPullEntries({
      env: {},
      log: (l) => lines.push(l),
      clientFor: () => client,
      onEmail: (_cfg, mail) => {
        got.push({ id: mail.id, email: mail.email, body: mail.body })
      },
    })
    expect(handles).toHaveLength(1)
    const h = handles[0]!
    expect(h.email).toBe('agent@e2e.local')
    expect(h.started).toBe(true)
    expect(h.reason).toBe('enabled')
    expect(h.intervalMs).toBe(DEFAULT_PULL_INTERVAL_MS)
    expect(h.limit).toBe(DEFAULT_PULL_LIMIT)
    expect(lines.join('\n')).toContain('polling enabled every 30000ms')

    await waitFor(() => client.ackCalls.length === 1, 3000, 'ack of the pulled delivery')
    expect(got).toEqual([{ id: 11, email: 'agent@e2e.local', body: { subject: 'self-pull' } }])
    expect(client.ackCalls[0]).toEqual([11])

    const atStop = client.listCalls
    h.stop()
    const stats = await h.done
    expect(stats).toEqual({ pulled: 1, acked: 1, errors: 0 })
    expect(h.stats()).toEqual({ pulled: 1, acked: 1, errors: 0 })
    await new Promise((r) => setTimeout(r, 50))
    expect(client.listCalls).toBe(atStop) // aborted ⇒ the loop really ended
  })

  it('③ 投递失败(onEmail reject)⇒ 不 ack, 下一轮重新取到同一封', async () => {
    await saveAgentConfig(binding(), AGENT_SID)
    // Same delivery id twice: the first attempt fails, the second succeeds.
    const client = new FakeGateway([batch(21), batch(21)])
    const attempts: number[] = []
    const handles = await startAgentPullEntries({
      // Shortest legal interval so the re-pull lands inside the test window.
      env: { AIMAIL_PULL_INTERVAL_MS: '1000' },
      log: () => {},
      clientFor: () => client,
      onEmail: (_cfg, mail) => {
        attempts.push(mail.id)
        if (attempts.length === 1) throw new Error('inbound chain down (simulated)')
      },
    })
    const h = handles[0]!
    await waitFor(() => attempts.length >= 2, 3000, 'the failed delivery to be re-pulled')
    expect(attempts).toEqual([21, 21])
    await waitFor(() => client.ackCalls.length === 1, 3000, 'the successful attempt to be acked')
    // Exactly one ack, and it carries only the successful id.
    expect(client.ackCalls).toEqual([[21]])
    h.stop()
    const stats = await h.done
    expect(stats.acked).toBe(1)
    expect(stats.pulled).toBe(1)
    expect(stats.errors).toBe(1) // the failure was counted, not swallowed
  })

  it('④ 间隔下限钳制 + AbortSignal 打断 sleep(不是等满间隔)', async () => {
    await saveAgentConfig(binding(), AGENT_SID)
    const client = new FakeGateway([]) // always an empty batch
    const handles = await startAgentPullEntries({
      env: { AIMAIL_PULL_INTERVAL_MS: '10', AIMAIL_PULL_LIMIT: '3' },
      log: () => {},
      clientFor: () => client,
      onEmail: () => {
        throw new Error('no delivery expected from an empty batch')
      },
    })
    const h = handles[0]!
    expect(h.intervalMs).toBe(1000) // '10' clamped up to the 1s floor
    expect(h.limit).toBe(3)
    await waitFor(() => client.listCalls >= 1, 3000, 'first round')
    const t0 = Date.now()
    h.stop()
    await h.done
    // Without an interruptible sleep this would take the full 1000ms interval.
    expect(Date.now() - t0).toBeLessThan(600)
    const after = client.listCalls
    await new Promise((r) => setTimeout(r, 100))
    expect(client.listCalls).toBe(after)
  })

  it('⑤ 一个 agent 级绑定一条循环; systemId 限定作用域; stop 幂等', async () => {
    await saveAgentConfig(binding({ email: 'a@e2e.local' }), AGENT_SID)
    await saveAgentConfig(binding({ email: 'b@e2e.local' }), AGENT_SID)
    await saveAgentConfig(binding({ email: 'agent@other.local', system_id: `${AGENT_SCOPE_SYSTEM_PREFIX}other` }), `${AGENT_SCOPE_SYSTEM_PREFIX}other`)
    const clients: FakeGateway[] = []
    const mk = (): FakeGateway => {
      const c = new FakeGateway([])
      clients.push(c)
      return c
    }
    const all = await startAgentPullEntries({
      env: {},
      log: () => {},
      clientFor: mk,
      onEmail: () => {
        throw new Error('no delivery expected from an empty batch')
      },
    })
    expect(all.map((h) => h.email).sort()).toEqual(['a@e2e.local', 'agent@other.local', 'b@e2e.local'])

    const scoped = await startAgentPullEntries({
      systemId: AGENT_SID,
      env: {},
      log: () => {},
      clientFor: mk,
      onEmail: () => {
        throw new Error('no delivery expected from an empty batch')
      },
    })
    expect(scoped.map((h) => h.email).sort()).toEqual(['a@e2e.local', 'b@e2e.local'])

    stopAgentPullEntries(all)
    stopAgentPullEntries(all) // idempotent
    stopAgentPullEntries(scoped)
    for (const h of [...all, ...scoped]) {
      await h.done
      expect(h.stats().errors).toBe(0)
    }
  })
})

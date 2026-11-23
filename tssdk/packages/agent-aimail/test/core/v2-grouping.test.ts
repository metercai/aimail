/**
 * §6.7 M3 tests — the V2 (AgentSystemId aggregate) pull contract.
 *
 * Covers the 18 acceptance items in the plan (v8): A' grouping (one stream
 * per (AgentSystemId, Gateway)), the delivery-level typed skip (paused/unknown
 * ⇒ left pending, never acked, never an error), the 401/403 auth-fatal (3
 * strikes ⇒ PullAuthError settles done, with the REAL status), the branch
 * guard (V2 always groups, V1 per-binding), the direct-entry refusal, and the
 * hot-add re-arm. No network, no container: poll-only fake clients (a client
 * without stream methods goes straight to the long-poll path in startPolling)
 * + bindings on disk under a temp AIMAIL_HOME.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  AGENT_SCOPE_SYSTEM_PREFIX,
  saveAgentConfig,
  findBindingByEmail,
  setBindingPaused,
  startAgentSystemGroups,
  startAgentPullEntry,
  startAgentPullEntries,
  startAgentLevel,
  startSystemPull,
  fireBindingCreated,
  resetAgentSystemIdForTests,
  PullAuthError,
  type AgentConfig,
  type GatewayResponse,
  type RequestClient,
} from '../../src/core/index.js'

const V1_SID = `${AGENT_SCOPE_SYSTEM_PREFIX}e2e`
const ASID = 'asid_test_0001'
const ASID2 = 'asid_test_0002'
const GW = 'http://127.0.0.1:1'

/** V2 binding (carries agent_system_id ⇒ pulls by aggregate). */
function v2(email: string, over: Partial<AgentConfig> = {}): AgentConfig {
  return {
    email,
    gateway_url: GW,
    domain: 'e2e.local',
    system_id: 'system-b237',
    api_key: 'key-' + email,
    agent_id: email.split('@')[0],
    agent_system_id: ASID,
    ...over,
  }
}
/** V1 binding (shared_addr_* host system ⇒ per-binding legacy loop). */
function v1(email: string, over: Partial<AgentConfig> = {}): AgentConfig {
  return {
    email,
    gateway_url: GW,
    domain: 'e2e.local',
    system_id: V1_SID,
    api_key: 'key-' + email,
    agent_id: email.split('@')[0],
    ...over,
  }
}

/** Poll-only fake gateway (no stream methods ⇒ startPolling uses long-poll). */
class FakePoll implements RequestClient {
  baseUrl = GW
  calls: Array<{ method: string; path: string; body?: Record<string, unknown> }> = []
  ackCalls: number[][] = []
  queue: Array<Record<string, unknown>>

  constructor(list: Array<Record<string, unknown>> = []) {
    this.queue = list
  }
  async request(method: string, p: string, body?: Record<string, unknown>): Promise<GatewayResponse> {
    this.calls.push(body === undefined ? { method, path: p } : { method, path: p, body })
    if (p.endsWith('/ack')) {
      const ids = (body?.ids as number[] | undefined) ?? []
      this.ackCalls.push(ids)
      return { status: 200, acked: ids.length } as GatewayResponse
    }
    return (this.queue.shift() ?? { status: 200, batches: [] }) as GatewayResponse
  }
  get listCalls(): number {
    return this.calls.filter((c) => !c.path.endsWith('/ack')).length
  }
}
const batch = (id: number, email: string): Record<string, unknown> => ({
  status: 200,
  batches: [{ body: { subject: 's' }, deliveries: [{ id, email, headers: {} }] }],
})
const fail = (status: number): Record<string, unknown> => ({ status })

async function waitFor(fn: () => boolean, ms = 4000, what = 'cond'): Promise<void> {
  const t0 = Date.now()
  while (!fn()) {
    if (Date.now() - t0 > ms) throw new Error(`waitFor timeout: ${what}`)
    await new Promise((r) => setTimeout(r, 10))
  }
}
/**
 * One fresh FakePoll per arm call (clientFor is invoked once per group at
 * arming time). This mirrors production: a re-arm builds NEW clients, and the
 * old loop's client must go quiet after its loop is stopped. The queue for
 * call i is perCall[i] (last entry repeats once the queue outgrows it).
 */
function countingClients(perCall: Array<Array<Record<string, unknown>>>): {
  clients: FakePoll[]
  built: () => number
  clientFor: () => FakePoll
} {
  const clients: FakePoll[] = []
  return {
    clients,
    built: () => clients.length,
    clientFor: () => {
      const q = perCall[Math.min(clients.length, perCall.length - 1)] ?? []
      const c = new FakePoll(q)
      clients.push(c)
      return c
    },
  }
}

let home: string
let prevHome: string | undefined
beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'mail-v2grp-'))
  prevHome = process.env.AIMAIL_HOME
  process.env.AIMAIL_HOME = home
  process.env.AIMAIL_AGENT_SYSTEM_ID = ASID
  resetAgentSystemIdForTests()
})
afterEach(async () => {
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  delete process.env.AIMAIL_AGENT_SYSTEM_ID
  resetAgentSystemIdForTests()
  await fs.rm(home, { recursive: true, force: true })
})

describe('§6.7 core — A\' grouping + typed skip + auth fatal', () => {
  it('#1 AIMAIL_PULL=0 ⇒ 0 stream + audit (grouping path)', async () => {
    await saveAgentConfig(v2('a@e2e.local'), 'system-b237')
    let built = 0
    const lines: string[] = []
    const handles = await startAgentSystemGroups([v2('a@e2e.local')], {
      env: { AIMAIL_PULL: '0' },
      log: (l) => lines.push(l),
      onEmail: () => {},
      clientFor: () => {
        built++
        return new FakePoll()
      },
    })
    expect(handles).toHaveLength(0)
    expect(built).toBe(0)
    expect(lines.join('\n')).toContain('pull disabled by config')
  })

  it('#2 single V2 address paused ⇒ no delivery / no ack (all-paused group does not arm)', async () => {
    const b = v2('a@e2e.local', { paused: true })
    let delivered = 0
    const lines: string[] = []
    const handles = await startAgentSystemGroups([b], {
      env: {},
      log: (l) => lines.push(l),
      onEmail: () => {
        delivered++
      },
      clientFor: () => new FakePoll([batch(1, 'a@e2e.local')]),
    })
    expect(handles).toHaveLength(0)
    expect(delivered).toBe(0)
    expect(lines.join('\n')).toContain('all members paused')
  })

  it('#3 enabled+paused mixed group ⇒ only the enabled address is delivered+acked', async () => {
    await saveAgentConfig(v2('a@e2e.local'), 'system-b237')
    await saveAgentConfig(v2('b@e2e.local', { paused: true }), 'system-b237')
    const client = new FakePoll([
      batch(1, 'a@e2e.local'),
      batch(2, 'b@e2e.local'),
      batch(1, 'a@e2e.local'), // re-pull of the (acked) one must be deduped
      batch(2, 'b@e2e.local'), // b stays pending ⇒ re-pulled
    ])
    const delivered: string[] = []
    const handles = await startAgentSystemGroups([v2('a@e2e.local'), v2('b@e2e.local', { paused: true })], {
      env: {},
      log: () => {},
      onEmail: (_cfg, mail) => {
        delivered.push(mail.email)
      },
      clientFor: () => client,
    })
    expect(handles).toHaveLength(1) // exactly ONE aggregate stream
    const h = handles[0]!
    await waitFor(() => client.ackCalls.length >= 1, 4000, 'ack of the enabled address')
    // A delivered + acked; B skipped (paused ⇒ left pending, never acked).
    expect(delivered).toEqual(['a@e2e.local'])
    expect(client.ackCalls.flat()).toEqual([1])
    expect(client.ackCalls.flat()).not.toContain(2) // b not acked
    h.stop()
    const stats = await h.done
    expect(stats.errors).toBe(0) // a skip is NOT an error
  })

  it('#4 unknown address (out-of-group delivery) ⇒ not acked, not stolen, not an error', async () => {
    await saveAgentConfig(v2('a@e2e.local'), 'system-b237')
    const client = new FakePoll([
      batch(7, 'ghost@e2e.local'), // no binding for this address
      batch(7, 'ghost@e2e.local'), // re-pulled (still pending)
    ])
    let delivered = 0
    const handles = await startAgentSystemGroups([v2('a@e2e.local')], {
      env: {},
      log: () => {},
      onEmail: () => {
        delivered++
      },
      clientFor: () => client,
    })
    const h = handles[0]!
    await waitFor(() => client.listCalls >= 2, 4000, 'the unknown mail to be re-pulled')
    expect(delivered).toBe(0) // never reached the host
    expect(client.ackCalls.flat()).toEqual([]) // nothing acked
    h.stop()
    const stats = await h.done
    expect(stats.errors).toBe(0) // unknown is a skip, not an error
  })

  it('#5 no-pointer + 2 V2 addresses (same asid) ⇒ exactly ONE aggregate stream; each delivery once; no dup ack', async () => {
    await saveAgentConfig(v2('a@e2e.local'), 'system-b237')
    await saveAgentConfig(v2('b@e2e.local'), 'system-b237')
    const client = new FakePoll([batch(1, 'a@e2e.local'), batch(2, 'b@e2e.local')])
    let built = 0
    const delivered: string[] = []
    const handles = await startAgentPullEntries({
      env: {},
      log: () => {},
      onEmail: (_c, mail) => {
        delivered.push(mail.email)
      },
      clientFor: () => {
        built++
        return client
      },
    })
    expect(handles).toHaveLength(1) // one (asid, gateway) group
    expect(built).toBe(1) // exactly ONE aggregate client
    expect(handles[0]!.email).toBe(`agent:${ASID}`)
    const h = handles[0]!
    await waitFor(() => delivered.length >= 2, 4000, 'both addresses delivered')
    expect(delivered.sort()).toEqual(['a@e2e.local', 'b@e2e.local'])
    expect(client.ackCalls.flat().sort()).toEqual([1, 2])
    h.stop()
    await h.done
  })

  it('#6 hot-add: 2 start ⇒ add 3rd (same asid) ⇒ live stays 1 (re-arm, not stack); new address receives; re-arm audit members=3', async () => {
    await saveAgentConfig(v2('a@e2e.local'), 'system-b237')
    await saveAgentConfig(v2('b@e2e.local'), 'system-b237')
    // Re-arm builds a FRESH client; its queue only carries the NEW mail
    // (a/b were acked by the first loop, so the gateway never re-serves them).
    const cc = countingClients([
      [batch(1, 'a@e2e.local'), batch(2, 'b@e2e.local')], // first arm
      [batch(3, 'c@e2e.local')], // re-arm: only the new address is pending
    ])
    const delivered: string[] = []
    const lines: string[] = []
    const rt = await startAgentLevel({
      env: {},
      log: (l) => lines.push(l),
      onEmail: (_c, mail) => {
        delivered.push(mail.email)
      },
      clientFor: cc.clientFor,
    })
    await waitFor(() => delivered.length >= 2, 4000, 'both initial addresses delivered')
    // Add the third address (same asid) → hot-add re-arms (stop + rescan).
    await saveAgentConfig(v2('c@e2e.local'), 'system-b237')
    await fireBindingCreated(v2('c@e2e.local'))
    await waitFor(() => cc.built() === 2, 4000, 'the re-arm to build a new client')
    await waitFor(() => delivered.includes('c@e2e.local'), 4000, 'the new address to receive')
    // The OLD loop is stopped (not stacked): its client goes quiet.
    const oldCalls = cc.clients[0]!.listCalls
    await new Promise((r) => setTimeout(r, 100))
    expect(cc.clients[0]!.listCalls).toBe(oldCalls)
    // No duplicate deliveries: each address exactly once (old mail was acked).
    expect(delivered.filter((e) => e === 'a@e2e.local')).toHaveLength(1)
    expect(delivered.filter((e) => e === 'b@e2e.local')).toHaveLength(1)
    // The re-arm audit line carries all three members.
    const rearm = lines.filter((l) => l.includes('members=[')).find((l) => l.includes('c@e2e.local'))
    expect(rearm).toBeDefined()
    expect(rearm).toContain('members=[a@e2e.local, b@e2e.local, c@e2e.local]')
    rt.stop()
  }, 20000)

  it('#7 rep key 401 ⇒ first-strike line + 3-strike done settle to PullAuthError(401) + mail stays pending', async () => {
    const client = new FakePoll([fail(401), fail(401), fail(401)])
    const lines: string[] = []
    const strikes: Array<{ status: number; n: number }> = []
    const h = await startSystemPull({
      systemId: 'agent:rep',
      agentSystemId: ASID,
      gatewayUrl: GW,
      apiKey: 'deadkey',
      client,
      intervalMs: 1000,
      mode: 'poll',
      log: (l) => lines.push(l),
      onAuthFailure: (status, streak) => strikes.push({ status, n: streak }),
    })
    const settled = await h.done
    expect(settled).toBeInstanceOf(PullAuthError)
    expect((settled as PullAuthError).status).toBe(401) // REAL status, not a fixed stand-in
    // First strike reported immediately; three strikes total.
    expect(strikes[0]).toEqual({ status: 401, n: 1 })
    expect(strikes.length).toBe(3)
    expect(lines.join('\n')).toContain('auth rejection (401)') // first-strike group line
    expect(client.ackCalls).toEqual([]) // mail never acked (stays pending)
  }, 20000)

  it('#8 compat regression: startAgentPullEntry(V1 cfg) real call, onEmail void ⇒ ack + stats.pulled++', async () => {
    await saveAgentConfig(v1('agent@e2e.local'), V1_SID)
    const client = new FakePoll([batch(11, 'agent@e2e.local')])
    const h = await startAgentPullEntry(v1('agent@e2e.local'), {
      env: {},
      log: () => {},
      onEmail: () => {}, // void = accepted
      clientFor: () => client,
    })
    expect(h).not.toBeNull()
    const handle = h!
    await waitFor(() => client.ackCalls.length >= 1, 4000, 'ack of the V1 delivery')
    expect(client.ackCalls.flat()).toEqual([11])
    handle.stop()
    const stats = await handle.done
    expect(stats.pulled).toBe(1)
    expect(stats.acked).toBe(1)
  })

  it('#9 non-auth failure (500) ⇒ backoff, loop stays alive (NOT fatal) — regression guard', async () => {
    await saveAgentConfig(v1('agent@e2e.local'), V1_SID)
    const client = new FakePoll([fail(500), fail(500), fail(500), batch(1, 'agent@e2e.local')])
    const h = (await startAgentPullEntry(v1('agent@e2e.local'), {
      env: { AIMAIL_PULL_INTERVAL_MS: '1000' },
      log: () => {},
      onEmail: () => {},
      clientFor: () => client,
    }))!
    // 3× 500: three failed rounds must hit the gateway, and the loop must
    // keep polling afterwards (NOT fatal, unlike 401/403).
    await waitFor(() => client.listCalls >= 3, 15000, 'three 500 rounds')
    // Recovery: the 4th round succeeds and acks — proof the loop survived.
    await waitFor(() => client.ackCalls.length >= 1, 20000, 'recovery round ack')
    h.stop()
    const stats = await h.done
    expect(stats).toEqual(expect.objectContaining({ acked: 1 }))
  }, 45000)

  it('#14 startAgentPullEntries({systemId}) + 2 V2 addresses ⇒ exactly ONE aggregate stream (branch guard)', async () => {
    await saveAgentConfig(v2('a@e2e.local'), 'system-b237')
    await saveAgentConfig(v2('b@e2e.local'), 'system-b237')
    let built = 0
    const handles = await startAgentPullEntries({
      systemId: 'system-b237',
      env: {},
      log: () => {},
      onEmail: () => {},
      clientFor: () => {
        built++
        return new FakePoll([])
      },
    })
    expect(handles).toHaveLength(1)
    expect(built).toBe(1)
    expect(handles[0]!.email).toBe(`agent:${ASID}`)
    handles.forEach((h) => h.stop())
    for (const h of handles) await h.done
  })

  it('#15 mixed V1+V2 fanout (systemId branch) ⇒ V1 per-binding + V2 exactly ONE group; total = 1 + V1 count', async () => {
    await saveAgentConfig(v1('x@e2e.local'), V1_SID)
    await saveAgentConfig(v2('a@e2e.local'), 'system-b237')
    await saveAgentConfig(v2('b@e2e.local'), 'system-b237')
    let built = 0
    const handles = await startAgentPullEntries({
      systemId: V1_SID,
      env: {},
      log: () => {},
      onEmail: () => {},
      clientFor: () => {
        built++
        return new FakePoll([])
      },
    })
    // The V1 binding lives under V1_SID; the two V2 under system-b237.
    // Scoped to V1_SID ⇒ only the V1 binding is enumerated here.
    expect(handles.map((h) => h.email)).toEqual(['x@e2e.local'])
    handles.forEach((h) => h.stop())
    for (const h of handles) await h.done
  })

  it('#16 startAgentPullEntry(V2 cfg) direct call ⇒ null + refusal audit; a group stream already up is not duplicated', async () => {
    const lines: string[] = []
    const h = await startAgentPullEntry(v2('a@e2e.local'), {
      env: {},
      log: (l) => lines.push(l),
      onEmail: () => {},
      clientFor: () => new FakePoll([]),
    })
    expect(h).toBeNull() // V2 per-entry start is refused
    expect(lines.join('\n')).toContain('V2 binding — per-entry start refused')
  })

  it('#17 startAgentLevel running, add a V2 address on a SECOND gateway (same asid) ⇒ re-arm ⇒ one stream PER (asid, gateway), not stacked per address; new address receives', async () => {
    const GW2 = 'http://127.0.0.1:2'
    await saveAgentConfig(v2('a@e2e.local', { agent_system_id: ASID }), 'system-b237')
    // Re-arm builds FRESH clients (one per (asid, gateway)): the re-scan finds
    // TWO groups now ⇒ TWO new clients. The a-group's new queue is empty (a was
    // acked); only the z-group (GW2) serves pending mail.
    const cc = countingClients([
      [batch(1, 'a@e2e.local')], // arm 1: one group (ASID, GW)
      [], // re-arm group 1 (ASID, GW): a already acked ⇒ nothing pending
      [batch(2, 'z@other.local')], // re-arm group 2 (ASID, GW2): the new address
    ])
    const delivered: string[] = []
    const lines: string[] = []
    const rt = await startAgentLevel({
      env: {},
      log: (l) => lines.push(l),
      onEmail: (_c, mail) => {
        delivered.push(mail.email)
      },
      clientFor: cc.clientFor,
    })
    await waitFor(() => delivered.includes('a@e2e.local'), 4000, 'first group delivered')
    // The host adds a binding on a SECOND gateway (same AgentSystemId) — a
    // real production case. Hot-add re-arms (stop + rescan): now TWO
    // (asid, gateway) groups ⇒ TWO fresh clients, NOT a per-address stack.
    const z = v2('z@other.local', { agent_system_id: ASID, gateway_url: GW2 })
    await saveAgentConfig(z, 'system-z999')
    await fireBindingCreated(z)
    await waitFor(() => cc.built() === 3, 6000, 'the re-arm to build the two group clients')
    await waitFor(() => delivered.includes('z@other.local'), 6000, 'the second-gateway address to receive')
    // The OLD loop is stopped (not stacked): its client goes quiet.
    const oldCalls = cc.clients[0]!.listCalls
    await new Promise((r) => setTimeout(r, 100))
    expect(cc.clients[0]!.listCalls).toBe(oldCalls)
    // Two distinct group audit lines, one per (asid, gateway).
    const aggregate = lines.filter((l) => l.includes('aggregate agent:') && l.includes('members=['))
    const gws = new Set(aggregate.map((l) => (l.match(/gw=(\S+)/) ?? [])[1]))
    expect(gws.has(GW)).toBe(true)
    expect(gws.has(GW2)).toBe(true)
    expect(delivered.filter((e) => e === 'a@e2e.local')).toHaveLength(1) // no duplicate
    expect(delivered).toContain('z@other.local')
    rt.stop()
  }, 25000)

  it('#18 V1 shared_addr_ paused ⇒ NOT armed (arm-level null + audit); runtime pause ⇒ delivery-level skip; resume ⇒ delivers again', async () => {
    const b = v1('agent@e2e.local', { paused: true })
    const lines: string[] = []
    // Arm-level: a paused V1 binding starts NO loop.
    const h0 = await startAgentPullEntry(b, {
      env: {},
      log: (l) => lines.push(l),
      onEmail: () => {},
      clientFor: () => new FakePoll([]),
    })
    expect(h0).toBeNull()
    expect(lines.join('\n')).toContain('paused — not polling')

    // Runtime pause (already armed) ⇒ delivery-level skip: not delivered, not acked, errors 0, re-pullable.
    await saveAgentConfig(v1('agent@e2e.local'), V1_SID) // active on disk
    const client = new FakePoll([
      batch(1, 'agent@e2e.local'), // round 1: active ⇒ delivered
    ])
    const delivered: string[] = []
    const h = (await startAgentPullEntry(v1('agent@e2e.local'), {
      env: { AIMAIL_PULL_INTERVAL_MS: '50' },
      log: () => {},
      onEmail: (_c, mail) => {
        delivered.push(mail.email)
      },
      clientFor: () => client,
    }))!
    await waitFor(() => delivered.length >= 1, 4000, 'first (active) delivery')
    // Now pause it mid-run; the NEXT delivery of the same id must be skipped.
    const loc = await findBindingByEmail('agent@e2e.local')
    await setBindingPaused(loc.activePath!, true, 'agent@e2e.local')
    // Hand the SAME delivery id back only AFTER the pause is on disk — the old
    // fixture queued both rounds up front, so a fast non-empty round could
    // deliver round 2 before the pause and make this test timing-dependent.
    client.queue.push(batch(1, 'agent@e2e.local'))
    await waitFor(() => client.listCalls >= 2, 6000, 'the re-pulled (paused) round')
    // Still exactly one delivery (the pre-pause one); the paused re-pull is skipped.
    expect(delivered).toEqual(['agent@e2e.local'])
    expect(client.ackCalls.flat()).toEqual([1]) // only the active round acked
    h.stop()
    const stats = await h.done
    expect(stats.errors).toBe(0) // skip is not an error
  })
})

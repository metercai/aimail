/**
 * pull-stream-state-machine.test.ts — Phase 2 TS state machine + contract
 * (three-client parity with py `aimail_base`/`aimail_tools` and bridge
 * `pull.rs`).
 *
 * Pins:
 * 1. SseParser — incremental frame parsing (same vectors as py/bridge);
 * 2. contract constants — ratchet against the gateway single source;
 * 3. decision functions — the SAME input vectors as py/bridge;
 * 4. startPolling state machine — scripted stream rounds (auto downgrade
 *    after 3 consecutive mid-stream errors; a healthy rotation RESETS the
 *    streak; stream mode never downgrades; drop ⇒ straight to Poll; a
 *    client without stream methods is poll-only);
 * 5. py↔ts parity — the py functions re-run via stdin (no .py file lands
 *    in the TS tree; the language-boundary gate forbids it) and diffed
 *    field-for-field.
 */
import { describe, it, expect } from 'vitest'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { SseParser } from '../../src/core/gateway.js'
import { startPolling } from '../../src/core/address-code.js'
import type { RequestClient } from '../../src/core/address-code.js'
import {
  PULL_HOLD_MAX_MS,
  PULL_HTTP_TIMEOUT_MS,
  PULL_STREAM_READ_TIMEOUT_MS,
  PULL_STREAM_PING_TIMEOUT_MS,
  PULL_REPROBE_INTERVAL_MS,
  PULL_MODE_ENV,
  parsePullMode,
  pullStreamBackoffSecs,
  pullPollBackoffSecs,
  pullDecideNextMode,
  type PullBatch,
  type StreamRoundResult,
} from '../../src/core/pull-contract.js'

// Repo root = 5 levels above test/core (…/tssdk/packages/<pkg>/test/core).
function repoRoot(): string {
  const here = path.dirname(fileURLToPath(import.meta.url))
  return path.resolve(here, '..', '..', '..', '..', '..')
}

function pysdkAvailable(): boolean {
  return existsSync(path.join(repoRoot(), 'pysdk', 'aimail_base.py'))
}

// ── 1. SSE frame parser (same vectors as py/bridge) ─────────────────────

const MAIL = (id: number): PullBatch[] => [
  { body: { subject: 'hi' }, deliveries: [{ id, email: 'a@x', headers: {} }] },
]

describe('SseParser', () => {
  it('a comment frame is a ping', () => {
    expect(new SseParser().feed(': ping\n\n')).toEqual([['ping', null]])
  })

  it('a single mail frame parses in one chunk', () => {
    const frame = 'event: mail\ndata: ' + JSON.stringify({ batches: MAIL(1) }) + '\n\n'
    const evs = new SseParser().feed(frame)
    expect(evs).toHaveLength(1)
    expect(evs[0][0]).toBe('mail')
    expect((evs[0][1] as PullBatch[])[0].deliveries[0].id).toBe(1)
  })

  it('a frame split across chunks is buffered to completion', () => {
    const raw = 'event: mail\ndata: ' + JSON.stringify({ batches: MAIL(9) }) + '\n\n'
    const p = new SseParser()
    const half = Math.floor(raw.length / 2)
    expect(p.feed(raw.slice(0, half))).toEqual([])
    const evs = p.feed(raw.slice(half))
    expect(evs).toHaveLength(1)
    expect((evs[0][1] as PullBatch[])[0].deliveries[0].id).toBe(9)
  })

  it('multiple events in one chunk all surface', () => {
    const chunk = ': ping\n\n' +
      'event: mail\ndata: {"batches":[]}\n\n' +
      'event: mail\ndata: ' + JSON.stringify({ batches: MAIL(2) }) + '\n\n'
    const evs = new SseParser().feed(chunk)
    expect(evs.map((e) => e[0])).toEqual(['ping', 'mail', 'mail'])
  })

  it('multi-line data joins with a newline (bad JSON ⇒ skipped, stream alive)', () => {
    const p = new SseParser()
    const bad = 'event: mail\ndata: {"batches": ["x\ndata: 1"]}\n\n'
    expect(p.feed(bad)).toEqual([])
    const ok = 'event: mail\ndata: ' + JSON.stringify({ batches: MAIL(3) }) + '\n\n'
    expect(p.feed(ok)).toHaveLength(1)
  })

  it('unknown events and bad data are skipped without killing the stream', () => {
    const p = new SseParser()
    expect(p.feed('event: other\ndata: {"batches":[]}\n\n')).toEqual([])
    expect(p.feed('event: mail\ndata: not-json\n\n')).toEqual([])
    const ok = 'event: mail\ndata: ' + JSON.stringify({ batches: MAIL(4) }) + '\n\n'
    expect(p.feed(ok)).toHaveLength(1)
  })

  it('bytes are buffered until the \\n\\n boundary', () => {
    const p = new SseParser()
    expect(p.feed(': ping\n')).toEqual([])
    expect(p.feed('\n')).toEqual([['ping', null]])
  })
})

// ── 2. contract constants (ratchet: gateway types.rs is the source) ─────

describe('contract constants', () => {
  it('hold / timeouts / reprobe mirror the gateway single source', () => {
    expect(PULL_HOLD_MAX_MS).toBe(50_000)
    expect(PULL_HTTP_TIMEOUT_MS).toBe(PULL_HOLD_MAX_MS + 15_000)
    expect(PULL_HTTP_TIMEOUT_MS).toBe(65_000)
    expect(PULL_STREAM_READ_TIMEOUT_MS).toBe(90_000)
    expect(PULL_STREAM_PING_TIMEOUT_MS).toBe(30_000)
    expect(PULL_REPROBE_INTERVAL_MS).toBe(600_000)
    expect(PULL_MODE_ENV).toBe('AIMAIL_PULL_MODE')
  })
})

// ── 3. decision functions (three-client parity vectors) ─────────────────

const DECIDE_VECTORS: Array<[string, 'ready' | 'unavailable' | null, number, [string, number]]> = [
  ['auto', 'ready', 0, ['stream', 0]],
  ['auto', 'ready', 3, ['stream', 0]],
  ['auto', 'unavailable', 0, ['poll', 0]],
  ['auto', 'unavailable', 5, ['poll', 0]],
  ['poll', 'ready', 0, ['poll', 0]], // kill switch beats everything
  ['poll', null, 9, ['poll', 0]],
  ['auto', null, 0, ['stream', 0]],
  ['auto', null, 1, ['stream', 1]],
  ['auto', null, 2, ['stream', 2]],
  ['auto', null, 3, ['poll', 0]], // 3 consecutive drops ⇒ Poll
  ['auto', null, 4, ['poll', 0]],
  ['stream', null, 1, ['stream', 1]], // forced: never downgrades
  ['stream', null, 3, ['stream', 4]],
  ['stream', null, 10, ['stream', 30]], // capped
]

describe('pullDecideNextMode', () => {
  for (const [mode, probe, streak, want] of DECIDE_VECTORS) {
    it(`(${mode}, ${probe}, ${streak}) ⇒ ${JSON.stringify(want)}`, () => {
      expect(pullDecideNextMode(mode as never, probe, streak)).toEqual(want)
    })
  }
})

describe('pullStreamBackoffSecs', () => {
  for (const [streak, want] of [
    [0, 0], [1, 1], [2, 2], [3, 4], [4, 8], [5, 16], [6, 30], [7, 30], [9, 30],
  ] as const) {
    it(`streak ${streak} ⇒ ${want}s`, () => {
      expect(pullStreamBackoffSecs(streak)).toBe(want)
    })
  }
})

describe('pullPollBackoffSecs', () => {
  for (const [interval, failures, want] of [
    [30, 0, 30], [30, 1, 60], [30, 2, 120], [30, 3, 240],
    [30, 4, 300], [30, 6, 300], [1, 0, 1], [1, 6, 64],
  ] as const) {
    it(`interval ${interval}s failures ${failures} ⇒ ${want}s`, () => {
      expect(pullPollBackoffSecs(interval, failures)).toBe(want)
    })
  }
})

describe('parsePullMode', () => {
  const V: Array<[string | undefined, string]> = [
    [undefined, 'auto'], ['', 'auto'], ['AUTO', 'auto'], ['stream', 'stream'],
    ['STREAM', 'stream'], ['poll', 'poll'], ['Poll ', 'poll'],
    ['bogus', 'auto'], ['streaming', 'auto'],
  ]
  for (const [raw, want] of V) {
    it(`"${raw}" ⇒ ${want}`, () => {
      expect(parsePullMode(raw)).toBe(want)
    })
  }
})

// ── 4. startPolling state machine (scripted stream rounds) ──────────────

interface FakeCounters {
  streamCalls: number
  pendingHits: number
  postBodies: Array<Record<string, unknown>>
  ackedIds: number[]
}

function makeClient(
  script: StreamRoundResult[],
  pending: PullBatch[][],
  withStream = true,
): RequestClient & { counters: FakeCounters } {
  const counters: FakeCounters = { streamCalls: 0, pendingHits: 0, postBodies: [], ackedIds: [] }
  let pendingIdx = 0
  const client: RequestClient & { counters: FakeCounters } = {
    baseUrl: 'http://127.0.0.1:1',
    counters,
    async request(method, requestPath, body) {
      void method
      if (requestPath === '/api/v1/admin/pending') {
        counters.pendingHits++
        counters.postBodies.push(body ?? {})
        const batches = pending[pendingIdx] ?? []
        pendingIdx++
        return { status: 200, batches }
      }
      if (requestPath === '/api/v1/admin/pending/ack') {
        const ids = ((body ?? {}) as { ids?: number[] }).ids ?? []
        counters.ackedIds.push(...ids)
        return { status: 200, acked: ids.length }
      }
      return { status: 404, error: 'nf' }
    },
  }
  if (withStream) {
    client.streamRound = async () => script[counters.streamCalls++] ?? 'stay'
    client.streamProbe = async () => false
  }
  return client
}

const B1: PullBatch[] = [{ body: {}, deliveries: [{ id: 1, email: 'a@x', headers: {} }] }]

describe('startPolling state machine', () => {
  it('auto: 3 consecutive mid-stream errors ⇒ Poll (streak persists across reconnects)', async () => {
    const c = makeClient(['error', 'error', 'error'], [B1])
    const got: number[] = []
    const t0 = Date.now()
    const stats = await startPolling(c, (m) => { got.push(m.id) }, {
      intervalMs: 5, maxRounds: 2, mode: 'auto',
    })
    const elapsed = Date.now() - t0
    expect(c.counters.streamCalls).toBe(3) // exactly 3 drop attempts
    expect(got).toEqual([1]) // delivered via the poll fallback
    expect(stats.acked).toBe(1)
    expect(elapsed).toBeLessThan(10_000) // backoffs 1s+2s, no 600s re-probe wait
  }, 15_000)

  it('auto: a healthy rotation RESETS the streak (5 attempts before downgrade)', async () => {
    // error, stay, error, error, error: without the reset the 3 consecutive
    // drops complete at attempt 4; with it (plan 0.3 B, bridge parity) the
    // run only completes at attempt 5.
    const c = makeClient(['error', 'stay', 'error', 'error', 'error'], [B1])
    const got: number[] = []
    const stats = await startPolling(c, (m) => { got.push(m.id) }, {
      intervalMs: 5, maxRounds: 2, mode: 'auto',
    })
    expect(c.counters.streamCalls).toBe(5)
    expect(got).toEqual([1])
    expect(stats.acked).toBe(1)
  }, 20_000)

  it('stream mode: mid-stream errors never downgrade (backoff forever)', async () => {
    const c = makeClient(['error', 'error', 'error', 'error'], [])
    const ac = new AbortController()
    const run = startPolling(c, () => {}, { intervalMs: 5, mode: 'stream', signal: ac.signal })
    const deadline = Date.now() + 10_000
    while (c.counters.streamCalls < 3 && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 25))
    }
    ac.abort()
    await run
    expect(c.counters.streamCalls).toBeGreaterThanOrEqual(3)
    expect(c.counters.pendingHits).toBe(0) // never fell to the poll branch
  }, 15_000)

  it('a connect drop goes straight to Poll (no streak, no backoff)', async () => {
    const c = makeClient(['drop'], [B1])
    const got: number[] = []
    const stats = await startPolling(c, (m) => { got.push(m.id) }, {
      intervalMs: 5, maxRounds: 2, mode: 'auto',
    })
    expect(c.counters.streamCalls).toBe(1)
    expect(got).toEqual([1])
    expect(stats.acked).toBe(1)
  })

  it('auto: repeated 409 occupied stays in stream, NEVER downgrades', async () => {
    // The single-slot lock is held by ANOTHER client: every stream round
    // reports 'occupied'. The loop stays in the current mode and backs off
    // against the SAME endpoint (the slot frees via the holder's lease/grace)
    // — occupied is NOT a transport error, so the drop streak never accrues
    // and the poll branch is never entered. Contrast the 3-error downgrade
    // above: with maxRounds=2 an error script would already be in poll; the
    // occupied script must still be holding the stream endpoint (no pending
    // hits, no downgrade).
    const c = makeClient(['occupied', 'occupied', 'occupied'], [B1])
    const ac = new AbortController()
    const run = startPolling(c, () => {}, { intervalMs: 5, mode: 'auto', signal: ac.signal })
    const deadline = Date.now() + 10_000
    while (c.counters.streamCalls < 3 && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 25))
    }
    ac.abort()
    await run
    expect(c.counters.streamCalls).toBeGreaterThanOrEqual(3)
    expect(c.counters.pendingHits).toBe(0) // never fell to the poll branch
  }, 15_000)

  it('a healthy rotation resets BOTH streaks (occupied + transport)', async () => {
    // occupied, stay, occupied: the healthy rotation breaks the occupied
    // run (fresh backoff ramp). Pins the `stay` reset of occupiedStreak —
    // without it the 3rd occupied would ramp from a higher backoff.
    const c = makeClient(['occupied', 'stay', 'occupied'], [B1])
    const ac = new AbortController()
    const run = startPolling(c, () => {}, { intervalMs: 5, mode: 'auto', signal: ac.signal })
    const deadline = Date.now() + 10_000
    while (c.counters.streamCalls < 3 && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 25))
    }
    ac.abort()
    await run
    expect(c.counters.streamCalls).toBeGreaterThanOrEqual(3)
    expect(c.counters.pendingHits).toBe(0)
  }, 15_000)

  it('a client without stream methods is poll-only (no probing)', async () => {
    const c = makeClient([], [B1], false)
    const got: number[] = []
    const stats = await startPolling(c, (m) => { got.push(m.id) }, {
      intervalMs: 5, maxRounds: 2, mode: 'auto',
    })
    expect(got).toEqual([1])
    expect(stats.acked).toBe(1)
    expect(c.counters.pendingHits).toBeGreaterThanOrEqual(1)
  })

  it('the long-pull body carries the contract hold', async () => {
    const c = makeClient(['drop'], [[], []])
    const ac = new AbortController()
    const run = startPolling(c, () => {}, { intervalMs: 10, maxRounds: 1, mode: 'poll', signal: ac.signal })
    await run
    expect(c.counters.postBodies.length).toBeGreaterThanOrEqual(1)
    expect(c.counters.postBodies[0].timeout_ms).toBe(PULL_HOLD_MAX_MS)
  })
})

// ── 5. py↔ts parity (stdin python3 — no .py file lands in the TS tree) ──

const PY_HELPER = `
import sys, os, json
case = json.loads(sys.argv[1])
sys.path.insert(0, os.path.join(os.environ['AIMAIL_REPO_ROOT'], 'pysdk'))
import aimail_base as ab
print(json.dumps({
    "mode": [ab.parse_pull_mode(x) for x in case["mode_in"]],
    "decide": [[m, d] for (m, d) in (ab.pull_decide_next_mode(o, p, s) for (o, p, s) in case["decide_in"])],
    "stream_backoff": [ab.pull_stream_backoff_secs(x) for x in case["stream_in"]],
    "poll_backoff": [ab.pull_poll_backoff_secs(i, f) for (i, f) in case["poll_in"]],
    "constants": {k: getattr(ab, k) for k in case["consts"]},
}))
`

function pyRun(cfg: Record<string, unknown>): Record<string, unknown> {
  const out = execFileSync('python3', ['-', JSON.stringify(cfg)], {
    input: PY_HELPER,
    encoding: 'utf-8',
    timeout: 30_000,
    env: { ...process.env, AIMAIL_REPO_ROOT: repoRoot() },
  })
  return JSON.parse(out.trim())
}

describe('py↔ts parity (same input vectors, identical outputs)', () => {
  const pyOk = pysdkAvailable()
  if (!pyOk) {
    it.skip('pysdk available for parity', () => {})
    return
  }

  it('decision functions + backoffs + parse_pull_mode + constants', () => {
    const modeIn: Array<string | null> = [
      null, '', 'AUTO', 'stream', 'STREAM', 'poll', 'Poll ', 'bogus', 'streaming',
    ]
    const cfg = {
      mode_in: modeIn,
      decide_in: DECIDE_VECTORS.map((v) => [v[0], v[1], v[2]]),
      stream_in: [0, 1, 2, 3, 4, 5, 6, 7, 9],
      poll_in: [[30, 0], [30, 1], [30, 2], [30, 3], [30, 4], [30, 6], [1, 0], [1, 6]],
      consts: ['PULL_HOLD_MAX_MS', 'PULL_HTTP_TIMEOUT_S', 'PULL_STREAM_READ_TIMEOUT_S',
        'PULL_STREAM_PING_TIMEOUT_S', 'PULL_REPROBE_INTERVAL_S'],
    }
    const py = pyRun(cfg)

    // parse_pull_mode: null in the JSON = py None = ts undefined.
    const tsMode = modeIn.map((x) => parsePullMode(x === null ? undefined : x))
    expect(tsMode).toEqual(py.mode as string[])

    const tsDecide = DECIDE_VECTORS.map((v) => pullDecideNextMode(v[0] as never, v[1], v[2]))
    expect(tsDecide).toEqual(py.decide as Array<[string, number]>)

    const tsStream = cfg.stream_in.map((x) => pullStreamBackoffSecs(x))
    expect(tsStream).toEqual(py.stream_backoff as number[])

    const tsPoll = cfg.poll_in.map(([i, f]) => pullPollBackoffSecs(i, f))
    expect(tsPoll).toEqual(py.poll_backoff as number[])

    // Constants: py keeps seconds, TS keeps milliseconds — compare in ms.
    const c = py.constants as Record<string, number>
    expect(PULL_HOLD_MAX_MS).toBe(c.PULL_HOLD_MAX_MS)
    expect(PULL_HTTP_TIMEOUT_MS).toBe(c.PULL_HTTP_TIMEOUT_S * 1000)
    expect(PULL_STREAM_READ_TIMEOUT_MS).toBe(c.PULL_STREAM_READ_TIMEOUT_S * 1000)
    expect(PULL_STREAM_PING_TIMEOUT_MS).toBe(c.PULL_STREAM_PING_TIMEOUT_S * 1000)
    expect(PULL_REPROBE_INTERVAL_MS).toBe(c.PULL_REPROBE_INTERVAL_S * 1000)
  })
})

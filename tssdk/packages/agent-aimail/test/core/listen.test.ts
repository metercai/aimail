/**
 * listen.ts — conflict-tolerant inbound bind (owner ruling 2026-10-25).
 *
 * Pinned contracts (L1, no container, loopback only):
 * 1. Free preferred port binds first try (the common default path).
 * 2. OCCUPIED preferred port ⇒ re-select port+1; the returned port is the
 *    ACTUAL bound port (source of truth), not the preferred one.
 * 3. N consecutive occupied ports ⇒ binds at preferred+N (the walk continues).
 * 4. Exhausting maxAttempts on a fully-occupied range ⇒ rejects with a loud
 *    error (the host must NOT report inbound live — silent black hole).
 * 5. Invalid preferred port (<1 or >=65536) ⇒ throws (caller defect).
 * 6. The returned server is really listening on the reported port (an HTTP
 *    request to it succeeds) — the "port is up" the caller reports is real.
 * 7. Concurrency: two listenWithRetry on the same preferred port both resolve,
 *    on DIFFERENT ports (the OS bind is the atomic arbiter — no double-bind).
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import { createServer, type Server } from 'node:http'
import { listenWithRetry } from '../../src/core/index.js'

/** Find a free loopback port (bind :0, read, close). */
function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const s = createServer()
    s.once('error', reject)
    s.listen(0, '127.0.0.1', () => {
      const { port } = s.address() as { port: number }
      s.close(() => resolve(port))
    })
  })
}

/** Occupy a port for the duration of a test. */
function occupy(port: number): Promise<Server> {
  return new Promise((resolve, reject) => {
    const s = createServer()
    s.once('error', reject)
    s.listen(port, '127.0.0.1', () => resolve(s))
  })
}

const servers: Server[] = []
function track(s: Server): Server {
  servers.push(s)
  return s
}
afterAll(async () => {
  for (const s of servers) await new Promise((r) => s.close(() => r(undefined)))
})

describe('listenWithRetry', () => {
  it('binds a free preferred port on the first try', async () => {
    const p = await freePort()
    const server = createServer()
    track(server)
    const actual = await listenWithRetry(server, '127.0.0.1', p)
    expect(actual).toBe(p)
  })

  it('re-selects port+1 when the preferred port is occupied (actual port is truth)', async () => {
    const p = await freePort()
    const blocker = track(await occupy(p))
    const server = createServer()
    track(server)
    const actual = await listenWithRetry(server, '127.0.0.1', p)
    expect(actual).toBe(p + 1) // NOT the preferred (dead) port
  })

  it('walks past N occupied ports to the first free one', async () => {
    const base = await freePort()
    const blockers = [track(await occupy(base)), track(await occupy(base + 1))]
    const server = createServer()
    track(server)
    const actual = await listenWithRetry(server, '127.0.0.1', base)
    expect(actual).toBe(base + 2)
    for (const b of blockers) await new Promise((r) => b.close(() => r(undefined)))
  })

  it('rejects loudly when every probed port is occupied (no silent black hole)', async () => {
    const base = await freePort()
    // occupy base and base+1, allow only 2 attempts
    const blockers = [track(await occupy(base)), track(await occupy(base + 1))]
    const server = createServer()
    track(server)
    await expect(listenWithRetry(server, '127.0.0.1', base, { maxAttempts: 2 }))
      .rejects.toThrow(/no free port/)
    for (const b of blockers) await new Promise((r) => b.close(() => r(undefined)))
  })

  it('throws on an invalid preferred port (caller defect)', async () => {
    const server = createServer()
    track(server)
    await expect(listenWithRetry(server, '127.0.0.1', 0)).rejects.toThrow(/invalid preferred port/)
    await expect(listenWithRetry(server, '127.0.0.1', 70000)).rejects.toThrow(/invalid preferred port/)
  })

  it('the reported port really serves (HTTP round-trip to it)', async () => {
    const p = await freePort()
    let flag = 0
    const server = createServer((req, res) => {
      flag = 1
      res.end('ok')
    })
    track(server)
    const actual = await listenWithRetry(server, '127.0.0.1', p)
    const body = await (await fetch(`http://127.0.0.1:${actual}/`)).text()
    expect(body).toBe('ok')
    expect(flag).toBe(1)
  })

  it('two concurrent starters on the same preferred port land on different ports', async () => {
    const p = await freePort()
    const a = createServer()
    const b = createServer()
    track(a)
    track(b)
    const [pa, pb] = await Promise.all([
      listenWithRetry(a, '127.0.0.1', p),
      listenWithRetry(b, '127.0.0.1', p),
    ])
    expect(pa).not.toBe(pb) // the bind is the atomic arbiter — no double-bind
    expect([pa, pb].sort((x, y) => x - y)).toEqual([p, p + 1])
  })
})

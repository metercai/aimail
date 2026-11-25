#!/usr/bin/env node
/**
 * racp_probe.mjs — step-0 probe for a live PI-Desktop host (plan §9 step 0).
 *
 * It answers the three questions the plan cannot answer from source reading alone:
 *   1. can a COLD (never loaded) session be woken with turn/start?
 *   2. does `admission: "queue"` queue a second turn instead of rejecting it?
 *   3. does a repeated `idempotencyKey` take effect only once?
 *
 * Usage
 *   node racp_probe.mjs --url ws://127.0.0.1:45861 --token <device token>
 *   node racp_probe.mjs --url ws://127.0.0.1:45861 --pairing ppt1.xxxx   # one-shot pairing
 *
 * Token sources: the device token a paired client holds, or a pairing token printed by
 * the host (`PI_HOST_PAIRING {...}` at startup / produced by the UI's "add device").
 * The token travels in the WebSocket upgrade request: `Authorization: Bearer <token>`.
 *
 * Exit: 0 = all probes ran, 1 = a probe failed, 2 = usage/transport.
 */
import { parseArgs } from 'node:util'

const { values: args } = parseArgs({
  options: {
    url: { type: 'string', default: 'ws://127.0.0.1:45861/v1/racp/ws' },
    token: { type: 'string' },
    pairing: { type: 'string' },
    label: { type: 'string', default: 'aimail-probe' },
    keep: { type: 'boolean', default: false },
    timeout: { type: 'string', default: '180000' },
  },
})

if (!args.token && !args.pairing) {
  console.error('need --token <device token> or --pairing <pairing token>')
  process.exit(2)
}

const TIMEOUT_MS = Number(args.timeout)
const bearer = args.token ?? args.pairing

/** Minimal JSON-RPC 2.0 over one WebSocket, with per-request timeouts. */
/**
 * WebSocket factory. Node's global WebSocket drops the custom `headers` option, which is
 * exactly the header carrying the bearer token — so prefer the `ws` package when it is
 * resolvable (`WS_DIR` points at a directory that contains it, keeping the repo dep-free).
 */
async function loadWebSocket() {
  try {
    const { createRequire } = await import('node:module')
    const req = createRequire(`${process.env.WS_DIR ?? process.cwd()}/`)
    return req('ws')
  } catch {
    return globalThis.WebSocket
  }
}

function connect(WS, url, token) {
  // Exact upgrade contract (packages/racp/src/ws-binding.ts + shared/racp.ts):
  //   path         /v1/racp/ws            (anything else ⇒ 404 "Not Found")
  //   subprotocol  pi-racp.v1.jsonrpc     (missing/wrong ⇒ 1006)
  //   header       Authorization: Bearer <device|pairing token>
  const ws = new WS(url, ['pi-racp.v1.jsonrpc'], { headers: { authorization: `Bearer ${token}` } })
  const pending = new Map()
  const events = []
  let seq = 0
  const waiters = []

  ws.addEventListener('message', (ev) => {
    let msg
    try {
      msg = JSON.parse(typeof ev.data === 'string' ? ev.data : String(ev.data))
    } catch {
      return
    }
    if (msg.id !== undefined && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(Object.assign(new Error(msg.error.message ?? 'rpc error'), { code: msg.error.code })) : resolve(msg.result)
      return
    }
    events.push(msg)
    for (const w of [...waiters]) {
      if (w.match(msg)) {
        waiters.splice(waiters.indexOf(w), 1)
        w.resolve(msg)
      }
    }
  })

  const opened = new Promise((resolve, reject) => {
    ws.addEventListener('open', () => resolve())
    ws.addEventListener('error', () => reject(new Error('websocket error (bad token or wrong url?)')))
    ws.addEventListener('close', (e) => reject(new Error(`websocket closed ${e.code} ${e.reason ?? ''}`)))
  })

  function call(method, params = {}) {
    const id = `c${++seq}`
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject })
      const timer = setTimeout(() => {
        pending.delete(id)
        reject(new Error(`${method} timed out`))
      }, TIMEOUT_MS)
      const done = (fn) => (v) => {
        clearTimeout(timer)
        fn(v)
      }
      pending.set(id, { resolve: done(resolve), reject: done(reject) })
      ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }))
    })
  }

  function waitFor(match, timeoutMs = TIMEOUT_MS) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('event wait timed out')), timeoutMs)
      waiters.push({ match, resolve: (m) => { clearTimeout(timer); resolve(m) } })
    })
  }

  return { ws, opened, call, waitFor, events, subscribe: (m) => ws.send(JSON.stringify(m)) }
}

const kindOf = (msg) => msg?.method ?? msg?.params?.event?.type ?? msg?.params?.kind ?? ''
const isTurnDone = (msg) => {
  const k = String(kindOf(msg))
  return k === 'turn.completed' || k === 'turn.failed' || k.includes('turn.completed') || k.includes('turn.failed')
}

const results = {}
function record(name, ok, detail) {
  results[name] = { ok, detail }
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`)
}

const WS = await loadWebSocket()
const initialize = (c) =>
  c.call('connection/initialize', {
    protocolVersion: '1.0',
    client: { name: 'aimail-probe', version: '0.0.0' },
    bindings: ['RACP-WS'],
    capabilities: {},
  })

let rpc = connect(WS, args.url, bearer)
try {
  await rpc.opened
} catch (e) {
  console.error(`cannot connect: ${e.message}`)
  process.exit(2)
}

let token = args.token
try {
  let init = await initialize(rpc)
  console.log(`connected: host=${init?.server?.hostId ?? '?'} proto=${init?.protocolVersion ?? '?'}`)

  if (!token) {
    const paired = await rpc.call('connection/pair', { deviceLabel: args.label })
    token = paired?.deviceToken ?? paired?.token
    record('pair', Boolean(token), token ? 'device token issued (store it, pairing tokens are single use)' : 'no token in reply')
    if (!token) throw new Error('pairing produced no device token')
    // The pairing connection stays an unprivileged "pairing" principal: the device token
    // only gets its roles on a NEW connection. Reconnect before doing anything else.
    rpc.ws.close()
    await new Promise((r) => setTimeout(r, 200))
    rpc = connect(WS, args.url, token)
    await rpc.opened
    init = await initialize(rpc)
    console.log(`reconnected as device: host=${init?.server?.hostId ?? '?'}`)
  }

  await rpc.call('events/subscribe', { scope: 'host' })

  // 1) cold session + turn (the "wake a session that was never loaded" case)
  const ctx = (n) => ({ requestId: `probe-${n}-${Date.now()}`, idempotencyKey: `aimail-probe-${n}` })
  const created = await rpc.call('session/create', { title: 'aimail probe', context: ctx('create') })
  const sessionId = created?.session?.id
  if (!sessionId) throw new Error('session/create returned no id')
  const t0 = Date.now()
  const first = await rpc.call('turn/start', {
    sessionId,
    idempotencyKey: 'aimail-probe-1',
    context: ctx('turn1'),
    input: { text: 'Reply with exactly: PROBE-OK' },
  })
  record('cold turn/start accepted', Boolean(first?.turn?.id), `turn=${first?.turn?.id ?? '-'} (${Date.now() - t0}ms)`)
  // Poll the turn record rather than relying on event plumbing: events are notifications
  // whose shape varies, while `turn/get` is the contract's own state source.
  const turnId = first.turn.id
  let state = 'accepted'
  const deadline = Date.now() + TIMEOUT_MS
  const seen = new Set()
  while (Date.now() < deadline) {
    const got = await rpc.call('turn/get', { turnId }).catch(() => null)
    const status = String(got?.turn?.status ?? '')
    if (status && !seen.has(status)) {
      seen.add(status)
      state = status
    }
    if (['completed', 'failed', 'canceled', 'interrupted', 'stopped'].includes(status)) break
    await new Promise((r) => setTimeout(r, 2000))
  }
  record('cold turn terminal', ['completed', 'failed'].includes(state), `states=${[...seen].join('→') || 'none'}`)

  // 2) queue: a second turn while the first is (or was) busy
  const second = await rpc.call('turn/start', {
    sessionId,
    idempotencyKey: 'aimail-probe-2',
    admission: 'queue',
    context: ctx('turn2'),
    input: { text: 'Reply with exactly: PROBE-2' },
  })
  record('admission=queue accepted', Boolean(second?.turn?.id ?? second?.queued), JSON.stringify(second?.turn?.status ?? second).slice(0, 60))

  // 3) idempotency: the same key twice must not create a second turn
  const again = await rpc.call('turn/start', {
    sessionId,
    idempotencyKey: 'aimail-probe-1',
    context: ctx('turn1'),
    input: { text: 'Reply with exactly: PROBE-OK' },
  })
  const sameTurn = String(again?.turn?.id ?? '') === String(first?.turn?.id ?? '')
  record('idempotencyKey deduplicates', sameTurn, `first=${first?.turn?.id ?? '-'} again=${again?.turn?.id ?? '-'}`)

  const history = await rpc.call('session/history', { sessionId, limit: 10 })
  record('session/history readable', true, `${Array.isArray(history?.messages) ? history.messages.length : '?'} message(s)`)

  // The queued turn is still running: deleting a session with an active turn is refused
  // ("the session has an active turn"), so drain it first — that also proves the queue ran.
  const queuedId = second?.turn?.id
  if (queuedId) {
    const until = Date.now() + TIMEOUT_MS
    let q = ''
    while (Date.now() < until) {
      const g = await rpc.call('turn/get', { turnId: queuedId }).catch(() => null)
      q = String(g?.turn?.status ?? '')
      if (['completed', 'failed', 'canceled', 'interrupted', 'stopped'].includes(q)) break
      await new Promise((r) => setTimeout(r, 2000))
    }
    record('queued turn drained', ['completed', 'failed'].includes(q), `status=${q || 'unknown'}`)
  }

  if (!args.keep) {
    await rpc.call('session/delete', { sessionId, context: ctx('delete') })
    console.log('probe session deleted')
  } else {
    console.log(`probe session kept: ${sessionId}`)
  }
} catch (e) {
  record('probe run', false, e?.message ?? String(e))
  process.exitCode = 1
} finally {
  rpc.ws.close()
}

const failed = Object.entries(results).filter(([, r]) => !r.ok)
console.log(`\n── summary: ${Object.keys(results).length - failed.length} passed, ${failed.length} failed`)
if (!args.token && failed.length === 0) {
  console.log('NOTE: this run paired a device. Re-run with --token <device token> to avoid pairing again.')
}
process.exit(failed.length === 0 ? 0 : 1)

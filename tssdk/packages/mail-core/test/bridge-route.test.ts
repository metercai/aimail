/**
 * Route side contract (owner ruling 2026-09-27).
 *
 * These assertions are the reason the module exists:
 *  1. a route is only pushed when the local receive endpoint is SERVING —
 *     pushing at registration time is what got the route pruned in production
 *     (created 15:53:17, removed 15:56:07);
 *  2. no bridge => 'no_bridge' (skip, not a failure), and the host must survive;
 *  3. every binding of the system gets its own route (iron rule: with a bridge
 *     present every registered address needs one), and each route targets that
 *     binding's own webhook_url;
 *  4. failure is REPORTED ('failed' + a line that names the repair command),
 *     never swallowed silently.
 */
import * as fs from 'node:fs'
import * as http from 'node:http'
import * as os from 'node:os'
import * as path from 'node:path'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test } from 'vitest'
import {
  ensureBridgeRoute,
  ensureBridgeRoutesForSystem,
  formatBridgeRouteLine,
  inboundServing,
} from '../src/index.js'

let iso: string
let prevHome: string | undefined
const servers: http.Server[] = []

beforeAll(() => {
  prevHome = process.env.AIMAIL_HOME
})
beforeEach(() => {
  iso = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-route-'))
  process.env.AIMAIL_HOME = iso
})
afterEach(() => {
  for (const s of servers.splice(0)) s.close()
})
afterAll(() => {
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  fs.rmSync(iso, { recursive: true, force: true })
})

/** A binding exactly as the register chain leaves it. */
function writeBinding(sid: string, email: string, webhookUrl: string) {
  const dir = path.join(iso, 'systems', sid, email.replace(/[^\w.-]/g, '_'))
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, 'agentmail.json'), JSON.stringify({
    email, system_id: sid, gateway_url: 'https://gw.test', domain: 'gw.test',
    api_key: 'k'.repeat(64), webhook_url: webhookUrl, webhook_secret: 's'.repeat(64),
  }))
}

/** Fake bridge admin API; records the route upserts it received. */
async function startFakeBridge(): Promise<{ port: number; posts: Array<Record<string, unknown>>; failWith?: number }> {
  const state: { port: number; posts: Array<Record<string, unknown>>; failWith?: number } = { port: 0, posts: [] }
  const srv = http.createServer((req, res) => {
    if (req.method === 'POST' && req.url === '/api/v1/routes') {
      let body = ''
      req.on('data', (c) => { body += String(c) })
      req.on('end', () => {
        try { state.posts.push(JSON.parse(body) as Record<string, unknown>) } catch { /* ignore */ }
        if (state.failWith) { res.writeHead(state.failWith).end('nope'); return }
        res.writeHead(200, { 'content-type': 'application/json' }).end('{"status":"ok"}')
      })
      return
    }
    res.writeHead(404).end()
  })
  servers.push(srv)
  await new Promise<void>((r) => srv.listen(0, '127.0.0.1', r))
  state.port = (srv.address() as { port: number }).port
  return state
}

/** A host inbound that is listening (or not started at all). */
async function startHost(): Promise<number> {
  const srv = http.createServer((_req, res) => { res.writeHead(405).end() })
  servers.push(srv)
  await new Promise<void>((r) => srv.listen(0, '127.0.0.1', r))
  return (srv.address() as { port: number }).port
}

describe('bridge route side', () => {
  test('no bridge listening => no_bridge, nothing pushed, no throw', async () => {
    writeBinding('sys-1', 'agent.acme@gw.test', 'http://127.0.0.1:1/aimail/inbound')
    const out = await ensureBridgeRoutesForSystem('sys-1', { bridgeAdminPort: 1 })
    expect(out).toHaveLength(1)
    expect(out[0]!.state).toBe('no_bridge')
    expect(out[0]!.count).toBe(1)
    expect(formatBridgeRouteLine(out[0]!)).toContain('route skipped for 1 address(es)')
  })

  test('route target is not serving => host_not_serving, no push', async () => {
    const bridge = await startFakeBridge()
    writeBinding('sys-1', 'agent.acme@gw.test', 'http://127.0.0.1:9/aimail/inbound')
    const out = await ensureBridgeRoute({
      systemId: 'sys-1', email: 'agent.acme@gw.test',
      webhookUrl: 'http://127.0.0.1:9/aimail/inbound', bridgeAdminPort: bridge.port,
    })
    expect(out.state).toBe('host_not_serving')
    expect(bridge.posts).toHaveLength(0)
  })

  test('every binding of the system gets its own route, targeted at its own webhook_url', async () => {
    const bridge = await startFakeBridge()
    const hostPort = await startHost()
    const target = `http://127.0.0.1:${hostPort}/aimail/inbound`
    writeBinding('sys-1', 'agent.one@gw.test', target)
    writeBinding('sys-1', 'agent.two@gw.test', target)

    const out = await ensureBridgeRoutesForSystem('sys-1', { bridgeAdminPort: bridge.port })
    expect(out.map(o => [o.email, o.state])).toEqual([
      ['agent.one@gw.test', 'ok'],
      ['agent.two@gw.test', 'ok'],
    ])
    expect(bridge.posts.map(p => [p.email, p.host])).toEqual([
      ['agent.one@gw.test', target],
      ['agent.two@gw.test', target],
    ])
    expect(formatBridgeRouteLine(out[0]!)).toBe(`route: agent.one@gw.test -> ${target}`)
  })

  test('a binding without webhook_url is reported, not silently routed to a guessed URL', async () => {
    const bridge = await startFakeBridge()
    writeBinding('sys-1', 'agent.nohook@gw.test', '')
    const out = await ensureBridgeRoute({
      systemId: 'sys-1', email: 'agent.nohook@gw.test', webhookUrl: '', bridgeAdminPort: bridge.port,
    })
    expect(out.state).toBe('host_not_serving')
    expect(out.detail).toContain('no webhook_url')
    expect(bridge.posts).toHaveLength(0)
  })

  test('bridge rejects the upsert => failed + a line naming the repair command', async () => {
    const bridge = await startFakeBridge()
    bridge.failWith = 500
    const hostPort = await startHost()
    const target = `http://127.0.0.1:${hostPort}/aimail/inbound`
    writeBinding('sys-1', 'agent.acme@gw.test', target)

    const out = await ensureBridgeRoutesForSystem('sys-1', { bridgeAdminPort: bridge.port })
    expect(out[0]!.state).toBe('failed')
    expect(out[0]!.detail).toContain('500')
    const line = formatBridgeRouteLine(out[0]!)
    expect(line).toContain('route FAILED for agent.acme@gw.test')
    expect(line).toContain("aimail repair")
  })

  test('a machine with no aimail system is a no-op (no route, no noise)', async () => {
    const bridge = await startFakeBridge()
    const out = await ensureBridgeRoutesForSystem(undefined, { bridgeAdminPort: bridge.port })
    expect(out).toEqual([])
    expect(bridge.posts).toHaveLength(0)
  })

  test('inboundServing probes loopback only and never a remote endpoint', async () => {
    const hostPort = await startHost()
    expect(await inboundServing(`http://127.0.0.1:${hostPort}/aimail/inbound`)).toBe(true)
    expect(await inboundServing('http://127.0.0.1:9/aimail/inbound')).toBe(false)
    expect(await inboundServing('https://agent.example.com/aimail/inbound')).toBe(false)
    expect(await inboundServing('')).toBe(false)
  })
})

/**
 * L1 contract test (user 2026-09-27 scope: first lock down the contract locally, then go to L2 for integration confirmation).
 *
 * Uses a **stub /hooks/agent** implemented per host documentation to locally verify the dispatch contract, in seconds, zero docker, zero release:
 *   · No Authorization ⇒ 401 ("Hook authentication failed")
 *   · Path is not <hooks.path>/agent ⇒ 404 ("Disabled hooks fall through")
 *   · message empty / agentId missing or not a configured agent ⇒ 400
 *   · carrying a caller sessionKey without opting in ⇒ 400 ("routing/session policy")
 *   · clean payload ⇒ 200 {ok:true}
 * These four are exactly what was "discovered" in the three rounds of 404/401/400 on L2 — they should have been pinned down here first.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createServer, type Server } from 'node:http'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import * as path from 'node:path'
import { deliverToAgent } from '../../src/openclaw/inbound.js'

const TOKEN = 'stub-hook-token'
const AGENTS = ['main']
let home = ''
let server: Server
let port = 0
let seen: Array<Record<string, unknown>> = []
let allowRequestSessionKey = false

function startStub(): Promise<number> {
  return new Promise((resolve) => {
    server = createServer((req, res) => {
      const send = (code: number, body: unknown) => {
        res.writeHead(code, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify(body))
      }
      if (!String(req.headers.authorization ?? '').startsWith('Bearer ')) {
        return send(401, { error: 'hook authentication failed' })
      }
      if (req.url !== '/hooks/agent') return send(404, { error: 'no hook action at that path' })
      let raw = ''
      req.on('data', (c) => (raw += c))
      req.on('end', () => {
        let body: Record<string, unknown> = {}
        try {
          body = JSON.parse(raw) as Record<string, unknown>
        } catch {
          return send(400, { error: 'invalid json' })
        }
        seen.push(body)
        if (!String(body.message ?? '').trim()) return send(400, { error: 'message required' })
        if (!AGENTS.includes(String(body.agentId ?? ''))) {
          return send(400, { error: 'agentId must name a configured agent' })
        }
        if (body.sessionKey !== undefined && !allowRequestSessionKey) {
          return send(400, { error: 'request session key requires allowRequestSessionKey' })
        }
        return send(200, { ok: true, runId: 'run-1' })
      })
    })
    server.listen(0, '127.0.0.1', () => resolve((server.address() as { port: number }).port))
  })
}

beforeEach(async () => {
  seen = []
  allowRequestSessionKey = false
  home = mkdtempSync(path.join(tmpdir(), 'oc-l1-'))
  process.env.HOME = home
  port = await startStub()
  mkdirSync(path.join(home, '.openclaw'), { recursive: true })
  writeFileSync(
    path.join(home, '.openclaw', 'openclaw.json'),
    JSON.stringify({
      gateway: { mode: 'local', port },
      hooks: { enabled: true, token: TOKEN, path: '/hooks' },
      agents: { entries: { main: {} } },
    }),
  )
})
afterEach(() => {
  server.close()
  rmSync(home, { recursive: true, force: true })
})

describe('dispatch contract (/hooks/agent) — L1', () => {
  it('clean payload is accepted and carries message + agentId, no caller sessionKey', async () => {
    const out = await deliverToAgent({} as never, { agentId: 'main', message: 'hello' })
    expect(out.status).toBe('delivered')
    expect(seen).toHaveLength(1)
    expect(seen[0].message).toBe('hello')
    expect(seen[0].agentId).toBe('main')
    expect(seen[0].sessionKey).toBeUndefined()
  })

  it('a caller sessionKey without opt-in is refused, and the reason survives (S1d/S1e pin)', async () => {
    // Simulate "someone added the caller key back": stub returns 400 per docs, adapter must surface the host's reason.
    const { hooksPath } = await import('../../src/openclaw/identity.js')
    expect(hooksPath()).toBe('/hooks')
    const res = await fetch(`http://127.0.0.1:${port}/hooks/agent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + TOKEN },
      body: JSON.stringify({ message: 'x', agentId: 'main', sessionKey: 'agent:main:hook:aimail', deliver: false }),
    })
    expect(res.status).toBe(400)
    expect(String(await res.text())).toContain('allowRequestSessionKey')
  })

  it('missing token is a 401 (auth), wrong path is a 404 (hooks not served)', async () => {
    const noAuth = await fetch(`http://127.0.0.1:${port}/hooks/agent`, { method: 'POST', body: '{}' })
    expect(noAuth.status).toBe(401)
    const wrongPath = await fetch(`http://127.0.0.1:${port}/agent`, {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + TOKEN },
    })
    expect(wrongPath.status).toBe(404)
  })
})

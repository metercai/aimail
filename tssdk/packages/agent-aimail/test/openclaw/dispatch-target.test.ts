/**
 * L1 (pure local): **dispatch-target resolution** of the address-level (agent-scope) binding — anti-regression nail.
 *
 * Defect (0.1.25, E2-pull/openclaw actual run original text):
 *   The binding obtained from address code activation has `agent_id` as the **address local part** (binding file: agent_id="agenta"),
 *   the old implementation `inbound.ts:200 const agentId = cfg.agent_id || 'main'` treated it as the **host agentId**
 *   and sent it to `/hooks/agent` ⇒ host `400 {"error":"unknown agentId \"agenta\""}` ⇒ deliverInbound
 *   returned `ok:false` ⇒ `startInboundPull`'s onEmail threw ⇒ mail-core did not ack ⇒ next round re-pulled
 *   (`deliveries=1`), stub hits 0→0: **email received but never enters agent turn**.
 *
 * this test nails down locally the rule "an address-level binding must resolve to an agent the **host accepts**":
 *   ① Address local part **not in** host roster ⇒ falls back to host-declared agent (no longer 'agenta')
 *   ② Address local part **is in** host roster ⇒ use it (true address-level hit)
 *   ③ System-level/bridge binding agent_id **unchanged** (zero impact on push path)
 *   ④ Host config missing ⇒ return empty string (do not guess; caller falls back to resolveAgentId() || 'main'),
 *      and **never** passes the address local part as agentId
 *   ⑤ Integration: actually POST to a stub that only accepts host roster based on resolved target ⇒ 200 delivered
 *      (negative control: old value 'agenta' ⇒ 400, and host's reason is surfaced)
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createServer, type Server } from 'node:http'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import * as path from 'node:path'
import type { AgentConfig } from '../../src/core/index.js'
import { deliverToAgent, resolveDispatchAgentId } from '../../src/openclaw/inbound.js'

const TOKEN = 'stub-hook-token'
const ADDR = 'agenta@e2-a.local'
const HOST_AGENTS = ['main']
let home = ''
let server: Server
let port = 0
let seen: Array<Record<string, unknown>> = []

const ADDRESS_SCOPE: AgentConfig = {
  // Address code redemption artifact: system_id = shared_addr_*, agent_id = address local part
  system_id: 'shared_addr_addra',
  email: ADDR,
  agent_id: 'agenta',
  api_key: 'k',
  gateway_url: 'http://127.0.0.1:1',
} as AgentConfig

const SYSTEM_SCOPE: AgentConfig = {
  // Platform registration artifact: system_id has no shared_addr_ prefix, agent_id = host agent
  system_id: 'hosts123456',
  email: 'support@e2.local',
  agent_id: 'support',
  api_key: 'k',
  gateway_url: 'http://127.0.0.1:1',
} as AgentConfig

function writeHostConfig(agents: string[] | null): void {
  const p = path.join(home, '.openclaw', 'openclaw.json')
  if (agents === null) {
    rmSync(p, { force: true })
    return
  }
  writeFileSync(
    p,
    JSON.stringify({
      gateway: { mode: 'local', port },
      hooks: { enabled: true, token: TOKEN, path: '/hooks' },
      agents: { entries: Object.fromEntries(agents.map((a) => [a, {}])) },
    }),
  )
}

/** stub /hooks/agent — only recognizes HOST_AGENTS (same shape as host contract: directly giving an unconfigured agent ⇒ 400). */
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
        if (!HOST_AGENTS.includes(String(body.agentId ?? ''))) {
          return send(400, { ok: false, error: `unknown agentId "${String(body.agentId ?? '')}"` })
        }
        return send(200, { ok: true, runId: 'run-1' })
      })
    })
    server.listen(0, '127.0.0.1', () => resolve((server.address() as { port: number }).port))
  })
}

beforeEach(async () => {
  seen = []
  home = mkdtempSync(path.join(tmpdir(), 'oc-dispatch-target-'))
  process.env.HOME = home
  mkdirSync(path.join(home, '.openclaw'), { recursive: true })
  port = await startStub()
  writeHostConfig(HOST_AGENTS)
})
afterEach(() => {
  server.close()
  rmSync(home, { recursive: true, force: true })
})

describe('resolveDispatchAgentId — address-level bindings do not pass address local part to host (L1 anti-regression)', () => {
  it('① Address code activation binding + host roster does not have that local part ⇒ falls back to host-declared agent (never agent_id)', () => {
    const id = resolveDispatchAgentId(ADDRESS_SCOPE, ADDR)
    expect(id).toBe('main')
    expect(id).not.toBe('agenta')
  })

  it('② Address local part is indeed a host agent ⇒ use it (true address-level hit)', () => {
    writeHostConfig(['agenta', 'main'])
    expect(resolveDispatchAgentId(ADDRESS_SCOPE, ADDR)).toBe('agenta')
  })

  it('③ System-level/bridge binding: agent_id unchanged (zero impact on push path, unrelated to roster)', () => {
    expect(resolveDispatchAgentId(SYSTEM_SCOPE, SYSTEM_SCOPE.email as string)).toBe('support')
    writeHostConfig(['other'])
    expect(resolveDispatchAgentId(SYSTEM_SCOPE, SYSTEM_SCOPE.email as string)).toBe('support')
  })

  it('④ Host config missing ⇒ empty string (do not guess), and never passes address local part', () => {
    writeHostConfig(null)
    const id = resolveDispatchAgentId(ADDRESS_SCOPE, ADDR)
    expect(id).toBe('')
    expect(id).not.toBe('agenta')
  })

  it('⑤ Integration: resolved target accepted by host (delivered); old value address local part rejected (reason surfaced)', async () => {
    const good = resolveDispatchAgentId(ADDRESS_SCOPE, ADDR)
    const out = await deliverToAgent({} as never, { agentId: good, message: 'pulled mail' })
    expect(out.status).toBe('delivered')
    expect(seen).toHaveLength(1)
    expect(seen[0].agentId).toBe('main')

    // Negative control: the value handed over by the old implementation — host returns 400 per contract, and the adapter passes the reason through as-is (does not swallow it)
    const bad = await deliverToAgent({} as never, {
      agentId: String(ADDRESS_SCOPE.agent_id),
      message: 'pulled mail',
    })
    expect(bad.status).toBe('dispatch_failed')
    expect(bad.detail).toContain('unknown agentId')
  })
})

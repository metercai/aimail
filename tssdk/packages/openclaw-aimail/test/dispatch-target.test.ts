/**
 * L1（纯本地）: 地址级(agent-scope)绑定的**派发目标解析** —— 防退化钉子。
 *
 * 缺陷(0.1.25, E2-pull/openclaw 实跑原文):
 *   地址码激活得到的绑定里 `agent_id` 是**地址本地部分**（agentmail.json: agent_id="agenta"），
 *   旧实现 `inbound.ts:200 const agentId = cfg.agent_id || 'main'` 把它当**宿主 agentId**
 *   送给 `/hooks/agent` ⇒ 宿主 `400 {"error":"unknown agentId \"agenta\""}` ⇒ deliverInbound
 *   返 `ok:false` ⇒ `startInboundPull` 的 onEmail 抛 ⇒ mail-core 不 ack ⇒ 下轮重拉
 *   （`deliveries=1`）、stub 命中 0→0：**取到信却永远进不了 agent turn**。
 *
 * 本测试把"地址级绑定必须解析到**宿主承认的** agent"这条在本地钉住：
 *   ① 地址本地部分**不在**宿主名册 ⇒ 落到宿主声明的 agent（不再是 'agenta'）
 *   ② 地址本地部分**在**宿主名册 ⇒ 就用它（真地址级命中）
 *   ③ 系统级/桥绑定的 agent_id **一字不变**（push 路径零影响）
 *   ④ 宿主配置缺失 ⇒ 返空串（不猜；调用方兜底 resolveAgentId() || 'main'），
 *      且**绝不**把地址本地部分当 agentId 交出去
 *   ⑤ 集成：按解析出的目标真的 POST 一个只认宿主名册的 stub ⇒ 200 delivered
 *      （负对照：旧值 'agenta' ⇒ 400，且宿主的原因被带出来）
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createServer, type Server } from 'node:http'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import * as path from 'node:path'
import type { AgentConfig } from '@aimail/mail-core'
import { deliverToAgent, resolveDispatchAgentId } from '../src/inbound.js'

const TOKEN = 'stub-hook-token'
const ADDR = 'agenta@e2-a.local'
const HOST_AGENTS = ['main']
let home = ''
let server: Server
let port = 0
let seen: Array<Record<string, unknown>> = []

const ADDRESS_SCOPE: AgentConfig = {
  // 地址码兑换产物: system_id = shared_addr_*, agent_id = 地址本地部分
  system_id: 'shared_addr_addra',
  email: ADDR,
  agent_id: 'agenta',
  api_key: 'k',
  gateway_url: 'http://127.0.0.1:1',
} as AgentConfig

const SYSTEM_SCOPE: AgentConfig = {
  // 平台注册产物: system_id 无 shared_addr_ 前缀, agent_id = 宿主 agent
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

/** stub /hooks/agent —— 只认 HOST_AGENTS（与宿主合同同形: 直给未配置 agent ⇒ 400）。 */
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

describe('resolveDispatchAgentId — 地址级绑定不打地址本地部分给宿主 (L1 防退化)', () => {
  it('① 地址码激活绑定 + 宿主名册没有该本地部分 ⇒ 落到宿主声明的 agent(绝不是 agent_id)', () => {
    const id = resolveDispatchAgentId(ADDRESS_SCOPE, ADDR)
    expect(id).toBe('main')
    expect(id).not.toBe('agenta')
  })

  it('② 地址本地部分确实是宿主 agent ⇒ 用它(真地址级命中)', () => {
    writeHostConfig(['agenta', 'main'])
    expect(resolveDispatchAgentId(ADDRESS_SCOPE, ADDR)).toBe('agenta')
  })

  it('③ 系统级/桥绑定: agent_id 一字不变(push 路径零影响, 与名册无关)', () => {
    expect(resolveDispatchAgentId(SYSTEM_SCOPE, SYSTEM_SCOPE.email as string)).toBe('support')
    writeHostConfig(['other'])
    expect(resolveDispatchAgentId(SYSTEM_SCOPE, SYSTEM_SCOPE.email as string)).toBe('support')
  })

  it('④ 宿主配置缺失 ⇒ 空串(不猜), 且绝不交出地址本地部分', () => {
    writeHostConfig(null)
    const id = resolveDispatchAgentId(ADDRESS_SCOPE, ADDR)
    expect(id).toBe('')
    expect(id).not.toBe('agenta')
  })

  it('⑤ 集成: 解析出的目标被宿主接受(delivered); 旧值地址本地部分被拒(原因带出来)', async () => {
    const good = resolveDispatchAgentId(ADDRESS_SCOPE, ADDR)
    const out = await deliverToAgent({} as never, { agentId: good, message: 'pulled mail' })
    expect(out.status).toBe('delivered')
    expect(seen).toHaveLength(1)
    expect(seen[0].agentId).toBe('main')

    // 负对照: 旧实现交出去的值——宿主按合同 400, 且适配器把原因原样带出(不吞)
    const bad = await deliverToAgent({} as never, {
      agentId: String(ADDRESS_SCOPE.agent_id),
      message: 'pulled mail',
    })
    expect(bad.status).toBe('dispatch_failed')
    expect(bad.detail).toContain('unknown agentId')
  })
})

/**
 * openclaw-aimail unit tests (vitest):
 *  - toTypeBoxParam translation (neutral MailToolParam → TypeBox schema)
 *  - registerAgentEmail 4-step idempotent chain (MockClient)
 *  - deregisterAgentEmail 3-step idempotent chain (MockClient, P2 acceptance)
 */
import { describe, it, expect } from 'vitest'
import { MAIL_TOOLS, toTypeBoxParam, toTypeBoxParams, emailForAgent } from '@aimail/mail-core'
import { createMailTools } from '../src/tools.js'
import { deregisterAgentEmail, type AdminClient } from '../src/commands.js'

// ── MockClient (records requests, returns scripted responses) ──────────────

class MockClient implements AdminClient {
  calls: Array<{ method: string; path: string; body?: Record<string, unknown> }> = []
  responses: Array<Record<string, unknown>> = []

  request(
    method: string,
    path: string,
    body?: Record<string, unknown>,
  ): Promise<{ status: number; [k: string]: unknown }> {
    this.calls.push({ method, path, body })
    const next = this.responses.shift() ?? { status: 200 }
    return Promise.resolve(next as { status: number; [k: string]: unknown })
  }
}

// ── toTypeBoxParam ─────────────────────────────────────────────────────────

describe('toTypeBoxParam', () => {
  it('maps string params with required/enum/description', () => {
    const schema = toTypeBoxParam({
      type: 'string',
      enum: ['check', 'add', 'remove'],
      description: 'Action to perform',
      required: true,
    })
    const raw = JSON.parse(JSON.stringify(schema))
    // TypeBox Union serializes as anyOf (standard JSON Schema, validated by
    // typebox Value.Check at tool-param time)
    expect(raw.anyOf?.map((a: { const?: string }) => a.const)).toEqual([
      'check',
      'add',
      'remove',
    ])
    expect(raw.description).toBe('Action to perform')
  })

  it('maps array params to string arrays', () => {
    const schema = toTypeBoxParam({ type: 'array', items: { type: 'string' } })
    const raw = JSON.parse(JSON.stringify(schema))
    expect(raw.type).toBe('array')
    expect(raw.items?.type).toBe('string')
  })

  it('builds an object schema with additionalProperties false', () => {
    const schema = toTypeBoxParams(MAIL_TOOLS[0].parameters)
    const raw = JSON.parse(JSON.stringify(schema))
    expect(raw.type).toBe('object')
    expect(raw.additionalProperties).toBe(false)
    expect(raw.properties?.to).toBeDefined()
    expect(raw.properties?.subject).toBeDefined()
  })
})

// ── MAIL_TOOLS iteration (D7: single source, 15 bare names) ───────────────

describe('createMailTools', () => {
  it('registers exactly the 15 MAIL_TOOLS bare names', () => {
    const tools = createMailTools({} as never)
    expect(tools).toHaveLength(15)
    expect(tools.map(t => t.name)).toEqual(MAIL_TOOLS.map(t => t.name))
    for (const t of tools) {
      expect(t.name).not.toMatch(/^aimail__/)
    }
  })
})

// ── deregisterAgentEmail (3-step idempotent chain, P2 acceptance) ─────────

describe('deregisterAgentEmail', () => {
  it('deletes api-key → domain → whitelist by exact match + id (3 steps)', async () => {
    const client = new MockClient()
    client.responses.push(
      { status: 200, data: [{ id: 11, email: 'agent@test.example' }] }, // api-keys GET
      { status: 200 }, // DELETE api-key
      { status: 200, data: [{ id: 22, domain: 'agent@test.example' }] }, // domains GET
      { status: 200 }, // DELETE domain
      { status: 200, data: [ // whitelists GET (该系统全部行)
        { id: 33, domain_addr: 'agent@test.example', value: 'mgr@test.example' },
        { id: 44, domain_addr: 'other@test.example', value: 'mgr@test.example' },
      ] },
      { status: 204 }, // DELETE whitelist/33
    )
    const out = await deregisterAgentEmail(client, {
      systemId: 'system-test',
      email: 'agent@test.example',
      domainAddr: 'test.example',
      managerAddress: 'mgr@test.example',
    })
    expect(out.api_key).toBe('200')
    expect(out.domain).toBe('200')
    expect(out.whitelist).toBe('204')
    const delCalls = client.calls.filter(c => c.method === 'DELETE')
    expect(delCalls.map(c => c.path)).toEqual([
      '/api/v1/admin/api-keys/11',
      '/api/v1/admin/system-domains/22',
      // 精确匹配到本地址的行(33) —— 绝不误删同 manager 的 other@ 行(44)
      '/api/v1/whitelists/33',
    ])
  })

  it('never blind-deletes: rows exist but no exact match ⇒ not_found_exact, no DELETE', async () => {
    const client = new MockClient()
    client.responses.push(
      { status: 200, data: [] }, // api-keys
      { status: 200, data: [] }, // domains
      { status: 200, data: [{ id: 44, domain_addr: 'other@test.example', value: 'mgr@test.example' }] },
    )
    const out = await deregisterAgentEmail(client, {
      systemId: 'system-test',
      email: 'ghost@test.example',
      domainAddr: 'test.example',
      managerAddress: 'mgr@test.example',
    })
    expect(out.whitelist).toBe('not_found_exact')
    expect(client.calls.filter(c => c.method === 'DELETE' && c.path.startsWith('/api/v1/whitelists')).length).toBe(0)
  })

  it('reports skipped when no manager is known (不猜不盲删)', async () => {
    const client = new MockClient()
    client.responses.push(
      { status: 200, data: [] },
      { status: 200, data: [] },
    )
    const out = await deregisterAgentEmail(client, {
      systemId: 'system-test',
      email: 'agent@test.example',
      domainAddr: 'test.example',
    })
    expect(out.whitelist).toBe('skipped')
    expect(client.calls.filter(c => c.method === 'DELETE' && c.path.startsWith('/api/v1/whitelists')).length).toBe(0)
  })

  it('is idempotent when nothing is found (not_found on each step)', async () => {
    const client = new MockClient()
    client.responses.push(
      { status: 200, data: [] }, // no api keys
      { status: 200, data: [] }, // no domains
      { status: 200, data: [] }, // no whitelist rows
    )
    const out = await deregisterAgentEmail(client, {
      systemId: 'system-test',
      email: 'ghost@test.example',
      domainAddr: 'test.example',
      managerAddress: 'mgr@test.example',
    })
    expect(out.api_key).toBe('not_found')
    expect(out.domain).toBe('not_found')
    expect(out.whitelist).toBe('not_found')
  })
})

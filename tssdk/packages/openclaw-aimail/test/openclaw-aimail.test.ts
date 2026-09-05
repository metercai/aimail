/**
 * openclaw-aimail unit tests (vitest):
 *  - toTypeBoxParam translation (neutral MailToolParam → TypeBox schema)
 *  - registerAgentEmail 4-step idempotent chain (MockClient)
 *  - deregisterAgentEmail 3-step idempotent chain (MockClient, P2 acceptance)
 */
import { describe, it, expect } from 'vitest'
import { MAIL_TOOLS, toTypeBoxParam, toTypeBoxParams } from '@aimail/mail-core'
import { createMailTools } from '../src/tools.js'
import { registerAgentEmail, deregisterAgentEmail, type AdminClient } from '../src/commands.js'

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

// ── MAIL_TOOLS iteration (D7: single source, 12 bare names) ───────────────

describe('createMailTools', () => {
  it('registers exactly the 13 MAIL_TOOLS bare names', () => {
    const tools = createMailTools({} as never)
    expect(tools).toHaveLength(13)
    expect(tools.map(t => t.name)).toEqual(MAIL_TOOLS.map(t => t.name))
    for (const t of tools) {
      expect(t.name).not.toMatch(/^amail__/)
    }
  })
})

// ── registerAgentEmail (4-step idempotent chain) ──────────────────────────

describe('registerAgentEmail', () => {
  const opts = {
    systemId: 'system-test',
    email: 'agent@test.example',
    webhookUrl: 'http://127.0.0.1:18789/aimail/inbound',
    webhookSecret: 's3cret',
  }

  it('registers and activates → api_key', async () => {
    const client = new MockClient()
    client.responses.push(
      { status: 201, activation_code: 'code-abc' },
      { status: 200, success: true, raw_key: 'sk-test' },
    )
    const result = await registerAgentEmail(client, opts)
    expect(result.api_key).toBe('sk-test')
    expect(client.calls[0].method).toBe('POST')
    expect(client.calls[0].path).toContain('/api/v1/admin/systems/system-test/addresses')
    expect(client.calls[0].path).toContain('generate_code=true')
    expect(client.calls[1].path).toBe('/api/v1/activate-address')
  })

  it('is idempotent when the address already exists (updates webhook)', async () => {
    const client = new MockClient()
    client.responses.push(
      { status: 409, error: 'address already exists' },
      { status: 200, data: [{ id: 7, domain: 'agent@test.example' }] },
      { status: 200 },
    )
    const result = await registerAgentEmail(client, opts)
    expect(result.exists).toBe(true)
    expect(result.api_key).toBeUndefined()
    const put = client.calls.find(c => c.method === 'PUT')
    expect(put?.path).toBe('/api/v1/admin/system-domains/7')
    expect(put?.body?.webhook_url).toBe(opts.webhookUrl)
  })
})

// ── deregisterAgentEmail (3-step idempotent chain, P2 acceptance) ─────────

describe('deregisterAgentEmail', () => {
  it('deletes api-key → domain → whitelist (3 steps)', async () => {
    const client = new MockClient()
    client.responses.push(
      { status: 200, data: [{ id: 11, email: 'agent@test.example' }] }, // api-keys
      { status: 200 }, // DELETE api-key
      { status: 200, data: [{ id: 22, domain: 'agent@test.example' }] }, // domains
      { status: 200 }, // DELETE domain
      { status: 200 }, // DELETE whitelist
    )
    const out = await deregisterAgentEmail(client, {
      systemId: 'system-test',
      email: 'agent@test.example',
      domainAddr: 'test.example',
    })
    expect(out.api_key).toBe('200')
    expect(out.domain).toBe('200')
    expect(out.whitelist).toBe('200')
    const delCalls = client.calls.filter(c => c.method === 'DELETE')
    expect(delCalls.map(c => c.path)).toEqual([
      '/api/v1/admin/api-keys/11',
      '/api/v1/admin/system-domains/22',
      '/api/v1/whitelists?domain_addr=test.example&value=agent%40test.example',
    ])
  })

  it('is idempotent when nothing is found (not_found on each step)', async () => {
    const client = new MockClient()
    client.responses.push(
      { status: 200, data: [] }, // no api keys
      { status: 200, data: [] }, // no domains
      { status: 200 }, // whitelist delete (idempotent)
    )
    const out = await deregisterAgentEmail(client, {
      systemId: 'system-test',
      email: 'ghost@test.example',
      domainAddr: 'test.example',
    })
    expect(out.api_key).toBe('not_found')
    expect(out.domain).toBe('not_found')
    expect(out.whitelist).toBe('200')
  })
})

/**
 * gateway-injected-fetch.test.ts — the transport seam a plugin host needs.
 *
 * A PI-Desktop plugin has no global `fetch`: egress goes through the host's
 * broker (`pi.net.fetch`), which answers one whole response and cannot stream a
 * body. These cases pin the two things that follow:
 *
 *   1. `request` shapes the call exactly as before (signed headers, JSON body,
 *      status never overwritten) while the transport is the caller's;
 *   2. a client on an injected transport reports "no stream" instead of probing,
 *      so the pull state machine falls to long-poll instead of opening an SSE
 *      read it cannot finish.
 */
import { describe, expect, it, vi } from 'vitest'

import { GatewayClient, type GatewayFetchImpl } from '../../src/core/gateway.js'

function reply(status: number, text: string): Awaited<ReturnType<GatewayFetchImpl>> {
  return { status, text: async () => text }
}

describe('GatewayClient with an injected transport', () => {
  it('sends the signed request through the injected transport', async () => {
    const fetchImpl = vi.fn(async () => reply(200, JSON.stringify({ status: 'ok', items: [] })))
    const client = new GatewayClient('https://gw.example/', 'k-1', 30_000, 'sys-1', {
      fetchImpl,
      pendingPath: '/api/v1/agent-system/pending',
      extraHeaders: { 'X-Agent-System-Id': 'as-1' },
    })

    const res = await client.request('POST', '/api/v1/agent-system/pending/ack', { ids: [1] })

    expect(res).toEqual({ status: 200, items: [] })
    const [url, init] = fetchImpl.mock.calls[0]!
    expect(url).toBe('https://gw.example/api/v1/agent-system/pending/ack')
    expect(init.method).toBe('POST')
    expect(new TextDecoder().decode(init.body)).toBe('{"ids":[1]}')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(init.headers['X-Agent-System-Id']).toBe('as-1')
    expect(init.headers['X-Api-Identity']).toBe('sys-1')
    expect(init.headers['X-Api-Signature']).toBeTruthy()
  })

  it('never invents a status: a 401 or a non-JSON body comes back as-is', async () => {
    const unauthorized = new GatewayClient('https://gw.example', 'k', 30_000, '', {
      fetchImpl: async () => reply(401, JSON.stringify({ error: 'bad key' })),
    })
    expect(await unauthorized.request('GET', '/api/v1/admin/pending')).toEqual({
      status: 401,
      error: 'bad key',
    })

    const html = new GatewayClient('https://gw.example', 'k', 30_000, '', {
      fetchImpl: async () => reply(502, '<html>bad gateway</html>'),
    })
    expect(await html.request('GET', '/x')).toEqual({ status: 502, body: '<html>bad gateway</html>' })
  })

  it('turns a thrown transport error into status 0, not an exception', async () => {
    const client = new GatewayClient('https://gw.example', 'k', 30_000, '', {
      fetchImpl: async () => {
        throw new Error('broker refused: net.domains')
      },
    })
    expect(await client.request('GET', '/api/v1/admin/pending')).toEqual({
      status: 0,
      error: 'broker refused: net.domains',
    })
  })

  it('reports "no stream" instead of probing a transport that cannot stream', async () => {
    const fetchImpl = vi.fn(async () => reply(200, ''))
    const injected = new GatewayClient('https://gw.example', 'k', 30_000, '', { fetchImpl })
    expect(injected.streamCapable).toBe(false)
    expect(await injected.streamProbe()).toBe(false)
    expect(fetchImpl).not.toHaveBeenCalled()

    // The default client keeps the streaming path (probe is still its own call).
    expect(new GatewayClient('https://gw.example', 'k').streamCapable).toBe(true)
  })
})

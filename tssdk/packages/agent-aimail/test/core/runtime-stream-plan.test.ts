import { describe, expect, it } from 'vitest'
import { planStreams } from '../../src/runtime/stream/plan.js'

describe('shared SSE planning', () => {
  it('uses one system stream for all addresses on that system', () => {
    const plans = planStreams(
      [{ systemId: 's1', gatewayUrl: 'http://gw', apiKey: 'K' }],
      [
        { email: 'a@x', gatewayUrl: 'http://gw', apiKey: 'a' },
        { email: 'b@x', gatewayUrl: 'http://gw', apiKey: 'b' },
        { email: 'off@x', gatewayUrl: 'http://gw', apiKey: 'c', paused: true },
      ],
    )
    expect(plans).toHaveLength(1)
    expect(plans[0]).toMatchObject({ scope: 'system', identity: 's1', emails: ['a@x', 'b@x'] })
  })

  it('does not mix addresses from different systems on the same gateway', () => {
    const plans = planStreams(
      [
        { systemId: 's1', gatewayUrl: 'http://gw', apiKey: 'K1' },
        { systemId: 's2', gatewayUrl: 'http://gw', apiKey: 'K2' },
      ],
      [
        { email: 'a@x', gatewayUrl: 'http://gw', apiKey: 'a', system_id: 's1' },
        { email: 'b@x', gatewayUrl: 'http://gw', apiKey: 'b', system_id: 's2' },
      ],
    )
    expect(plans.map(p => p.emails).sort()).toEqual([['a@x'], ['b@x']].sort())
  })

  it('falls back to per-address streams when no system key exists', () => {
    const plans = planStreams([], [
      { email: 'a@x', gatewayUrl: 'http://gw', apiKey: 'a' },
      { email: 'b@x', gatewayUrl: 'http://gw', apiKey: 'b' },
    ])
    expect(plans.map(p => p.identity)).toEqual(['a@x', 'b@x'])
    expect(plans.every(p => p.scope === 'address')).toBe(true)
  })
})

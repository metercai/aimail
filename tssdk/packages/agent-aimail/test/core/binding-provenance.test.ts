/**
 * Binding provenance — the anti-forgery judgement (see src/core/binding-provenance.ts).
 *
 * What this file is really about: a binding file is a CACHE of a capability, not the capability
 * itself. The capability is the agent-scope key the gateway issued when an activation code was
 * consumed, and every pull is authenticated with it — so a hand-written `agentmail.json` buys
 * nothing: without a real key the gateway answers 401, and with a real key for that address it is
 * exactly what an activation would have granted (the gateway resolves the address from the key, so
 * a forged `email` field fetches nothing).
 *
 * What was missing is the *diagnostic* half: a half-invented file (no AgentSystemId, no expiry, no
 * proof it ever went through activation) armed silently. The activation path now stamps
 * `binding_source` + `activated_at`, and this suite pins both the stamp and the judgement. The
 * judgement is unconditional — there is no switch and no "compat" mode (nothing has shipped yet, so
 * an unstamped binding is hand-written or a writer bug, never a legacy artefact).
 */
import { describe, expect, test } from 'vitest'

import {
  BINDING_SOURCE_ACTIVATION,
  bindingProvenance,
  isRefusedByGateway,
  refuseBindingAfterGatewayRejection,
  resetGatewayRejections,
} from '../../src/core/binding-provenance.js'

const ACTIVATED = {
  email: 'agent@example.com',
  gateway_url: 'https://gw.test',
  domain: 'example.com',
  system_id: 'sys-1',
  agent_system_id: 'asid_abc',
  api_key: 'k'.repeat(64),
  agent_id: 'agent',
  binding_source: BINDING_SOURCE_ACTIVATION,
  activated_at: '2026-11-25T10:00:00.000Z',
}

describe('binding provenance', () => {
  test('a binding minted by the activation chain is proven', () => {
    expect(bindingProvenance(ACTIVATED)).toEqual({ ok: true, reason: '' })
  })

  test('no stamp ⇒ not proven (the hand-written case)', () => {
    const { binding_source: _drop, ...handWritten } = ACTIVATED
    const v = bindingProvenance(handWritten)
    expect(v.ok).toBe(false)
    expect(v.reason).toMatch(/no activation stamp/)
  })

  test('a forged/unknown stamp is refused, not trusted', () => {
    const v = bindingProvenance({ ...ACTIVATED, binding_source: 'register-address' })
    expect(v.ok).toBe(false)
    expect(v.reason).toMatch(/unknown binding_source/)
  })

  test('a stamp without a timestamp does not count as proof', () => {
    expect(bindingProvenance({ ...ACTIVATED, activated_at: '' }).ok).toBe(false)
    expect(bindingProvenance({ ...ACTIVATED, activated_at: 'not-a-date' }).ok).toBe(false)
  })

  test('the seven §7.1 fields are required, so a stub cannot pass by adding a stamp', () => {
    for (const field of ['email', 'gateway_url', 'domain', 'system_id', 'agent_system_id', 'api_key', 'agent_id']) {
      const partial: Record<string, unknown> = { ...ACTIVATED }
      delete partial[field]
      const v = bindingProvenance(partial)
      expect(v.ok, `${field} must be required`).toBe(false)
      expect(v.reason).toContain(field)
    }
  })

  test('an absent binding is "not proven" rather than a crash', () => {
    expect(bindingProvenance(undefined).ok).toBe(false)
    expect(bindingProvenance(null).ok).toBe(false)
  })

  test('there is no escape hatch: an env variable cannot re-enable an unstamped binding', () => {
    // Guards the removal itself — a future "compat switch" would have to delete this test.
    process.env.AIMAIL_STRICT_BINDING = '0'
    const { binding_source: _drop, ...handWritten } = ACTIVATED
    try {
      expect(bindingProvenance(handWritten).ok).toBe(false)
    } finally {
      delete process.env.AIMAIL_STRICT_BINDING
    }
  })
})

describe('gateway verdict (the authority) is reflected into arming', () => {
  test('a key the gateway rejected stops being armable', () => {
    const email = 'agent@example.com'
    expect(isRefusedByGateway(email)).toBe(false)
    refuseBindingAfterGatewayRejection(email)
    expect(isRefusedByGateway(email.toUpperCase())).toBe(true) // case-insensitive
    resetGatewayRejections()
    expect(isRefusedByGateway(email)).toBe(false)
  })

  test('one rejected address does not taint another', () => {
    refuseBindingAfterGatewayRejection('other@example.com')
    expect(isRefusedByGateway('agent@example.com')).toBe(false)
    resetGatewayRejections()
  })
})

/**
 * Host-neutral thread helpers (session-router).
 *
 * `routeThread()` used to be tested here. It was deleted on 2026-11-25: dsh's shipped hub used it
 * while the fixtures exercised `decideInboundDelivery()`, i.e. the tested router and the shipped one
 * differed (no E1, no anchor record, no dispatch line). The routing decision now has one
 * implementation — `runtime/routing.ts` — whose own suite (`test/core/runtime-routing.test.ts`)
 * covers E1/E2/E3, C16 and C17.
 */
import { describe, expect, it } from 'vitest'
import { normalizeMessageId, threadRoot } from '../../src/runtime/session-router.js'

describe('host-neutral thread helpers', () => {
  it('uses references[0], then in_reply_to, then message_id', () => {
    expect(threadRoot({ message_id: 'm', in_reply_to: 'r', references: ['ref', 'r'] })).toBe('ref')
    expect(threadRoot({ message_id: 'm', in_reply_to: 'r' })).toBe('r')
    expect(threadRoot({ message_id: 'm' })).toBe('m')
    expect(threadRoot({}, { 'in-reply-to': 'h' })).toBe('h')
  })

  it('compares message ids bare (angle brackets are transport, not identity)', () => {
    expect(normalizeMessageId('<abc@x>')).toBe('abc@x')
    expect(normalizeMessageId('  <abc@x> ')).toBe('abc@x')
    expect(normalizeMessageId(undefined)).toBe('')
  })
})

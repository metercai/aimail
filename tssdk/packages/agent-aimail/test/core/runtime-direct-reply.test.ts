import { describe, expect, it } from 'vitest'
import {
  directReplySession,
  normalizeMessageId,
  parentMessageId,
  referencesList,
} from '../../src/runtime/session-router.js'

/**
 * Contract §9 — the ONE hard routing rule:
 * a reply whose immediate parent is an outbound mail WE sent goes back to the
 * session that sent it. Everything else is thread-level.
 *
 * These cases exist because the naive implementations fail exactly here:
 *   - using references[0] instead of In-Reply-To (root vs parent)
 *   - falling back to the reference chain when In-Reply-To is present but unknown
 */
describe('parent message resolution', () => {
  it('prefers In-Reply-To (the immediate parent)', () => {
    expect(parentMessageId({ in_reply_to: '<parent@x>' }, {})).toBe('parent@x')
    expect(parentMessageId({}, { 'in-reply-to': '<h@x>' })).toBe('h@x')
  })

  it('falls back to the LAST reference, never the first', () => {
    expect(parentMessageId({ references: ['root@x', 'mid@x', 'parent@x'] })).toBe('parent@x')
    expect(parentMessageId({}, { references: '<root@x> <parent@x>' })).toBe('parent@x')
    expect(parentMessageId({ references: ['only@x'] })).toBe('only@x')
  })

  it('normalizes brackets and whitespace, and ignores empties', () => {
    expect(normalizeMessageId('  <a@x>  ')).toBe('a@x')
    expect(referencesList({ references: '  <a@x>   <b@x>  ' })).toEqual(['a@x', 'b@x'])
    expect(referencesList({ references: ['<>', '  ', '<c@x>'] })).toEqual(['c@x'])
  })

  it('returns empty when the mail carries no threading information', () => {
    expect(parentMessageId({})).toBe('')
    expect(parentMessageId({}, {})).toBe('')
  })
})

describe('direct reply → the session that sent the parent', () => {
  const ours: Record<string, string> = { 'out-1@aimail': 'session-1', 'out-2@aimail': 'session-2' }
  const lookup = (id: string) => ours[id]

  it('routes a reply to our outbound mail back to its session (In-Reply-To)', () => {
    expect(
      directReplySession({ in_reply_to: '<out-1@aimail>', references: ['<a@ext>', '<out-1@aimail>'] }, {}, lookup),
    ).toEqual({ sessionId: 'session-1', parent: 'out-1@aimail' })
  })

  it('uses the last reference only when In-Reply-To is absent', () => {
    expect(directReplySession({ references: ['<a@ext>', '<out-2@aimail>'] }, {}, lookup)).toEqual({
      sessionId: 'session-2',
      parent: 'out-2@aimail',
    })
  })

  it('does NOT treat the thread root as a direct reply', () => {
    // We started this thread (root is ours) but the mail answers one of theirs.
    expect(
      directReplySession(
        { in_reply_to: '<their-mid@ext>', references: ['<out-1@aimail>', '<their-mid@ext>'] },
        {},
        lookup,
      ),
    ).toBeNull()
  })

  it('does NOT fall back to references when In-Reply-To points elsewhere', () => {
    // Even though the chain contains our mail, the parent is not ours ⇒ thread-level.
    expect(
      directReplySession(
        { in_reply_to: '<unknown@ext>', references: ['<out-1@aimail>', '<unknown@ext>'] },
        {},
        lookup,
      ),
    ).toBeNull()
  })

  it('is not a direct reply for a brand-new mail', () => {
    expect(directReplySession({ message_id: '<fresh@ext>' }, {}, lookup)).toBeNull()
  })

  it('is not a direct reply when the lookup misses', () => {
    expect(directReplySession({ in_reply_to: '<nobody@ext>' }, {}, lookup)).toBeNull()
  })

  it('ignores an empty session id from the index', () => {
    expect(directReplySession({ in_reply_to: '<out-1@aimail>' }, {}, () => '')).toBeNull()
  })
})

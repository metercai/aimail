/**
 * §9 edge selection on OpenClaw — which session an inbound mail belongs to.
 *
 * The bug this pins: inbound was keyed ONLY by a thread-root hash, so a reply to a mail the
 * agent had sent from a **user conversation** landed in a hook session instead of the one that
 * wrote it — the one hard routing rule (contract §9.2 I1) was unmet while C8's same-thread case
 * still looked green. `send_mail` now stamps the sending session's routable handle
 * (`session_key`) onto the message's local meta; this decision reads it back.
 */
import { describe, expect, it } from 'vitest'

import { dispatchIdempotencyKey, effectiveDispatchAgentId, selectSessionKey } from '../../src/openclaw/inbound.js'

const POLICY = { allowSessionKey: true, prefixes: ['agent:', 'hook:'] }

const reply = {
  message_id: '<reply@remote>',
  in_reply_to: '<ours@local>',
  references: ['<root@remote>', '<ours@local>'],
}

describe('selectSessionKey — E1 (direct reply) before E2 (thread)', () => {
  it('answers in the session that sent the mail it replies to', () => {
    const out = selectSessionKey({
      agentId: 'main',
      payload: reply,
      headers: {},
      policy: POLICY,
      parentMeta: { session_key: 'agent:main:main' },
    })
    expect(out).toEqual({ sessionKey: 'agent:main:main', source: 'direct-reply' })
  })

  it('accepts In-Reply-To, and the LAST reference when it is absent (never references[0])', () => {
    const viaRefs = selectSessionKey({
      agentId: 'main',
      payload: { ...reply, in_reply_to: undefined },
      headers: {},
      policy: POLICY,
      parentMeta: { session_key: 'agent:main:chat-7' },
    })
    expect(viaRefs).toEqual({ sessionKey: 'agent:main:chat-7', source: 'direct-reply' })

    // references[0] is the thread root; answering "the root" would be a thread-level guess.
    const rootOnly = selectSessionKey({
      agentId: 'main',
      payload: { message_id: '<reply@remote>', references: ['<ours@local>'] },
      headers: {},
      policy: POLICY,
      parentMeta: { session_key: 'agent:main:chat-7' },
    })
    expect(rootOnly).toEqual({ sessionKey: 'agent:main:chat-7', source: 'direct-reply' })
  })

  it('falls back to the deterministic thread key when the parent was not ours', () => {
    const out = selectSessionKey({
      agentId: 'main',
      payload: reply,
      headers: {},
      policy: POLICY,
      // meta absent: this binding never sent <ours@local>
    })
    expect(out.source).toBe('thread')
    expect(out.sessionKey).toMatch(/^agent:main:aimail:[0-9a-f]{24}$/)
    expect(out.dropped).toBeUndefined()
  })

  it('keeps the thread key stable for every mail of one thread (E2)', () => {
    const a = selectSessionKey({ agentId: 'main', payload: reply, headers: {}, policy: POLICY })
    const b = selectSessionKey({
      agentId: 'main',
      payload: { message_id: '<later@remote>', references: ['<root@remote>'] },
      headers: {},
      policy: POLICY,
    })
    expect(a.sessionKey).toBe(b.sessionKey)
  })

  it('does not force a recorded key the host would reject — degrades to thread and says so', () => {
    const out = selectSessionKey({
      agentId: 'main',
      payload: reply,
      headers: {},
      policy: { allowSessionKey: true, prefixes: ['agent:aimail:'] },
      parentMeta: { session_key: 'agent:main:main' },
    })
    expect(out.source).toBe('thread')
    expect(out.dropped).toBe('sessionKey-not-in-allowed-prefixes')
  })

  it('with no prefix allowlist configured, any recorded key is accepted', () => {
    const out = selectSessionKey({
      agentId: 'main',
      payload: reply,
      headers: {},
      policy: { allowSessionKey: true, prefixes: [] },
      parentMeta: { session_key: 'custom:whatever' },
    })
    expect(out).toEqual({ sessionKey: 'custom:whatever', source: 'direct-reply' })
  })
})

describe('effectiveDispatchAgentId (P1-1)', () => {
  it('never lets an empty dispatch id reach the session key', () => {
    // The bug: `resolveDispatchAgentId` returns '' for a host config without agents, so the key
    // became `agent::aimail:<hash>` while the dispatch body fell back to `main` — two different
    // agents in one request.
    expect(effectiveDispatchAgentId('')).not.toBe('')
    expect(effectiveDispatchAgentId(undefined)).not.toBe('')
    expect(effectiveDispatchAgentId('   ')).not.toBe('')
    expect(effectiveDispatchAgentId('main')).toBe('main')
    expect(effectiveDispatchAgentId(' agenta ')).toBe('agenta')
  })

  it('produces a session key whose agent segment matches the dispatch agent', () => {
    const key = selectSessionKey({
      agentId: effectiveDispatchAgentId(''),
      payload: { message_id: '<m@x>' },
      headers: {},
      policy: { allowSessionKey: true, prefixes: ['agent:'] },
    })
    expect(key.sessionKey).toMatch(/^agent:[a-z0-9_-]+:aimail:[0-9a-f]{24}$/)
    expect(key.sessionKey.startsWith('agent::')).toBe(false)
  })
})

describe('dispatchIdempotencyKey — replay must not outlive its target', () => {
  it('the same mail to the same session is one key (a retry is deduped)', () => {
    expect(dispatchIdempotencyKey('<m@x>', 'agent:main:aimail:abc')).toBe(
      dispatchIdempotencyKey('<m@x>', 'agent:main:aimail:abc'),
    )
  })

  it('the same mail to a DIFFERENT session is a different key (so the host re-runs it)', () => {
    // Without this, deleting the session and re-routing would hit openclaw's replay cache and get
    // a 200 with no run — "acked, zero landed" (C17).
    expect(dispatchIdempotencyKey('<m@x>', 'agent:main:aimail:abc')).not.toBe(
      dispatchIdempotencyKey('<m@x>', 'agent:main:aimail:def'),
    )
    expect(dispatchIdempotencyKey('<m@x>', 'agent:main:aimail:abc')).not.toBe(
      dispatchIdempotencyKey('<m@x>', undefined),
    )
  })

  it('carries the target and stays inside the host bound', () => {
    const key = dispatchIdempotencyKey('<m@x>', 'agent:main:aimail:abc')
    expect(key).toContain('session:agent:main:aimail:abc')
    expect(key.length).toBeLessThanOrEqual(128)
    expect(dispatchIdempotencyKey('   ', 'agent:x')).toBe('')
  })
})


/**
 * v1 API signature — 3-language parity test.
 *
 * The canonical vector is pinned in aimail-gateway
 * docs/API-SIGNATURE-PROTOCOL.md and in the Rust unit test
 * `api_signature_matches_canonical_vector`. TS / Python / Rust MUST all
 * produce the same value for the same inputs — this test guards the TS side.
 */
import { describe, it, expect } from 'vitest'
import { computeApiSignature, sha256Hex } from '../src/api-signature.js'

const RAW_KEY = '0123456789abcdef0123456789abcdef'
const TS = '1756000000000'

describe('computeApiSignature (canonical vector)', () => {
  it('sha256(raw_key) == DB key_hash', () => {
    expect(sha256Hex(RAW_KEY)).toBe(
      '3eb1bd439947eb762998e566ccc2e099c791118b2f40579cc4f7da2b5061b7f9',
    )
  })

  it('POST + body matches the canonical vector', () => {
    const path = '/api/v1/whitelists?domain=alice%40x.com&value=%40mx-a.test'
    const body = new TextEncoder().encode('{"direction":"to"}')
    const sig = computeApiSignature(RAW_KEY, 'POST', path, body, Number(TS))
    expect(sig).not.toBeNull()
    expect(sig!.timestamp).toBe(TS)
    expect(sig!.signature).toBe(
      'cabf840e1d1a8dd9d6885762beae087f422dbd4d6d20c9ca404896120a45bcbd',
    )
  })

  it('GET + empty body matches the canonical vector', () => {
    const sig = computeApiSignature(RAW_KEY, 'GET', '/api/v1/whoami', new Uint8Array(0), Number(TS))
    expect(sig!.signature).toBe(
      '1aac75c79bea9c60efb3280a384900ce649c346c3da5cc124361fc5070e55c74',
    )
  })

  it('tampering any field changes the signature', () => {
    const path = '/api/v1/whitelists?domain=alice%40x.com'
    const body = new TextEncoder().encode('{"direction":"to"}')
    const base = computeApiSignature(RAW_KEY, 'POST', path, body, Number(TS))!
    const b2 = new TextEncoder().encode('{"direction":"from"}')
    expect(computeApiSignature(RAW_KEY, 'POST', path, b2, Number(TS))!.signature).not.toBe(base.signature)
    expect(computeApiSignature(RAW_KEY, 'POST', path, body, Number(TS) + 1)!.signature).not.toBe(base.signature)
    expect(computeApiSignature(RAW_KEY, 'GET', path, body, Number(TS))!.signature).not.toBe(base.signature)
  })

  it('method is case-insensitive (upper-cased in base)', () => {
    const path = '/api/v1/whoami'
    const a = computeApiSignature(RAW_KEY, 'get', path, new Uint8Array(0), Number(TS))!
    const b = computeApiSignature(RAW_KEY, 'GET', path, new Uint8Array(0), Number(TS))!
    expect(a.signature).toBe(b.signature)
  })

  it('empty api key -> null', () => {
    expect(computeApiSignature('', 'GET', '/x', new Uint8Array(0), Number(TS))).toBeNull()
  })
})

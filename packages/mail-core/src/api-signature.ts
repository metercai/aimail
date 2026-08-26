/**
 * v1 API signature — canonical TS implementation (single source of truth).
 * Contract: aimail-gateway docs/API-SIGNATURE-PROTOCOL.md
 *
 * Mirrors Python `aimail_base.compute_api_signature` and Rust
 * `compute_api_signature` — all three MUST produce identical output for the
 * same inputs (see the canonical vector in that doc and the tests).
 *
 * The raw API key never crosses the wire: the HMAC key is
 * `sha256(rawKey)` (== the DB `api_keys.key_hash`), derived offline here.
 * The caller separately adds `X-Api-Identity` (the key's email for
 * address-scoped keys, or its `system_id` for system-level keys).
 */
import { createHash, createHmac } from 'node:crypto'

export function sha256Hex(input: string | Uint8Array): string {
  return createHash('sha256').update(input).digest('hex')
}

/**
 * Compute the v1 API signature headers.
 *
 * `path` MUST be the exact request target (path + query, URL-encoded) sent on
 * the wire, so the server's `path_and_query()` re-computes the identical base
 * string.
 *
 * @param apiKey   raw API key (kept offline)
 * @param method   HTTP method (case-insensitive; upper-cased in the base)
 * @param path     request target incl. query string
 * @param body     request body bytes (empty for GET / no body)
 * @param timestampMs  fixed timestamp for tests; defaults to now
 * @returns `{ timestamp, signature }` or `null` when `apiKey` is empty.
 */
export function computeApiSignature(
  apiKey: string,
  method: string,
  path: string,
  body: Uint8Array | string = new Uint8Array(0),
  timestampMs?: number,
): { timestamp: string; signature: string } | null {
  if (!apiKey) return null
  const keyHash = sha256Hex(apiKey)
  const timestamp = String(timestampMs ?? Date.now())
  const bodyBytes: Uint8Array =
    typeof body === 'string' ? new TextEncoder().encode(body) : body
  const bodyHash = sha256Hex(bodyBytes)
  const base = `${method.toUpperCase()}\n${path}\n${timestamp}\n${bodyHash}`
  const signature = createHmac('sha256', keyHash).update(base).digest('hex')
  return { timestamp, signature }
}

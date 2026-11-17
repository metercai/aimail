/**
 * Pure SSE stream planning for shared-gateway routing.
 *
 * One stream per (gateway_url, credential scope): all addresses of a system
 * share its system stream. Multiple systems on one gateway still need one
 * stream per system key because a system key cannot observe another system.
 * Address-only (B2) deployments fall back to one address stream each.
 */
export interface StreamSystem {
  systemId: string
  gatewayUrl: string
  apiKey: string
}

export interface StreamBinding {
  email: string
  gatewayUrl: string
  apiKey: string
  /** Owning system; absent only for legacy callers. */
  system_id?: string
  paused?: boolean
}

export interface StreamPlan {
  scope: 'system' | 'address'
  identity: string
  gatewayUrl: string
  apiKey: string
  emails: string[]
}

export function planStreams(
  systems: StreamSystem[],
  bindings: StreamBinding[],
): StreamPlan[] {
  const bySystem = new Map<string, StreamPlan>()
  for (const s of systems) {
    if (!s.gatewayUrl || !s.apiKey || !s.systemId) continue
    const key = `${s.gatewayUrl}\n${s.systemId}`
    if (bySystem.has(key)) continue
    const emails = bindings
      .filter((b) => !b.paused && b.gatewayUrl === s.gatewayUrl
        && (!b.system_id || b.system_id === s.systemId))
      .map((b) => b.email)
    bySystem.set(key, {
      scope: 'system', identity: s.systemId, gatewayUrl: s.gatewayUrl,
      apiKey: s.apiKey, emails,
    })
  }
  if (bySystem.size > 0) return [...bySystem.values()]
  // No system key: no key covers multiple addresses; keep one stream per
  // actionable address (still one router, never per workspace/session).
  const byAddr = new Map<string, StreamPlan>()
  for (const b of bindings) {
    if (b.paused || !b.gatewayUrl || !b.apiKey || !b.email) continue
    const key = `${b.gatewayUrl}\n${b.email}`
    if (byAddr.has(key)) continue
    byAddr.set(key, {
      scope: 'address', identity: b.email, gatewayUrl: b.gatewayUrl,
      apiKey: b.apiKey, emails: [b.email],
    })
  }
  return [...byAddr.values()]
}
